import os
import re
import json
import asyncio
from twitchio.ext import commands

# --- LOAD CONFIG ---
if not os.path.exists("config.json"):
    print("❌ No config.json found. Run setup.py first!")
    exit(1)

with open("config.json", "r") as cfg:
    config = json.load(cfg)

TWITCH_TOKEN = config["twitch_token"]
TWITCH_CHANNEL = config["twitch_channel"]
ETS2_MOD_PATH = config["ets2_mod_path"]
ETS2_PROFILE_PATH = config["ets2_profile_path"]
ETS2_STEAM_PATH = config["ets2_steam_path"]

bot = commands.Bot(token=TWITCH_TOKEN, prefix="!", initial_channels=[TWITCH_CHANNEL])

# --- FUNCTIONS ---
def parse_mod_manager():
    """Read ETS2 mod_manager.sii for correct mod load order."""
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

def get_dlc_list():
    dlc_manifest = os.path.join(ETS2_STEAM_PATH, "dlc")
    if not os.path.exists(dlc_manifest):
        return []
    return [f.replace(".dlc", "") for f in os.listdir(dlc_manifest) if f.endswith(".dlc")]

def format_mod_response(mods, dlcs):
    formatted_mods = []
    for mod in mods:
        search_url = f"https://steamcommunity.com/workshop/browse/?appid=227300&searchtext={mod.replace(' ', '+')}"
        formatted_mods.append(f"{mod} ({search_url})")
    return f"Mods (Load Order): {' | '.join(formatted_mods)} || DLC: {', '.join(dlcs) if dlcs else 'None detected'}"

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

# --- TWITCH COMMAND ---
@bot.command(name="mods")
async def mods_command(ctx):
    mods = parse_mod_manager()
    dlcs = get_dlc_list()
    response = format_mod_response(mods, dlcs)
    await send_chunked_message(ctx, response)

# --- START BOT ---
if __name__ == "__main__":
    print("✅ Bot started. Type !mods in your Twitch chat.")
    bot.run()
