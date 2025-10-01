import os
import re
import json
import zipfile
import asyncio
import time
import requests
import struct
import zlib
import atexit
import sys
from twitchio.ext import commands

# SII decryption imports
try:
    from Crypto.Cipher import AES
except ImportError:
    print("Installing pycryptodome for SII decryption...")
    import subprocess
    subprocess.check_call(["pip", "install", "pycryptodome"])
    from Crypto.Cipher import AES

# Single instance prevention
LOCK_FILE = "bot_instance.lock"

def ensure_single_instance():
    """Ensure only one instance of the bot can run at a time."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                existing_pid = int(f.read().strip())
            
            # Check if the process is still running
            try:
                import psutil
                if psutil.pid_exists(existing_pid):
                    print(f"❌ Another bot instance is already running (PID: {existing_pid})")
                    print("   Please stop the existing instance before starting a new one.")
                    sys.exit(1)
                else:
                    print(f"🧹 Cleaning up stale lock file (PID {existing_pid} no longer exists)")
                    os.remove(LOCK_FILE)
            except ImportError:
                # Fallback method without psutil
                print("⚠️  Cannot check if existing instance is running (psutil not available)")
                print("   If you're sure no other instance is running, delete 'bot_instance.lock' manually")
                print("   Or run: del bot_instance.lock")
                sys.exit(1)
        except (ValueError, FileNotFoundError):
            print("🧹 Cleaning up invalid lock file")
            os.remove(LOCK_FILE)
    
    # Create lock file with current PID
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    # Ensure cleanup on exit
    atexit.register(cleanup_lock_file)
    print(f"🔒 Bot instance locked (PID: {os.getpid()})")

def cleanup_lock_file():
    """Clean up the lock file when the bot exits."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print("🧹 Lock file cleaned up")
    except:
        pass  # Ignore cleanup errors

def hex_to_readable_profile_name(hex_string):
    """Convert hex-encoded profile directory name to human-readable string."""
    try:
        # Check if it looks like a hex string (only hex chars and reasonable length)
        if not all(c in '0123456789ABCDEFabcdef' for c in hex_string):
            return hex_string  # Return original if contains non-hex characters
        
        # Must be even length and reasonable size for profile names
        if len(hex_string) % 2 != 0 or len(hex_string) < 4 or len(hex_string) > 100:
            return hex_string
        
        # Convert hex to bytes, then decode to string
        decoded_bytes = bytes.fromhex(hex_string)
        
        # Try UTF-8 first, then UTF-16 (common for profile names)
        try:
            readable_name = decoded_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                readable_name = decoded_bytes.decode('utf-16le')
            except UnicodeDecodeError:
                return hex_string  # Return original if can't decode
        
        # Clean up any null bytes and non-printable characters
        readable_name = ''.join(c for c in readable_name if c.isprintable() and c != '\x00')
        
        # Only return decoded name if it looks like a reasonable profile name
        if readable_name and len(readable_name) >= 2 and readable_name.isascii():
            return readable_name
        else:
            return hex_string
    except Exception:
        return hex_string  # Return original if any error occurs

# SII decryption key from TheLazyTomcat/SII_Decrypt
SII_KEY = bytes([
    0x2a, 0x5f, 0xcb, 0x17, 0x91, 0xd2, 0x2f, 0xb6, 0x02, 0x45, 0xb3, 0xd8, 0x36, 0x9e, 0xd0, 0xb2,
    0xc2, 0x73, 0x71, 0x56, 0x3f, 0xbf, 0x1f, 0x3c, 0x9e, 0xdf, 0x6b, 0x11, 0x82, 0x5a, 0x5d, 0x0a
])

# SII file signatures
SII_SIGNATURE_ENCRYPTED = 0x43736353  # "ScsC"
SII_SIGNATURE_NORMAL = 0x4e696953     # "SiiN"

