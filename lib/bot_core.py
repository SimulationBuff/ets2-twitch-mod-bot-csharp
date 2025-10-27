"""
lib/bot_core.py

Core bot runtime classes and utilities extracted for modularity and testability.

Provides:
- CooldownManager: simple user/global cooldown handling
- SingleInstanceLock: async file-based single-instance lock with stale-lock cleanup
- DLCDetector: detect installed/active major map DLC from Steam folder and profile files
- ETS2ModBot: twitch.io based bot class wiring parser, cache, cooldowns, and DLC detector

This module is dependency-light and designed to be importable by a small shim `bot.py`
that re-exports the public API for backwards compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiofiles

# twitchio is an external dependency; keep import local so tests that don't use the bot
# can still import this module for utilities.
try:
    from twitchio.ext import commands
except Exception:  # pragma: no cover - environment dependent
    commands = None  # type: ignore

# Optional psutil dependency for checking running PIDs
try:
    import psutil

    HAS_PSUTIL = True
except Exception:  # pragma: no cover - optional dependency
    psutil = None  # type: ignore
    HAS_PSUTIL = False

# Local project modules (expected to exist after refactor)
try:
    from lib.cache import ModCache
except Exception:  # pragma: no cover - defensive import
    ModCache = None  # type: ignore

try:
    from lib.parser import ModParser, ModInfo
except Exception:  # pragma: no cover - defensive import
    ModParser = None  # type: ignore
    ModInfo = None  # type: ignore

# Major map DLC mapping (human-readable names)
MAJOR_MAP_DLC: Dict[str, str] = {
    "east": "Going East!",
    "north": "Scandinavia",
    "fr": "Vive la France!",
    "it": "Italia",
    "balt": "Beyond the Baltic Sea",
    "iberia": "Iberia",
    "balkan_w": "West Balkans",
    "greece": "Greece",
}

# Default file paths (relative to repo/working dir)
LOCK_FILE = Path("bot_instance.lock")
CACHE_FILE = Path("modcache.json")


@dataclass
class BotConfig:
    """Lightweight config dataclass used by the core bot.

    Keep this simple so callers/tests can create instances without external setup.
    """

    twitch_token: str
    twitch_channel: str
    ets2_mod_path: Path
    ets2_profile_path: Path
    ets2_steam_path: Path
    user_cooldown_seconds: int = 30
    refresh_global_seconds: int = 120


class CooldownManager:
    """Manage per-user and global cooldowns for commands."""

    def __init__(self, user_cooldown: int, global_cooldown: int) -> None:
        self.user_cooldown = user_cooldown
        self.global_cooldown = global_cooldown
        self.user_cooldowns: Dict[str, float] = {}
        self.global_cooldowns: Dict[str, float] = {}

    def check_cooldown(self, user: str, command: str) -> Tuple[bool, Optional[str]]:
        """Returns (ok, message). If ok is False, message contains the timeout text."""
        now = (
            asyncio.get_event_loop().time()
            if asyncio.get_event_loop().is_running()
            else __import__("time").time()
        )

        # User cooldown
        if user in self.user_cooldowns:
            elapsed = now - self.user_cooldowns[user]
            remaining = self.user_cooldown - elapsed
            if remaining > 0:
                return (
                    False,
                    f"⏰ Please wait {int(remaining)} seconds before using commands again.",
                )

        # Global cooldown for refresh commands
        if command == "refreshmods" and "refreshmods" in self.global_cooldowns:
            elapsed = now - self.global_cooldowns["refreshmods"]
            remaining = self.global_cooldown - elapsed
            if remaining > 0:
                return (
                    False,
                    f"⏰ Please wait {int(remaining)} seconds before refreshing mods again (global cooldown).",
                )

        # Update cooldowns
        self.user_cooldowns[user] = now
        if command == "refreshmods":
            self.global_cooldowns["refreshmods"] = now

        return True, None


class SingleInstanceLock:
    """Async single-instance lock using a filesystem lockfile.

    Behavior:
    - `acquire()` writes the current PID to the lock file after
      attempting to handle a stale or invalid existing lock.
    - If a PID is found and psutil reports the PID exists, exit(1) is invoked.
    - `release()` removes the lock file.
    """

    def __init__(self, lock_file: Path = LOCK_FILE) -> None:
        self.lock_file = lock_file

    async def acquire(self) -> None:
        """Acquire the lock, or exit if another running instance is detected."""
        # If file exists, attempt to inspect and handle it
        if self.lock_file.exists():
            await self._handle_existing_lock()

        # Write our PID (async) to the lock file
        try:
            async with aiofiles.open(self.lock_file, "w") as f:
                await f.write(str(os.getpid()))
            logging.info("🔒 Bot instance locked (PID: %d)", os.getpid())
        except Exception as exc:
            logging.error("Failed to create lock file %s: %s", self.lock_file, exc)
            raise

    async def _handle_existing_lock(self) -> None:
        """Read an existing lock file and decide what to do with it."""
        try:
            async with aiofiles.open(self.lock_file, "r") as f:
                raw = (await f.read()).strip()
            pid = int(raw)
            # If psutil indicates the pid exists, abort
            if HAS_PSUTIL and psutil.pid_exists(pid):
                logging.error("❌ Another bot instance is running (PID: %s)", pid)
                # Mirror previous behaviour: terminate with non-zero status
                sys.exit(1)
            else:
                logging.info("🧹 Cleaning up stale lock file (PID %s)", pid)
                try:
                    self.lock_file.unlink()
                except Exception:
                    logging.debug(
                        "Failed to unlink stale lock file; continuing", exc_info=True
                    )
        except ValueError:
            logging.info("🧹 Cleaning up invalid lock file (non-integer contents)")
            try:
                self.lock_file.unlink()
            except Exception:
                logging.debug(
                    "Failed to unlink invalid lock file; continuing", exc_info=True
                )
        except FileNotFoundError:
            # Race: file removed between exists() and here
            return
        except Exception as exc:
            logging.warning("Unexpected error handling existing lock file: %s", exc)

    def release(self) -> None:
        """Remove the lock file if present."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                logging.info("🧹 Lock file cleaned up")
        except Exception as exc:
            logging.warning("Failed to cleanup lock file: %s", exc)


