#!/usr/bin/env python3
"""
ETS2 Twitch Mod Bot - Optimized Version

A Twitch bot that reads and displays active Euro Truck Simulator 2 mods
and DLC for convoy compatibility checking.

This optimized version follows Python best practices:
- Proper async/await patterns
- Type hints throughout
- Structured logging
- Configuration management
- Error handling
- Code organization
- Resource management
"""

import asyncio
import json
import logging
import os
import re
import struct
import sys
import time
import zipfile
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from weakref import WeakValueDictionary

import aiofiles
import aiohttp
from twitchio.ext import commands

# Optional dependencies with graceful fallback
try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logging.warning("pycryptodome not available - SII decryption disabled")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logging.warning("psutil not available - enhanced process checking disabled")


# Constants
LOCK_FILE = Path("bot_instance.lock")
CACHE_FILE = Path("mod_cache.json")
CONFIG_FILE = Path("config.json")
STEAM_WORKSHOP_URL = "https://steamcommunity.com/workshop/browse/"

# SII decryption constants
SII_KEY = bytes([
    0x2a, 0x5f, 0xcb, 0x17, 0x91, 0xd2, 0x2f, 0xb6, 0x02, 0x45, 0xb3, 0xd8, 0x36, 0x9e, 0xd0, 0xb2,
    0xc2, 0x73, 0x71, 0x56, 0x3f, 0xbf, 0x1f, 0x3c, 0x9e, 0xdf, 0x6b, 0x11, 0x82, 0x5a, 0x5d, 0x0a
])
SII_SIGNATURE_ENCRYPTED = 0x43736353  # "ScsC"
SII_SIGNATURE_NORMAL = 0x4e696953     # "SiiN"

# Major map DLC for convoy compatibility
MAJOR_MAP_DLC = {
    "east": "Going East!",
    "north": "Scandinavia", 
    "fr": "Vive la France!",
    "it": "Italia",
    "balt": "Beyond the Baltic Sea",
    "iberia": "Iberia",
    "balkan_w": "West Balkans",
    "greece": "Greece"
}


@dataclass
class BotConfig:
    """Configuration settings for the bot."""
    twitch_token: str
    twitch_channel: str
    ets2_mod_path: Path
    ets2_profile_path: Path
    ets2_steam_path: Path
    user_cooldown_seconds: int = 30
    refresh_global_seconds: int = 120
    
    @classmethod
    async def load(cls, config_path: Path = CONFIG_FILE) -> 'BotConfig':
        """Load configuration from JSON file with validation."""
        if not config_path.exists():
            raise FileNotFoundError(f"Config file {config_path} not found. Run setup.py first!")
        
        async with aiofiles.open(config_path, 'r', encoding='utf-8') as f:
            data = json.loads(await f.read())
        
        # Validate required fields
        required_fields = ['twitch_token', 'twitch_channel', 'ets2_mod_path', 
                          'ets2_profile_path', 'ets2_steam_path']
        for field in required_fields:
            if not data.get(field):
                raise ValueError(f"Missing required config field: {field}")
        
        cooldowns = data.get('cooldowns', {})
        
        return cls(
            twitch_token=data['twitch_token'],
            twitch_channel=data['twitch_channel'],
            ets2_mod_path=Path(data['ets2_mod_path']),
            ets2_profile_path=Path(data['ets2_profile_path']),
            ets2_steam_path=Path(data['ets2_steam_path']),
            user_cooldown_seconds=cooldowns.get('user_command_seconds', 30),
            refresh_global_seconds=cooldowns.get('refresh_global_seconds', 120)
        )


@dataclass
class ModInfo:
    """Information about a mod."""
    display_name: str
    filename: str
    load_order: int = 0
    source: str = "unknown"  # "profile", "folder", "workshop"


@dataclass
class ProfileInfo:
    """Information about an ETS2 profile."""
    path: Path
    display_name: str
    mods_count: int
    timestamp: float
    parent_dir: str


