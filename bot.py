import os
import re
import json
import zipfile
import asyncio
import time
import requests
from twitchio.ext import commands

# --- CONFIG LOADING ---
CONFIG_FILE = "config.json"
if not os.path.exists(CONFIG_FILE):
    print("❌ No config.json found. Run setup.py first!")
    exit(1)

with open(CONFIG_FILE, "r", encoding="utf-8") as cfg:
    config = json.load(cfg)

TWITCH_TOKEN = config["twitch_token"]
TWITCH_CHANNEL = config["twitch_channel"]
ETS2_MOD_PATH = config["ets2_mod_path"]
ETS2_PROFILE_PATH = config["ets2_profile_path"]
ETS2_STEAM_PATH = config["ets2_steam_path"]

USER_COOLDOWN = config.get("cooldowns", {}).get("user_command_seconds", 30)
GLOBAL_COOLDOWN = config.get("cooldowns", {}).get("refresh_global_seconds", 120)

bot = commands.Bot(token=TWITCH_TOKEN, prefix="!", initial_channels=[TWITCH_CHANNEL])

# --- CACHE ---
CACHE_FILE = "mod_cache.json"
STEAM_WORKSHOP_SEARCH_URL = "https://steamcommunity.com/workshop/browse/"

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        mod_cache = json.load(f)
else:
    mod_cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(mod_cache, f, indent=4, ensure_ascii=False)

def clear_cache():
    global mod_cache
    mod_cache = {}
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

# --- MOD NAME RESOLUTION ---
def get_mod_display_name(mod_file):
    """Get human-readable name for a mod file with caching and Steam lookup."""
    filename = os.path.basename(mod_file)

    if filename in mod_cache:
        return mod_cache[filename]

    # 1. Try manifest.sii
    try:
        with zipfile.ZipFile(mod_file, 'r') as z:
            if "manifest.sii" in z.namelist():
                with z.open("manifest.sii") as mf:
                    content = mf.read().decode("utf-8", errors="ignore")
                    match = re.search(r'mod_name: "(.*?)"', content)
                    if match:
                        human_name = match.group(1)
                        mod_cache[filename] = human_name
                        save_cache()
                        return human_name
    except Exception:
        pass

    # 2. Steam Workshop lookup
    cleaned_name = os.path.splitext(filename)[0].replace("_", " ").replace("-", " ").strip()
    try:
        params = {
            "appid": "227300",
            "searchtext": cleaned_name,
            "browsesort": "trend",
            "section": "readytouseitems"
        }
        resp = requests.get(STEAM_WORKSHOP_SEARCH_URL, params=params, timeout=5)
        if resp.ok:
            match = re.search(r'<div class="workshopItemTitle">(.+?)</div>', resp.text)
            if match:
                human_name = match.group(1).strip()
                mod_cache[filename] = human_name
                save_cache()
                return human_name
    except Exception:
        pass

    # 3. Fallback: prettified filename
    human_name = cleaned_name.title()
    mod_cache[filename] = human_name
    save_cache()
    return human_name

# --- MOD LIST PARSING ---
def parse_mod_manager():
    if not os.path.exists(ETS2_PROFILE_PATH):
        return []

    profiles = [os.path.join(ETS2_PROFILE_PATH, p) for p in os.listdir(ETS2_PROFILE_PATH)]
    if not profiles:
        return []

    latest_profile = max(profiles, key=os.path.getmtime)
    mod_manager_path = os.path.join(latest_profile, "mod_manager.sii")
    if not os.path.exists(mod_manager_path):
        return []

    mods_in_order = []
    with open(mod_manager_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        matches = re.findall(r'mod_package\s*:\s*\S+\s*\{\s*[^}]*?mod_name: "(.*?)"', content)
        mods_in_order.extend(matches)
    return mods_in_order

def parse_mod_folder(mod_path):
    if not os.path.exists(mod_path):
        return []
    mods = []
    for f in os.listdir(mod_path):
        if f.endswith(".scs"):
            mods.append(get_mod_display_name(os.path.join(mod_path, f)))
    return mods

def get_mod_list(profile_path, mod_path):
    mods = parse_mod_manager()
    if mods:
        return mods
    return parse_mod_folder(mod_path)

def get_dlc_list():
    dlc_manifest = os.path.join(ETS2_STEAM_PATH, "dlc")
    if not os.path.exists(dlc_manifest):
        return []
    return [f.replace(".dlc", "") for f in os.listdir(dlc_manifest) if f.endswith(".dlc")]

# --- RESPONSE FORMATTING ---
def format_mod_response(mods, dlcs):
    formatted_mods = []
    for mod in mods:
        search_url = f"https://steamcommunity.com/workshop/browse/?appid=227300&searchtext={mod.replace(' ', '+')}"
        formatted_mods.append(f"{mod} ({search_url})")
    return f"Mods: {' | '.join(formatted_mods)} || DLC: {', '.join(dlcs) if dlcs else 'None detected'}"

async def send_chunked_message(ctx, message, limit=500, delay=0.5):
    while message:
        chunk = message[:limit]
        last_sep = max(chunk.rfind("|"), chunk.rfind(","), chunk.rfind(" "))
        if 0 < last_sep < len(chunk):
            chunk = chunk[:last_sep]
        await ctx.send(chunk.strip())
        message = message[len(chunk):].strip()
        if message:
            await asyncio.sleep(delay)

# --- ANTI-SPAM ---
user_cooldowns = {}
global_cooldowns = {"mods": 0, "refreshmods": 0}

def check_cooldown(user, command):
    now = time.time()
    # Per-user cooldown
    if user in user_cooldowns and now - user_cooldowns[user] < USER_COOLDOWN:
        return False
    # Global cooldown for refreshmods
    if command == "refreshmods" and now - global_cooldowns["refreshmods"] < GLOBAL_COOLDOWN:
        return False
    # Passed cooldown → update
    user_cooldowns[user] = now
    if command == "refreshmods":
        global_cooldowns["refreshmods"] = now
    return True

# --- TWITCH COMMANDS ---
@bot.command(name="mods")
async def mods_command(ctx):
    if not check_cooldown(ctx.author.name, "mods"):
        return
    mods = get_mod_list(ETS2_PROFILE_PATH, ETS2_MOD_PATH)
    dlcs = get_dlc_list()
    response = format_mod_response(mods, dlcs)
    await send_chunked_message(ctx, response)

@bot.command(name="refreshmods")
async def refreshmods_command(ctx):
    if not check_cooldown(ctx.author.name, "refreshmods"):
        return
    clear_cache()
    mods = get_mod_list(ETS2_PROFILE_PATH, ETS2_MOD_PATH)
    dlcs = get_dlc_list()
    response = "🔄 Mod cache refreshed! " + format_mod_response(mods, dlcs)
    await send_chunked_message(ctx, response)

# --- START BOT ---
if __name__ == "__main__":
    print("✅ Bot started. Type !mods or !refreshmods in Twitch chat.")
    bot.run()