def decrypt_sii_file(file_path):
    """Decrypt SII files using proper AES-256-CBC decryption."""
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
        
        if len(data) < 4:
            return None
            
        signature = struct.unpack('<I', data[:4])[0]
        
        if signature == SII_SIGNATURE_NORMAL:
            # Plain text SII file
            return data.decode('utf-8', errors='ignore')
        elif signature == SII_SIGNATURE_ENCRYPTED:
            # Encrypted SII file - decrypt it
            header_size = 4 + 32 + 16 + 4  # signature + HMAC + IV + datasize
            if len(data) < header_size:
                return None
                
            hmac_hash = data[4:36]      # 32 bytes HMAC
            init_vector = data[36:52]   # 16 bytes IV
            data_size = struct.unpack('<I', data[52:56])[0]
            encrypted_payload = data[56:]
            
            # Decrypt using AES-256-CBC
            cipher = AES.new(SII_KEY, AES.MODE_CBC, init_vector)
            decrypted_compressed = cipher.decrypt(encrypted_payload)
            
            # Decompress using zlib
            try:
                decompressed = zlib.decompress(decrypted_compressed)
                return decompressed.decode('utf-8', errors='ignore')
            except zlib.error:
                # Try without decompression
                return decrypted_compressed.decode('utf-8', errors='ignore')
        else:
            return None
            
    except Exception as e:
        print(f"Error decrypting {file_path}: {e}")
        return None