class DLCDetector:
    """Detects installed and active major map DLC."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    async def get_active_dlc(self) -> List[str]:
        """Return list of active DLC (human-readable names)."""
        dlcs = set()

        # Scan Steam DLC folder
        try:
            if self.config.ets2_steam_path.exists():
                dlcs.update(await self._scan_steam_dlc())
        except Exception:
            logging.debug("DLCDetector: steam scan error", exc_info=True)

        # Scan profile files for activation flags
        try:
            dlcs.update(await self._scan_profile_dlc())
        except Exception:
            logging.debug("DLCDetector: profile scan error", exc_info=True)

        return sorted(dlcs)

    async def _scan_steam_dlc(self) -> List[str]:
        """Scan Steam path for dlc_*.scs files matching our mapping."""
        dlcs: List[str] = []
        try:
            for entry in self.config.ets2_steam_path.iterdir():
                if (
                    entry.is_file()
                    and entry.name.startswith("dlc_")
                    and entry.suffix == ".scs"
                ):
                    code = entry.stem[4:]  # remove 'dlc_' prefix
                    name = MAJOR_MAP_DLC.get(code)
                    if name:
                        dlcs.append(name)
        except Exception as exc:
            logging.error("DLCDetector: error scanning Steam DLC: %s", exc)
        return dlcs

    async def _scan_profile_dlc(self) -> List[str]:
        """Scan the most recent profile directory for config flags that indicate DLC activation."""
        dlcs: List[str] = []
        profile_root = Path(self.config.ets2_profile_path)
        if not profile_root.exists():
            return dlcs

        try:
            # We expect profile_root to contain profile directories; choose newest
            candidates = [p for p in profile_root.iterdir() if p.is_dir()]
            if not candidates:
                return dlcs
            latest = max(candidates, key=lambda p: p.stat().st_mtime)

            potential_files = ["profile.sii", "config.cfg", "config_local.cfg"]
            for fn in potential_files:
                p = latest / fn
                if p.exists():
                    try:
                        async with aiofiles.open(
                            p, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = await f.read()
                        dlcs.extend(self._parse_dlc_content(content))
                    except Exception:
                        logging.debug(
                            "DLCDetector: failed to read profile file %s",
                            p,
                            exc_info=True,
                        )
        except Exception as exc:
            logging.error("DLCDetector: error scanning profiles for DLC: %s", exc)
        # ensure uniqueness
        return list(set(dlcs))

    def _parse_dlc_content(self, content: str) -> List[str]:
        """Search content for patterns that indicate DLC activation."""
        found: List[str] = []
        for code, name in MAJOR_MAP_DLC.items():
            patterns = [
                rf"dlc_{re.escape(code)}\s*[:=]\s*[1-9]",
                rf"{re.escape(code)}\s*.*enabled",
                rf"{re.escape(code)}\s*.*active",
                rf'"{re.escape(code)}"',
            ]
            for pat in patterns:
                if re.search(pat, content, re.IGNORECASE):
                    found.append(name)
                    break
        return found


if commands is not None:

    class ETS2ModBot(commands.Bot):
        """Main Twitch bot class wiring together parser, cache, cooldowns and DLC detector.

        This class intentionally keeps side-effects minimal in __init__; network and I/O
        operations are performed in command handlers.
        """

        def __init__(self, config: BotConfig) -> None:
            # Ensure twitchio is available
            if commands is None:
                raise RuntimeError("twitchio not available in this environment")

            super().__init__(
                token=config.twitch_token,
                prefix="!",
                initial_channels=[config.twitch_channel],
            )
            self.config = config
            # Use provided cache if available, otherwise use default ModCache
            self.cache = ModCache() if ModCache is not None else None
            self.cooldown_manager = CooldownManager(
                config.user_cooldown_seconds, config.refresh_global_seconds
            )
            # Parser & DLC detector must be created with injected config/cache
            self.mod_parser = (
                ModParser(config, self.cache) if ModParser is not None else None
            )
            self.dlc_detector = DLCDetector(config)

            # instance lock for running single instance
            self.lock = SingleInstanceLock()

        async def event_ready(self) -> None:
            logging.info("✅ Bot ready! Connected to %s", self.config.twitch_channel)

        async def event_command_error(self, context, error) -> None:
            """Gracefully handle command errors and notify the invoking user if possible."""
            logging.error("Command error: %s", error)
            try:
                await context.send(
                    f"@{context.author.name}: ❌ An error occurred. Please try again later."
                )
            except Exception:
                # Swallow errors from error handling to avoid infinite loops
                logging.debug("Failed to send error message to context", exc_info=True)

        @commands.command(name="mods")
        async def mods_command(self, ctx) -> None:
            """Display active mods and DLC."""
            cooldown_ok, timeout_msg = self.cooldown_manager.check_cooldown(
                ctx.author.name, "mods"
            )
            if not cooldown_ok:
                await ctx.send(f"@{ctx.author.name}: {timeout_msg}")
                return

            try:
                mods = (
                    await self.mod_parser.get_active_mods() if self.mod_parser else []
                )
                dlcs = await self.dlc_detector.get_active_dlc()
                response = self._format_response(mods, dlcs)
                await self._send_chunked_message(ctx, response)
            except Exception as exc:
                logging.error("Error in mods command: %s", exc)
                try:
                    await ctx.send(
                        f"@{ctx.author.name}: ❌ Error retrieving mods. Check bot configuration."
                    )
                except Exception:
                    logging.debug(
                        "Failed to send error notice in mods_command", exc_info=True
                    )

        @commands.command(name="refreshmods")
        async def refresh_mods_command(self, ctx) -> None:
            """Refresh mod cache and inform user."""
            cooldown_ok, timeout_msg = self.cooldown_manager.check_cooldown(
                ctx.author.name, "refreshmods"
            )
            if not cooldown_ok:
                await ctx.send(f"@{ctx.author.name}: {timeout_msg}")
                return

            try:
                if self.cache:
                    await self.cache.clear()
                mods = (
                    await self.mod_parser.get_active_mods() if self.mod_parser else []
                )
                response = f"✅ Mod cache refreshed! Found {len(mods)} active mods. Use !mods to see the list."
                await ctx.send(f"@{ctx.author.name}: {response}")
            except Exception as exc:
                logging.error("Error in refresh command: %s", exc)
                try:
                    await ctx.send(f"@{ctx.author.name}: ❌ Error refreshing cache.")
                except Exception:
                    logging.debug(
                        "Failed to send error notice in refresh_mods_command",
                        exc_info=True,
                    )

        def _format_response(self, mods: List[ModInfo], dlcs: List[str]) -> str:
            """Format list of mods and dlcs into a compact chat-friendly string."""
            if not mods and not dlcs:
                return "❌ No mods or DLC detected! Check your ETS2 installation paths."

            parts: List[str] = []
            if mods:
                parts.append("🚛 MODS (Load Order - MUST MATCH FOR CONVOY): ")
                formatted_mods = []
                for i, mod in enumerate(mods, 1):
                    mod_name = (
                        (mod.display_name[:30] + "...")
                        if len(mod.display_name) > 33
                        else mod.display_name
                    )
                    formatted_mods.append(f"{i}.{mod_name}")
                parts.append(" | ".join(formatted_mods))
            else:
                parts.append("🚛 MODS: None detected")

            if dlcs:
                parts.append(f" || 🗺️ DLC: {', '.join(dlcs)}")
            else:
                parts.append(" || 🗺️ DLC: None detected")

            return "".join(parts)

        async def _send_chunked_message(
            self, ctx, message: str, limit: int = 500, delay: float = 0.5
        ) -> None:
            """Send long messages in chunks to avoid chat limits."""
            while message:
                chunk = message[:limit]
                # Try to split on sensible delimiters
                last_sep = max(chunk.rfind("|"), chunk.rfind(","), chunk.rfind(" "))
                if 0 < last_sep < len(chunk):
                    chunk = chunk[:last_sep]
                try:
                    await ctx.send(chunk.strip())
                except Exception:
                    logging.debug("Failed to send a message chunk", exc_info=True)
                message = message[len(chunk) :].strip()
                if message:
                    await asyncio.sleep(delay)

else:
    # Provide a fallback stub for environments without twitchio so imports don't fail.
    ETS2ModBot = None  # type: ignore


__all__ = [
    "BotConfig",
    "CooldownManager",
    "SingleInstanceLock",
    "DLCDetector",
    "ETS2ModBot",
    "MAJOR_MAP_DLC",
    "LOCK_FILE",
    "CACHE_FILE",
]