class CooldownManager:
    """Manages command cooldowns for users and global operations."""
    
    def __init__(self, user_cooldown: int, global_cooldown: int):
        self.user_cooldown = user_cooldown
        self.global_cooldown = global_cooldown
        self.user_cooldowns: Dict[str, float] = {}
        self.global_cooldowns: Dict[str, float] = {}
    
    def check_cooldown(self, user: str, command: str) -> Tuple[bool, Optional[str]]:
        """Check if user/command is on cooldown."""
        now = time.time()
        
        # Check user cooldown
        if user in self.user_cooldowns:
            remaining = self.user_cooldown - (now - self.user_cooldowns[user])
            if remaining > 0:
                return False, f"⏰ Please wait {int(remaining)} seconds before using commands again."
        
        # Check global cooldown for refresh commands
        if command == "refreshmods" and "refreshmods" in self.global_cooldowns:
            remaining = self.global_cooldown - (now - self.global_cooldowns["refreshmods"])
            if remaining > 0:
                return False, f"⏰ Please wait {int(remaining)} seconds before refreshing mods again (global cooldown)."
        
        # Update cooldowns
        self.user_cooldowns[user] = now
        if command == "refreshmods":
            self.global_cooldowns["refreshmods"] = now
        
        return True, None


class ModCache:
    """Async mod name cache with persistence."""
    
    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self._cache: Dict[str, str] = {}
        self._lock = asyncio.Lock()
    
    async def load(self) -> None:
        """Load cache from disk."""
        if not self.cache_file.exists():
            return
        
        try:
            async with aiofiles.open(self.cache_file, 'r', encoding='utf-8') as f:
                self._cache = json.loads(await f.read())
        except Exception as e:
            logging.warning(f"Failed to load cache: {e}")
            self._cache = {}
    
    async def save(self) -> None:
        """Save cache to disk."""
        try:
            async with aiofiles.open(self.cache_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self._cache, indent=2, ensure_ascii=False))
        except Exception as e:
            logging.error(f"Failed to save cache: {e}")
    
    async def get(self, key: str) -> Optional[str]:
        """Get cached value."""
        async with self._lock:
            return self._cache.get(key)
    
    async def set(self, key: str, value: str) -> None:
        """Set cached value and save."""
        async with self._lock:
            self._cache[key] = value
            await self.save()
    
    async def clear(self) -> None:
        """Clear cache."""
        async with self._lock:
            self._cache.clear()
            if self.cache_file.exists():
                self.cache_file.unlink()