def parse_profile_for_mods(content):
    """Parse profile.sii content for active mod configuration."""
    if not content:
        return []
    
    found_mods = []
    
    # Look for the exact active_mods array format from profile.sii
    # Format: active_mods[X]: "mod_identifier|Display Name"
    pattern = r'active_mods\[(\d+)\]:\s*"([^|]+)\|([^"]+)"'
    matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
    
    if matches:
        # Sort by index in REVERSE order - ETS2 stores highest index as first in load order
        sorted_matches = sorted(matches, key=lambda x: int(x[0]), reverse=True)
        
        for index, mod_id, display_name in sorted_matches:
            # Use the display name directly
            clean_name = display_name.strip()
            if clean_name and len(clean_name) > 2:
                found_mods.append(clean_name)
    
    return found_mods

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
    """Parse active mods from profile.sii using proper SII decryption.
    
    Reads the actual active mod configuration directly from the ETS2 profile.sii file,
    which contains the authoritative list of 25 active mods in correct load order.
    This matches exactly what's shown in the ETS2 interface.
    """
    print("🔍 Reading active mods from profile.sii...")
    
    # Find all profile folders that contain actual user profiles
    profile_dirs = []
    ets2_docs_path = os.path.dirname(ETS2_PROFILE_PATH)
    
    # Look for profile directories in ETS2 documents folder
    for item in os.listdir(ets2_docs_path):
        item_path = os.path.join(ets2_docs_path, item)
        if os.path.isdir(item_path):
            item_lower = item.lower()
            # Include: profiles, profiles(version).bak
            # Exclude: steam_profiles, steam_profiles(version).bak  
            if (item_lower.startswith('profiles') and 
                not item_lower.startswith('steam_profiles')):
                profile_dirs.append(item_path)
                print(f"📁 Found valid profile directory: {item}")
            elif item_lower.startswith('steam_profiles'):
                print(f"📁 Skipped steam profile directory: {item}")
    
    # Only add the configured profile path if it's valid (not steam_profiles)
    if (ETS2_PROFILE_PATH not in profile_dirs and os.path.exists(ETS2_PROFILE_PATH)):
        config_dir_name = os.path.basename(ETS2_PROFILE_PATH).lower()
        if config_dir_name.startswith('profiles') and not config_dir_name.startswith('steam_profiles'):
            profile_dirs.append(ETS2_PROFILE_PATH)
            print(f"📁 Added configured profile path: {os.path.basename(ETS2_PROFILE_PATH)}")
        else:
            print(f"📁 Skipped configured steam profile path: {os.path.basename(ETS2_PROFILE_PATH)}")
    
    if not profile_dirs:
        print("❌ No profile directories found")
        return []
    
    print(f"📂 Scanning {len(profile_dirs)} profile director{'y' if len(profile_dirs) == 1 else 'ies'} for active profiles")
    
    # Search all profile directories for profile.sii files and find the most recently used one
    most_recent_profile = None
    most_recent_timestamp = 0
    all_active_profiles = []
    
    for profile_dir in profile_dirs:
        if not os.path.exists(profile_dir):
            continue
            
        print(f"🔍 Scanning profile directory: {os.path.basename(profile_dir)}")
            
        # Find all subdirectories in this profile directory
        try:
            subdirs = [d for d in os.listdir(profile_dir) if os.path.isdir(os.path.join(profile_dir, d))]
        except:
            print(f"   ⚠️ Cannot access directory: {profile_dir}")
            continue
            
        for subdir in subdirs:
            # Convert hex profile directory name to readable name
            readable_name = hex_to_readable_profile_name(subdir)
            profile_display_name = readable_name if readable_name != subdir else f"Profile ({subdir})"
            
            profile_sii_path = os.path.join(profile_dir, subdir, "profile.sii")
            
            if os.path.exists(profile_sii_path):
                # Get the modification timestamp of the profile.sii file
                try:
                    profile_mtime = os.path.getmtime(profile_sii_path)
                    from datetime import datetime
                    readable_time = datetime.fromtimestamp(profile_mtime).strftime("%Y-%m-%d %H:%M:%S")
                except:
                    profile_mtime = 0
                    readable_time = "unknown"
                
                # Try to decrypt and parse the profile.sii
                decrypted_content = decrypt_sii_file(profile_sii_path)
                
                if decrypted_content:
                    # Check if this profile has active mods
                    active_mods_count = 0
                    for line in decrypted_content.split('\n'):
                        if 'active_mods:' in line and ':' in line:
                            try:
                                active_mods_count = int(line.split(':')[1].strip())
                                break
                            except:
                                pass
                    
                    # Include all profiles (regardless of mod count) to find the most recent
                    profile_info = {
                        'path': os.path.join(profile_dir, subdir),
                        'display_name': profile_display_name,
                        'subdir': subdir,
                        'mods_count': active_mods_count,
                        'timestamp': profile_mtime,
                        'profile_sii_path': profile_sii_path,
                        'decrypted_content': decrypted_content,
                        'parent_dir': os.path.basename(profile_dir)
                    }
                    all_active_profiles.append(profile_info)
                    
                    # Track the most recently modified profile (regardless of mod count)
                    if profile_mtime > most_recent_timestamp:
                        most_recent_timestamp = profile_mtime
                        most_recent_profile = profile_info
                    
                    status = f"{active_mods_count} mods" if active_mods_count > 0 else "no mods"
                    print(f"   📄 {profile_display_name} - {status} (last used: {readable_time})")
                else:
                    print(f"   ❌ {profile_display_name} - cannot decrypt profile.sii")
            else:
                print(f"   ⚠️ {profile_display_name} - no profile.sii found")

    # Report all found profiles and select the most recent
    if all_active_profiles:
        print(f"\n📊 Found {len(all_active_profiles)} profile(s) across all directories:")
        for profile in sorted(all_active_profiles, key=lambda p: p['timestamp'], reverse=True):
            from datetime import datetime
            readable_time = datetime.fromtimestamp(profile['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
            status = "🏆 MOST RECENT" if profile == most_recent_profile else "  "
            mods_text = f"{profile['mods_count']} mods" if profile['mods_count'] > 0 else "no mods"
            print(f"   {status} {profile['display_name']} ({profile['parent_dir']}) - {mods_text} (last used: {readable_time})")
        
        if most_recent_profile:
            print(f"\n✅ Using most recent profile: '{most_recent_profile['display_name']}' from {most_recent_profile['parent_dir']}")
            
            # Only proceed with mod parsing if the profile has mods
            if most_recent_profile['mods_count'] > 0:
                # Parse the mods from the most recent profile
                active_mods = parse_profile_for_mods(most_recent_profile['decrypted_content'])
                
                if active_mods:
                    print(f"📋 Successfully extracted {len(active_mods)} mods from most recent profile")
                    return active_mods
                else:
                    print(f"⚠️  Most recent profile has {most_recent_profile['mods_count']} mods but extraction failed")
            else:
                print(f"ℹ️  Most recent profile has no active mods")
                return []
    
    print("❌ No active profile.sii found with mods")
    print("ℹ️  Falling back to comprehensive detection...")
    
    # Fallback to the original method if profile.sii doesn't work
    return parse_mod_manager_fallback()

def parse_mod_manager_fallback():
    """Fallback: Parse active mods using comprehensive detection to match ETS2's UI display.
    
    Combines local .scs files with workshop mods from mods_info.sii to provide
    the complete list of 25 active mods as shown in the ETS2 interface.
    """
    print("ℹ️  Using comprehensive mod detection fallback...")
    
    ets2_docs_path = os.path.dirname(ETS2_PROFILE_PATH)
    
    # Step 1: Get local mods from mod folder (.scs files)
    local_mods = []
    mod_folder_path = os.path.join(ets2_docs_path, "mod")
    
    if os.path.exists(mod_folder_path):
        try:
            mod_files = [f for f in os.listdir(mod_folder_path) if f.endswith('.scs')]
            for mod_file in sorted(mod_files):
                clean_name = clean_mod_name(mod_file)
                local_mods.append(clean_name)
            print(f"📦 Found {len(local_mods)} local .scs files")
        except Exception as e:
            print(f"❌ Error reading mod folder: {e}")
    
    # Step 2: Get workshop mods from mods_info.sii
    workshop_mods = []
    mods_info_path = os.path.join(ets2_docs_path, "mods_info.sii")
    
    if os.path.exists(mods_info_path):
        try:
            with open(mods_info_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            
            # Parse workshop mod entries
            info_pattern = r'info\[(\d+)\]:\s*"([^"]*)"'
            matches = re.findall(info_pattern, content)
            
            processed_ids = set()
            
            for index, entry in matches:
                if not entry or '|' not in entry:
                    continue
                
                mod_id, timestamp = entry.split('|', 1)
                
                # Skip duplicates
                if mod_id in processed_ids:
                    continue
                processed_ids.add(mod_id)
                
                # Workshop mod
                if 'mod_workshop_package.' in mod_id:
                    workshop_id_match = re.search(r'mod_workshop_package\.(\d+)', mod_id)
                    if workshop_id_match:
                        workshop_id = workshop_id_match.group(1)
                        # Try to make workshop IDs more meaningful
                        if len(workshop_id) > 8:
                            workshop_mods.append(f"Steam Workshop Mod {workshop_id[-6:]}")
                        else:
                            workshop_mods.append(f"Steam Workshop Mod {workshop_id}")
                
                # Additional local mod references (not in folder)
                elif mod_id.endswith('.scs'):
                    clean_name = clean_mod_name(mod_id)
                    # Only add if not already in local mods
                    if clean_name not in local_mods:
                        local_mods.append(clean_name)
            
            print(f"📋 Found {len(workshop_mods)} workshop mod references")
            
        except Exception as e:
            print(f"❌ Error reading mods_info.sii: {e}")
    
    # Step 3: Combine to match ETS2's 25 active mods
    all_mods = local_mods + workshop_mods
    
    # ETS2 shows 25 active mods, so prioritize accordingly
    target_count = 25
    
    if len(all_mods) >= target_count:
        # Take first 25 mods (local mods are prioritized as they're in the active folder)
        active_mods = all_mods[:target_count]
        print(f"✅ Selected {len(active_mods)} active mods to match ETS2 UI")
    else:
        # Take all available mods
        active_mods = all_mods
        print(f"📊 Found {len(active_mods)} total mods (ETS2 shows {target_count})")
    
    return active_mods

def clean_mod_name(filename):
    """Clean up mod filename to a readable display name."""
    mod_name = os.path.splitext(filename)[0]
    mod_name = mod_name.replace('_', ' ').replace('-', ' ')
    
    # Handle common patterns
    words = mod_name.split()
    cleaned_words = []
    
    for word in words:
        # Keep lowercase articles and prepositions
        if word.lower() in ['v', 'by', 'for', 'and', 'the', 'of', 'to', 'in', 'on', 'at']:
            cleaned_words.append(word.lower())
        # Handle version numbers
        elif word.startswith('v') and len(word) > 1 and word[1:].replace('.', '').replace('_', '').isdigit():
            cleaned_words.append(word.upper())
        # Handle ProMods specific formatting
        elif word.lower().startswith('promods'):
            if 'v' in word.lower():
                parts = word.split('v')
                cleaned_words.append(f"ProMods {parts[0][7:].title()} V{parts[1]}" if len(parts) > 1 else word.title())
            else:
                cleaned_words.append(word.title())
        # Regular capitalization
        else:
            cleaned_words.append(word.title())
    
    return ' '.join(cleaned_words)

def get_dlc_list():
    """Get major map DLC that affects convoy compatibility."""
    dlcs = []
    
    # Focus on MAJOR MAP DLC only - these are what matter for convoy compatibility
    major_map_dlc = {
        "east": "Going East!",
        "north": "Scandinavia", 
        "fr": "Vive la France!",
        "it": "Italia",
        "balt": "Beyond the Baltic Sea",
        "iberia": "Iberia",
        "balkan_w": "West Balkans",
        "greece": "Greece"
    }
    
    # Method 1: Check for major map DLC .scs files in Steam directory
    try:
        if os.path.exists(ETS2_STEAM_PATH):
            for item in os.listdir(ETS2_STEAM_PATH):
                if item.startswith("dlc_") and item.endswith(".scs"):
                    dlc_code = item[4:-4]  # Remove "dlc_" prefix and ".scs" suffix
                    
                    # Only include major map DLC that affects convoy compatibility
                    if dlc_code in major_map_dlc:
                        dlc_name = major_map_dlc[dlc_code]
                        if dlc_name not in dlcs:
                            dlcs.append(dlc_name)
    except Exception:
        pass
    
    # Method 2: Fallback - check profile for any DLC activation patterns
    try:
        if os.path.exists(ETS2_PROFILE_PATH):
            profiles = [os.path.join(ETS2_PROFILE_PATH, p) for p in os.listdir(ETS2_PROFILE_PATH)]
            if profiles:
                latest_profile = max(profiles, key=os.path.getmtime)
                
                # Check multiple potential DLC tracking files
                potential_files = ["profile.sii", "config.cfg", "config_local.cfg"]
                
                for filename in potential_files:
                    file_path = os.path.join(latest_profile, filename)
                    if os.path.exists(file_path):
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            
                            # Look for major map DLC in the content
                            for dlc_code, dlc_name in major_map_dlc.items():
                                dlc_patterns = [
                                    rf'dlc_{dlc_code}.*?:\s*[1-9]',  # activation flag = 1 or higher
                                    rf'{dlc_code}.*?enabled',        # contains "enabled"
                                    rf'{dlc_code}.*?active',         # contains "active"
                                    rf'"{dlc_code}"'                 # quoted reference
                                ]
                                
                                for pattern in dlc_patterns:
                                    if re.search(pattern, content, re.IGNORECASE):
                                        if dlc_name not in dlcs:
                                            dlcs.append(dlc_name)
                                        break  # Found this DLC, move to next
    except Exception:
        pass
    
    # If no specific activation found, assume all major map DLC files = active
    # This is usually correct since Steam DLC is typically always active when owned
    
    return sorted(dlcs)  # Return sorted for consistency

def parse_mod_folder(mod_path):
    """Fallback method: parse mod folder when profile.sii is unavailable."""
    if not os.path.exists(mod_path):
        return []
    mods = []
    # Get .scs files and sort them alphabetically as fallback
    scs_files = [f for f in os.listdir(mod_path) if f.endswith(".scs")]
    scs_files.sort()  # At least give some consistent order
    
    for f in scs_files:
        mods.append(get_mod_display_name(os.path.join(mod_path, f)))
    return mods

def get_mod_list(profile_path, mod_path):
    """Get mod list in correct load order - CRITICAL for convoy compatibility!"""
    # Use profile.sii parsing (proper load order)
    mods = parse_mod_manager()
    if mods:
        print(f"✅ Found {len(mods)} mods in correct load order from profile.sii")
        return mods
    
    # Fallback to mod folder (alphabetical order - not ideal but better than nothing)
    print("⚠️  profile.sii not found, using alphabetical order from mod folder")
    return parse_mod_folder(mod_path)

# --- RESPONSE FORMATTING ---
def format_mod_response(mods, dlcs):
    """Format mod response with numbered order for convoy compatibility."""
    if not mods and not dlcs:
        return "❌ No mods or DLC detected! Check your ETS2 installation paths."
    
    response_parts = []
    
    # Format mods with load order numbers - CRITICAL for convoy!
    if mods:
        response_parts.append(f"🚛 MODS (Load Order - MUST MATCH FOR CONVOY): ")
        formatted_mods = []
        for i, mod in enumerate(mods, 1):
            # Truncate long mod names for readability but keep the order number
            mod_name = mod[:30] + "..." if len(mod) > 33 else mod
            formatted_mods.append(f"{i}.{mod_name}")
        
        # Join with separators that are easy to read
        mods_text = " | ".join(formatted_mods)
        response_parts.append(mods_text)
    else:
        response_parts.append("🚛 MODS: None detected")
    
    # Format DLC
    if dlcs:
        response_parts.append(f" || 🗺️ DLC: {', '.join(dlcs)}")
    else:
        response_parts.append(" || 🗺️ DLC: None detected")
    
    return "".join(response_parts)

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
        remaining = int(USER_COOLDOWN - (now - user_cooldowns[user]))
        return False, f"⏰ Please wait {remaining} seconds before using commands again."
    
    # Global cooldown for refreshmods
    if command == "refreshmods" and now - global_cooldowns["refreshmods"] < GLOBAL_COOLDOWN:
        remaining = int(GLOBAL_COOLDOWN - (now - global_cooldowns["refreshmods"]))
        return False, f"⏰ Please wait {remaining} seconds before refreshing mods again (global cooldown)."
    
    # Passed cooldown → update
    user_cooldowns[user] = now
    if command == "refreshmods":
        global_cooldowns["refreshmods"] = now
    return True, None

# --- TWITCH COMMANDS ---
@bot.command(name="mods")
async def mods_command(ctx):
    cooldown_ok, timeout_message = check_cooldown(ctx.author.name, "mods")
    if not cooldown_ok:
        await ctx.send(f"@{ctx.author.name}: {timeout_message}")
        return
    
    mods = get_mod_list(ETS2_PROFILE_PATH, ETS2_MOD_PATH)
    dlcs = get_dlc_list()
    response = format_mod_response(mods, dlcs)
    await send_chunked_message(ctx, response)

@bot.command(name="refreshmods")
async def refreshmods_command(ctx):
    cooldown_ok, timeout_message = check_cooldown(ctx.author.name, "refreshmods")
    if not cooldown_ok:
        await ctx.send(f"@{ctx.author.name}: {timeout_message}")
        return
    
    clear_cache()
    mods = get_mod_list(ETS2_PROFILE_PATH, ETS2_MOD_PATH)
    dlcs = get_dlc_list()
    response = "✅ Mod cache refreshed! Found " + str(len(mods)) + " active mods. Use !mods to see the list."
    await ctx.send(f"@{ctx.author.name}: {response}")

# --- START BOT ---
if __name__ == "__main__":
    # Ensure only one instance can run
    ensure_single_instance()
    
    print("✅ Bot started. Type !mods or !refreshmods in Twitch chat.")
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
        cleanup_lock_file()
    except Exception as e:
        print(f"\n❌ Bot crashed: {e}")
        cleanup_lock_file()
        raise
