"""
lib/parser.py

ModParser and related dataclasses extracted from the original monolithic bot module.

Provides:
- ModInfo, ProfileInfo dataclasses
- ModParser class with async methods:
  - get_active_mods()
  - _parse_from_profile()
  - _parse_from_folder()
  - helpers for manifest extraction, steam lookup, filename cleaning, and profile discovery

The implementation is structured for testability:
- Most public behaviours are async and small helper methods are separated.
- Network calls use aiohttp and can be monkeypatched during tests.
- Uses an injected cache instance to avoid global state and allow mocking.

Note: this module expects a `config` object passed to ModParser with the following attributes:
- ets2_mod_path: Path to the mods folder
- ets2_profile_path: Path to the ETS2 profile root (or a path beneath which profile directories exist)
"""

from __future__ import annotations

import asyncio
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

# Local imports (the project layout places helper modules under lib/ and project root)
try:
    # Use the decrypt module from the lib package after refactor
    from lib.decrypt import SIIDecryptor, SII_SIGNATURE_NORMAL, SII_SIGNATURE_ENCRYPTED  # type: ignore
except Exception:  # pragma: no cover - defensive import for refactor staging
    # If running tests before the rest of the refactor, SIIDecryptor may be unavailable.
    SIIDecryptor = None  # type: ignore
    SII_SIGNATURE_NORMAL = None  # type: ignore
    SII_SIGNATURE_ENCRYPTED = None  # type: ignore

# Constants
STEAM_WORKSHOP_URL = "https://steamcommunity.com/workshop/browse/"

# Regex used to parse active_mods lines in profile.sii
_ACTIVE_MODS_RE = re.compile(
    r'active_mods\[(\d+)\]:\s*"([^|]+)\|([^"]+)"', re.IGNORECASE | re.MULTILINE
)


@dataclass
class ModInfo:
    display_name: str
    filename: str
    load_order: int = 0
    source: str = "unknown"


@dataclass
class ProfileInfo:
    path: Path
    display_name: str
    mods_count: int
    timestamp: float
    parent_dir: str