class SingleInstanceLock:
    """Manages single instance lock file."""
    
    def __init__(self, lock_file: Path = LOCK_FILE):
        self.lock_file = lock_file
    
    async def acquire(self) -> None:
        """Acquire single instance lock."""
        if self.lock_file.exists():
            await self._handle_existing_lock()
        
        # Create lock file
        async with aiofiles.open(self.lock_file, 'w') as f:
            await f.write(str(os.getpid()))
        
        logging.info(f"🔒 Bot instance locked (PID: {os.getpid()})")
    
    async def _handle_existing_lock(self) -> None:
        """Handle existing lock file."""
        try:
            async with aiofiles.open(self.lock_file, 'r') as f:
                existing_pid = int((await f.read()).strip())
            
            if HAS_PSUTIL and psutil.pid_exists(existing_pid):
                logging.error(f"❌ Another bot instance is running (PID: {existing_pid})")
                sys.exit(1)
            else:
                logging.info(f"🧹 Cleaning up stale lock file (PID {existing_pid})")
                self.lock_file.unlink()
                
        except (ValueError, FileNotFoundError) as e:
            logging.info("🧹 Cleaning up invalid lock file")
            self.lock_file.unlink()
    
    def release(self) -> None:
        """Release lock file."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                logging.info("🧹 Lock file cleaned up")
        except Exception as e:
            logging.warning(f"Failed to cleanup lock file: {e}")


class SIIDecryptor:
    """Handles SII file decryption."""
    
    @staticmethod
    async def decrypt_file(file_path: Path) -> Optional[str]:
        """Decrypt SII file if encrypted, return content as string."""
        if not HAS_CRYPTO:
            # Fallback to reading as plain text
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return await f.read()
            except Exception:
                return None
        
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                data = await f.read()
            
            if len(data) < 4:
                return None
            
            signature = struct.unpack('<I', data[:4])[0]
            
            if signature == SII_SIGNATURE_NORMAL:
                # Plain text SII file
                return data.decode('utf-8', errors='ignore')
            
            elif signature == SII_SIGNATURE_ENCRYPTED:
                return await SIIDecryptor._decrypt_encrypted(data)
            
            return None
            
        except Exception as e:
            logging.error(f"Error decrypting {file_path}: {e}")
            return None
    
    @staticmethod
    async def _decrypt_encrypted(data: bytes) -> Optional[str]:
        """Decrypt encrypted SII data."""
        header_size = 4 + 32 + 16 + 4  # signature + HMAC + IV + datasize
        if len(data) < header_size:
            return None
        
        init_vector = data[36:52]   # 16 bytes IV
        encrypted_payload = data[56:]
        
        # Decrypt using AES-256-CBC
        cipher = AES.new(SII_KEY, AES.MODE_CBC, init_vector)
        decrypted_compressed = cipher.decrypt(encrypted_payload)
        
        # Try decompression
        try:
            decompressed = zlib.decompress(decrypted_compressed)
            return decompressed.decode('utf-8', errors='ignore')
        except zlib.error:
            # Try without decompression
            return decrypted_compressed.decode('utf-8', errors='ignore')


class ModParser:
    """Parses mod information from various sources."""
    
    def __init__(self, config: BotConfig, cache: ModCache):
        self.config = config
        self.cache = cache
    
    async def get_active_mods(self) -> List[ModInfo]:
        """Get list of active mods in correct load order."""
        # Try profile.sii first (most accurate)
        mods = await self._parse_from_profile()
        if mods:
            logging.info(f"✅ Found {len(mods)} mods from profile.sii")
            return mods
        
        # Fallback to mod folder
        logging.warning("⚠️ profile.sii not found, using mod folder")
        return await self._parse_from_folder()
    
    async def _parse_from_profile(self) -> List[ModInfo]:
        """Parse mods from profile.sii files."""
        profiles = await self._find_profiles()
        if not profiles:
            return []
        
        # Use most recent profile
        latest_profile = max(profiles, key=lambda p: p.timestamp)
        logging.info(f"Using profile: {latest_profile.display_name}")
        
        if latest_profile.mods_count == 0:
            return []
        
        # Decrypt and parse profile.sii
        profile_sii = latest_profile.path / "profile.sii"
        content = await SIIDecryptor.decrypt_file(profile_sii)
        
        if content:
            return self._extract_mods_from_content(content)
        
        return []
    
    async def _find_profiles(self) -> List[ProfileInfo]:
        """Find all valid ETS2 profiles."""
        profiles = []
        docs_path = self.config.ets2_profile_path.parent
        
        if not docs_path.exists():
            return profiles
        
        async for profile_dir in self._iter_profile_dirs(docs_path):
            async for subdir in self._iter_subdirs(profile_dir):
                profile_info = await self._analyze_profile(profile_dir, subdir)
                if profile_info:
                    profiles.append(profile_info)
        
        return profiles
    
    async def _iter_profile_dirs(self, docs_path: Path):
        """Iterate over valid profile directories."""
        try:
            for item in docs_path.iterdir():
                if item.is_dir():
                    name_lower = item.name.lower()
                    if (name_lower.startswith('profiles') and 
                        not name_lower.startswith('steam_profiles')):
                        yield item
        except OSError as e:
            logging.error(f"Cannot access {docs_path}: {e}")
    
    async def _iter_subdirs(self, profile_dir: Path):
        """Iterate over subdirectories in profile directory."""
        try:
            for subdir in profile_dir.iterdir():
                if subdir.is_dir():
                    yield subdir
        except OSError:
            pass
    
    async def _analyze_profile(self, profile_dir: Path, subdir: Path) -> Optional[ProfileInfo]:
        """Analyze a profile subdirectory."""
        profile_sii = subdir / "profile.sii"
        if not profile_sii.exists():
            return None
        
        try:
            stat = profile_sii.stat()
            content = await SIIDecryptor.decrypt_file(profile_sii)
            
            mods_count = 0
            if content:
                for line in content.split('\n'):
                    if 'active_mods:' in line and ':' in line:
                        try:
                            mods_count = int(line.split(':')[1].strip())
                            break
                        except (ValueError, IndexError):
                            pass
            
            display_name = self._hex_to_readable_name(subdir.name)
            
            return ProfileInfo(
                path=subdir,
                display_name=display_name,
                mods_count=mods_count,
                timestamp=stat.st_mtime,
                parent_dir=profile_dir.name
            )
        except Exception as e:
            logging.debug(f"Error analyzing profile {subdir}: {e}")
            return None
    
    def _hex_to_readable_name(self, hex_string: str) -> str:
        """Convert hex-encoded profile name to readable string."""
        try:
            if not all(c in '0123456789ABCDEFabcdef' for c in hex_string):
                return hex_string
            
            if len(hex_string) % 2 != 0 or len(hex_string) < 4 or len(hex_string) > 100:
                return hex_string
            
            decoded_bytes = bytes.fromhex(hex_string)
            
            # Try UTF-8, then UTF-16
            for encoding in ['utf-8', 'utf-16le']:
                try:
                    readable = decoded_bytes.decode(encoding)
                    cleaned = ''.join(c for c in readable if c.isprintable() and c != '\x00')
                    if cleaned and len(cleaned) >= 2 and cleaned.isascii():
                        return cleaned
                except UnicodeDecodeError:
                    continue
            
            return hex_string
        except Exception:
            return hex_string
    
    def _extract_mods_from_content(self, content: str) -> List[ModInfo]:
        """Extract mod list from profile.sii content."""
        mods = []
        pattern = r'active_mods\[(\d+)\]:\s*"([^|]+)\|([^"]+)"'
        matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
        
        if matches:
            # Sort by index in reverse order (ETS2 load order)
            sorted_matches = sorted(matches, key=lambda x: int(x[0]), reverse=True)
            
            for index, mod_id, display_name in sorted_matches:
                clean_name = display_name.strip()
                if clean_name and len(clean_name) > 2:
                    mods.append(ModInfo(
                        display_name=clean_name,
                        filename=mod_id,
                        load_order=int(index),
                        source="profile"
                    ))
        
        return mods
    
    async def _parse_from_folder(self) -> List[ModInfo]:
        """Fallback: parse mods from mod folder."""
        if not self.config.ets2_mod_path.exists():
            return []
        
        mods = []
        try:
            scs_files = [f for f in self.config.ets2_mod_path.iterdir() 
                        if f.suffix.lower() == '.scs']
            scs_files.sort()  # Alphabetical order as fallback
            
            for i, mod_file in enumerate(scs_files):
                display_name = await self._get_mod_display_name(mod_file)
                mods.append(ModInfo(
                    display_name=display_name,
                    filename=mod_file.name,
                    load_order=i,
                    source="folder"
                ))
        except Exception as e:
            logging.error(f"Error parsing mod folder: {e}")
        
        return mods
    
    async def _get_mod_display_name(self, mod_file: Path) -> str:
        """Get human-readable name for a mod file."""
        cached_name = await self.cache.get(mod_file.name)
        if cached_name:
            return cached_name
        
        # Try manifest.sii
        display_name = await self._extract_from_manifest(mod_file)
        if display_name:
            await self.cache.set(mod_file.name, display_name)
            return display_name
        
        # Try Steam Workshop lookup
        display_name = await self._lookup_steam_workshop(mod_file)
        if display_name:
            await self.cache.set(mod_file.name, display_name)
            return display_name
        
        # Fallback to cleaned filename
        display_name = self._clean_filename(mod_file.name)
        await self.cache.set(mod_file.name, display_name)
        return display_name
    
    async def _extract_from_manifest(self, mod_file: Path) -> Optional[str]:
        """Extract mod name from manifest.sii."""
        try:
            with zipfile.ZipFile(mod_file, 'r') as z:
                if "manifest.sii" in z.namelist():
                    with z.open("manifest.sii") as mf:
                        content = mf.read().decode("utf-8", errors="ignore")
                        match = re.search(r'mod_name: "(.*?)"', content)
                        if match:
                            return match.group(1)
        except Exception:
            pass
        return None
    
    async def _lookup_steam_workshop(self, mod_file: Path) -> Optional[str]:
        """Lookup mod name from Steam Workshop."""
        cleaned_name = mod_file.stem.replace("_", " ").replace("-", " ").strip()
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                params = {
                    "appid": "227300",
                    "searchtext": cleaned_name,
                    "browsesort": "trend",
                    "section": "readytouseitems"
                }
                async with session.get(STEAM_WORKSHOP_URL, params=params) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        match = re.search(r'<div class="workshopItemTitle">(.+?)</div>', text)
                        if match:
                            return match.group(1).strip()
        except Exception:
            pass
        
        return None
    
    def _clean_filename(self, filename: str) -> str:
        """Clean up filename for display."""
        name = Path(filename).stem
        name = name.replace('_', ' ').replace('-', ' ')
        words = name.split()
        
        cleaned_words = []
        for word in words:
            if word.lower() in ['v', 'by', 'for', 'and', 'the', 'of', 'to', 'in', 'on', 'at']:
                cleaned_words.append(word.lower())
            elif word.startswith('v') and len(word) > 1 and word[1:].replace('.', '').replace('_', '').isdigit():
                cleaned_words.append(word.upper())
            elif word.lower().startswith('promods'):
                if 'v' in word.lower():
                    parts = word.split('v')
                    if len(parts) > 1:
                        cleaned_words.append(f"ProMods {parts[0][7:].title()} V{parts[1]}")
                    else:
                        cleaned_words.append(word.title())
                else:
                    cleaned_words.append(word.title())
            else:
                cleaned_words.append(word.title())
        
        return ' '.join(cleaned_words)


class DLCDetector:
    """Detects installed and active DLC."""
    
    def __init__(self, config: BotConfig):
        self.config = config
    
    async def get_active_dlc(self) -> List[str]:
        """Get list of active major map DLC."""
        dlcs = set()
        
        # Check Steam DLC files
        if self.config.ets2_steam_path.exists():
            dlcs.update(await self._scan_steam_dlc())
        
        # Check profile for DLC activation
        dlcs.update(await self._scan_profile_dlc())
        
        return sorted(dlcs)
    
    async def _scan_steam_dlc(self) -> List[str]:
        """Scan Steam directory for DLC files."""
        dlcs = []
        try:
            for item in self.config.ets2_steam_path.iterdir():
                if item.name.startswith("dlc_") and item.suffix == ".scs":
                    dlc_code = item.stem[4:]  # Remove "dlc_" prefix
                    if dlc_code in MAJOR_MAP_DLC:
                        dlcs.append(MAJOR_MAP_DLC[dlc_code])
        except Exception as e:
            logging.error(f"Error scanning Steam DLC: {e}")
        
        return dlcs
    
    async def _scan_profile_dlc(self) -> List[str]:
        """Scan profile files for DLC activation."""
        dlcs = []
        
        if not self.config.ets2_profile_path.exists():
            return dlcs
        
        try:
            # Find most recent profile
            profiles = []
            for profile_dir in self.config.ets2_profile_path.iterdir():
                if profile_dir.is_dir():
                    profiles.append(profile_dir)
            
            if not profiles:
                return dlcs
            
            latest_profile = max(profiles, key=lambda p: p.stat().st_mtime)
            
            # Check potential DLC files
            potential_files = ["profile.sii", "config.cfg", "config_local.cfg"]
            
            for filename in potential_files:
                file_path = latest_profile / filename
                if file_path.exists():
                    dlcs.extend(await self._parse_dlc_from_file(file_path))
        
        except Exception as e:
            logging.error(f"Error scanning profile DLC: {e}")
        
        return list(set(dlcs))  # Remove duplicates
    
    async def _parse_dlc_from_file(self, file_path: Path) -> List[str]:
        """Parse DLC activation from a config file."""
        dlcs = []
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            
            for dlc_code, dlc_name in MAJOR_MAP_DLC.items():
                patterns = [
                    rf'dlc_{dlc_code}.*?:\s*[1-9]',
                    rf'{dlc_code}.*?enabled',
                    rf'{dlc_code}.*?active',
                    rf'"{dlc_code}"'
                ]
                
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        dlcs.append(dlc_name)
                        break
        
        except Exception as e:
            logging.debug(f"Error parsing {file_path}: {e}")
        
        return dlcs


class ETS2ModBot(commands.Bot):
    """Main bot class with improved error handling and async patterns."""
    
    def __init__(self, config: BotConfig):
        super().__init__(
            token=config.twitch_token,
            prefix="!",
            initial_channels=[config.twitch_channel]
        )
        
        self.config = config
        self.cache = ModCache()
        self.cooldown_manager = CooldownManager(
            config.user_cooldown_seconds,
            config.refresh_global_seconds
        )
        self.mod_parser = ModParser(config, self.cache)
        self.dlc_detector = DLCDetector(config)
        self.lock = SingleInstanceLock()
    
    async def event_ready(self) -> None:
        """Called when bot is ready."""
        logging.info(f"✅ Bot ready! Connected to {self.config.twitch_channel}")
    
    async def event_command_error(self, context, error) -> None:
        """Handle command errors gracefully."""
        logging.error(f"Command error: {error}")
        try:
            await context.send(f"@{context.author.name}: ❌ An error occurred. Please try again later.")
        except Exception:
            pass  # Don't let error handling cause more errors
    
    @commands.command(name="mods")
    async def mods_command(self, ctx) -> None:
        """Display active mods and DLC."""
        cooldown_ok, timeout_msg = self.cooldown_manager.check_cooldown(ctx.author.name, "mods")
        if not cooldown_ok:
            await ctx.send(f"@{ctx.author.name}: {timeout_msg}")
            return
        
        try:
            mods = await self.mod_parser.get_active_mods()
            dlcs = await self.dlc_detector.get_active_dlc()
            response = self._format_response(mods, dlcs)
            await self._send_chunked_message(ctx, response)
        except Exception as e:
            logging.error(f"Error in mods command: {e}")
            await ctx.send(f"@{ctx.author.name}: ❌ Error retrieving mods. Check bot configuration.")
    
    @commands.command(name="refreshmods")
    async def refresh_mods_command(self, ctx) -> None:
        """Refresh mod cache."""
        cooldown_ok, timeout_msg = self.cooldown_manager.check_cooldown(ctx.author.name, "refreshmods")
        if not cooldown_ok:
            await ctx.send(f"@{ctx.author.name}: {timeout_msg}")
            return
        
        try:
            await self.cache.clear()
            mods = await self.mod_parser.get_active_mods()
            response = f"✅ Mod cache refreshed! Found {len(mods)} active mods. Use !mods to see the list."
            await ctx.send(f"@{ctx.author.name}: {response}")
        except Exception as e:
            logging.error(f"Error in refresh command: {e}")
            await ctx.send(f"@{ctx.author.name}: ❌ Error refreshing cache.")
    
    def _format_response(self, mods: List[ModInfo], dlcs: List[str]) -> str:
        """Format mod and DLC response."""
        if not mods and not dlcs:
            return "❌ No mods or DLC detected! Check your ETS2 installation paths."
        
        response_parts = []
        
        if mods:
            response_parts.append("🚛 MODS (Load Order - MUST MATCH FOR CONVOY): ")
            formatted_mods = []
            for i, mod in enumerate(mods, 1):
                mod_name = mod.display_name[:30] + "..." if len(mod.display_name) > 33 else mod.display_name
                formatted_mods.append(f"{i}.{mod_name}")
            
            response_parts.append(" | ".join(formatted_mods))
        else:
            response_parts.append("🚛 MODS: None detected")
        
        if dlcs:
            response_parts.append(f" || 🗺️ DLC: {', '.join(dlcs)}")
        else:
            response_parts.append(" || 🗺️ DLC: None detected")
        
        return "".join(response_parts)
    
    async def _send_chunked_message(self, ctx, message: str, limit: int = 500, delay: float = 0.5) -> None:
        """Send long messages in chunks."""
        while message:
            chunk = message[:limit]
            last_sep = max(chunk.rfind("|"), chunk.rfind(","), chunk.rfind(" "))
            if 0 < last_sep < len(chunk):
                chunk = chunk[:last_sep]
            
            await ctx.send(chunk.strip())
            message = message[len(chunk):].strip()
            
            if message:
                await asyncio.sleep(delay)


async def setup_logging() -> None:
    """Configure logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('bot.log', encoding='utf-8')
        ]
    )


async def main() -> None:
    """Main entry point."""
    await setup_logging()
    
    try:
        # Load configuration
        config = await BotConfig.load()
        
        # Create bot
        bot = ETS2ModBot(config)
        
        # Acquire single instance lock
        await bot.lock.acquire()
        
        # Load cache
        await bot.cache.load()
        
        logging.info("🚀 Starting ETS2 Twitch Mod Bot")
        logging.info("✅ Type !mods or !refreshmods in Twitch chat")
        
        # Run bot
        await bot.start()
        
    except KeyboardInterrupt:
        logging.info("🛑 Bot stopped by user")
    except Exception as e:
        logging.error(f"❌ Bot crashed: {e}")
        raise
    finally:
        if 'bot' in locals():
            bot.lock.release()


if __name__ == "__main__":
    asyncio.run(main())