class ModParser:
    """Parses mod information from ETS2 profiles and mod folders.

    Args:
        config: object with attributes `ets2_mod_path` (Path) and `ets2_profile_path` (Path)
        cache: an async-friendly cache instance that implements `get(key) -> Optional[str]` and `set(key, value)`
    """

    def __init__(self, config, cache) -> None:
        self.config = config
        self.cache = cache

    async def get_active_mods(self) -> List[ModInfo]:
        """Return the active mods, preferring the profile.sii parsing path, falling back to the mod folder."""
        # Try parsing from profile.sii (most accurate load order)
        try:
            mods = await self._parse_from_profile()
            if mods:
                logging.info("ModParser: Found %d mods from profile.sii", len(mods))
                return mods
        except Exception as exc:
            logging.debug("ModParser: profile parsing failed: %s", exc, exc_info=True)

        # Fallback to parsing the mods folder
        logging.warning(
            "ModParser: profile.sii not found or empty; using mod folder as fallback"
        )
        return await self._parse_from_folder()

    # -------------------------
    # Profile-based parsing
    # -------------------------
    async def _parse_from_profile(self) -> List[ModInfo]:
        """Parse mods from the most recent profile.sii found under the configured profile path."""
        if not hasattr(self.config, "ets2_profile_path"):
            return []

        profiles = await self._find_profiles()
        if not profiles:
            return []

        # Choose most recent profile by timestamp
        latest_profile = max(profiles, key=lambda p: p.timestamp)
        logging.info("ModParser: using profile: %s", latest_profile.display_name)

        if latest_profile.mods_count == 0:
            return []

        profile_sii = latest_profile.path / "profile.sii"
        if not profile_sii.exists():
            return []

        # Use SIIDecryptor if available
        if SIIDecryptor is None:
            logging.debug(
                "ModParser: SIIDecryptor not available; cannot parse profile.sii"
            )
            return []

        content = await SIIDecryptor.decrypt_file(profile_sii)
        if not content:
            return []

        return self._extract_mods_from_content(content)

    async def _find_profiles(self) -> List[ProfileInfo]:
        """
        Discover profile directories under the ETS2 documents folder.

        The logic mirrors the game's layout where a 'profiles' directory contains per-profile subfolders.
        """
        profiles: List[ProfileInfo] = []

        docs_path = (
            Path(self.config.ets2_profile_path).parent
            if hasattr(self.config, "ets2_profile_path")
            else None
        )
        if not docs_path or not docs_path.exists():
            return profiles

        # Iterate candidate profile directories
        async for profile_dir in self._iter_profile_dirs(docs_path):
            async for subdir in self._iter_subdirs(profile_dir):
                info = await self._analyze_profile(profile_dir, subdir)
                if info:
                    profiles.append(info)

        return profiles

    async def _iter_profile_dirs(self, docs_path: Path):
        """Yield directories that look like ETS2 profile containers (e.g., profiles/, profiles_steam)."""
        try:
            for item in docs_path.iterdir():
                if item.is_dir():
                    name_lower = item.name.lower()
                    if name_lower.startswith("profiles") and not name_lower.startswith(
                        "steam_profiles"
                    ):
                        yield item
        except OSError as exc:
            logging.error("ModParser: cannot access docs path %s: %s", docs_path, exc)

    async def _iter_subdirs(self, profile_dir: Path):
        """Yield subdirectories inside a profile directory (each subdir is a profile)."""
        try:
            for subdir in profile_dir.iterdir():
                if subdir.is_dir():
                    yield subdir
        except OSError:
            # Ignore unreadable directories
            return

    async def _analyze_profile(
        self, profile_dir: Path, subdir: Path
    ) -> Optional[ProfileInfo]:
        """Return ProfileInfo for a valid profile subdir, or None if invalid."""
        profile_sii = subdir / "profile.sii"
        if not profile_sii.exists():
            return None

        try:
            stat = profile_sii.stat()
            content = None
            if SIIDecryptor is not None:
                content = await SIIDecryptor.decrypt_file(profile_sii)

            mods_count = 0
            if content:
                # Simple heuristic: look for a line like "active_mods: <number>"
                for line in content.splitlines():
                    if "active_mods:" in line and ":" in line:
                        try:
                            mods_count = int(line.split(":", 1)[1].strip())
                            break
                        except (ValueError, IndexError):
                            continue

            display_name = self._hex_to_readable_name(subdir.name)

            return ProfileInfo(
                path=subdir,
                display_name=display_name,
                mods_count=mods_count,
                timestamp=stat.st_mtime,
                parent_dir=profile_dir.name,
            )
        except Exception as exc:
            logging.debug(
                "ModParser: error analyzing profile %s: %s", subdir, exc, exc_info=True
            )
            return None

    def _hex_to_readable_name(self, hex_string: str) -> str:
        """Convert ETS2 hex-encoded profile folder names to readable names.

        If the input doesn't look like hex or decoding fails for reasonable encodings,
        the original string is returned.
        """
        try:
            if not all(c in "0123456789ABCDEFabcdef" for c in hex_string):
                return hex_string

            if len(hex_string) % 2 != 0 or len(hex_string) < 4 or len(hex_string) > 100:
                return hex_string

            decoded = bytes.fromhex(hex_string)
            # Try decodings in preference order
            for enc in ("utf-8", "utf-16le"):
                try:
                    readable = decoded.decode(enc)
                    cleaned = "".join(
                        c for c in readable if c.isprintable() and c != "\x00"
                    )
                    if cleaned and len(cleaned) >= 2 and cleaned.isascii():
                        return cleaned
                except UnicodeDecodeError:
                    continue

            return hex_string
        except Exception:
            return hex_string

    def _extract_mods_from_content(self, content: str) -> List[ModInfo]:
        """Extract mod entries from a profile.sii content string.

        Returns a list ordered by ETS2 load order (reverse index in profile).
        """
        mods: List[ModInfo] = []

        matches = _ACTIVE_MODS_RE.findall(content)
        if not matches:
            return mods

        # Sort by index descending to get load order (ETS2 uses reverse indexes)
        sorted_matches = sorted(matches, key=lambda x: int(x[0]), reverse=True)
        for index, mod_id, display_name in sorted_matches:
            clean_name = display_name.strip()
            if clean_name and len(clean_name) > 1:
                mods.append(
                    ModInfo(
                        display_name=clean_name,
                        filename=mod_id,
                        load_order=int(index),
                        source="profile",
                    )
                )
        return mods

    # -------------------------
    # Folder-based parsing (fallback)
    # -------------------------
    async def _parse_from_folder(self) -> List[ModInfo]:
        """Parse mods from the configured ETS2 mods folder."""
        if not hasattr(self.config, "ets2_mod_path"):
            return []

        mods_path = Path(self.config.ets2_mod_path)
        if not mods_path.exists():
            return []

        mods: List[ModInfo] = []
        try:
            scs_files = [
                f
                for f in mods_path.iterdir()
                if f.is_file() and f.suffix.lower() == ".scs"
            ]
            scs_files.sort()  # alphabetical fallback order
            for i, mod_file in enumerate(scs_files):
                display_name = await self._get_mod_display_name(mod_file)
                mods.append(
                    ModInfo(
                        display_name=display_name,
                        filename=mod_file.name,
                        load_order=i,
                        source="folder",
                    )
                )
        except Exception as exc:
            logging.error("ModParser: error parsing mod folder %s: %s", mods_path, exc)

        return mods

    async def _get_mod_display_name(self, mod_file: Path) -> str:
        """Resolve a human-readable name for a .scs mod file, using cache, manifest, or Steam lookup."""
        # Try cache first
        try:
            cached = await self.cache.get(mod_file.name)
            if cached:
                return cached
        except Exception:
            # If cache errors, continue gracefully
            logging.debug(
                "ModParser: cache.get failed for %s", mod_file.name, exc_info=True
            )

        # Try manifest.sii inside the SCS
        name = await self._extract_from_manifest(mod_file)
        if name:
            try:
                await self.cache.set(mod_file.name, name)
            except Exception:
                logging.debug(
                    "ModParser: cache.set failed for %s", mod_file.name, exc_info=True
                )
            return name

        # Try Steam Workshop lookup
        name = await self._lookup_steam_workshop(mod_file)
        if name:
            try:
                await self.cache.set(mod_file.name, name)
            except Exception:
                logging.debug(
                    "ModParser: cache.set failed for %s", mod_file.name, exc_info=True
                )
            return name

        # Fallback to cleaned filename
        cleaned = self._clean_filename(mod_file.name)
        try:
            await self.cache.set(mod_file.name, cleaned)
        except Exception:
            logging.debug(
                "ModParser: cache.set failed for %s", mod_file.name, exc_info=True
            )
        return cleaned

    async def _extract_from_manifest(self, mod_file: Path) -> Optional[str]:
        """Try to extract `mod_name` from `manifest.sii` inside the .scs archive."""
        try:
            with zipfile.ZipFile(mod_file, "r") as z:
                if "manifest.sii" in z.namelist():
                    with z.open("manifest.sii") as mf:
                        content = mf.read().decode("utf-8", errors="ignore")
                        match = re.search(r'mod_name:\s*"(.*?)"', content)
                        if match:
                            return match.group(1).strip()
        except Exception:
            # Non-fatal; manifest is optional
            logging.debug(
                "ModParser: failed to extract manifest from %s", mod_file, exc_info=True
            )
        return None

    async def _lookup_steam_workshop(self, mod_file: Path) -> Optional[str]:
        """Attempt to look up mod display name using Steam Workshop search (best-effort)."""
        cleaned_name = mod_file.stem.replace("_", " ").replace("-", " ").strip()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                params: Dict[str, str] = {
                    "appid": "227300",
                    "searchtext": cleaned_name,
                    "browsesort": "trend",
                    "section": "readytouseitems",
                }
                async with session.get(STEAM_WORKSHOP_URL, params=params) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        match = re.search(
                            r'<div class="workshopItemTitle">(.+?)</div>', text
                        )
                        if match:
                            return match.group(1).strip()
        except Exception:
            # Network/parse failures are fine; return None and fallback later
            logging.debug(
                "ModParser: steam lookup failed for %s", mod_file, exc_info=True
            )
        return None

    def _clean_filename(self, filename: str) -> str:
        """Produce a human-friendly name from a filename as a final fallback."""
        name = Path(filename).stem
        name = name.replace("_", " ").replace("-", " ")
        words = name.split()
        cleaned_words = []
        for word in words:
            lw = word.lower()
            if lw in {"v", "by", "for", "and", "the", "of", "to", "in", "on", "at"}:
                cleaned_words.append(lw)
            elif (
                word.startswith("v")
                and len(word) > 1
                and word[1:].replace(".", "").replace("_", "").isdigit()
            ):
                cleaned_words.append(word.upper())
            elif word.lower().startswith("promods"):
                # Heuristic handling of ProMods naming (best-effort)
                if "v" in word.lower():
                    parts = word.split("v")
                    if len(parts) > 1:
                        cleaned_words.append(
                            f"ProMods {parts[0][7:].title()} V{parts[1]}"
                        )
                    else:
                        cleaned_words.append(word.title())
                else:
                    cleaned_words.append(word.title())
            else:
                cleaned_words.append(word.title())
        return " ".join(cleaned_words)


__all__ = ["ModParser", "ModInfo", "ProfileInfo"]
