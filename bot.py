"""
Compatibility shim for the ETS2 Twitch Mod Bot.

This module provides a minimal, stable import surface for external code that
previously imported from `bot`. The implementation has been refactored into the
`lib` package; importing from this shim keeps older imports working while
encouraging new code to import explicitly from `lib.*`.

Example
-------
    from bot import ETS2ModBot, ModParser, ModCache
"""

from typing import List

# Re-export primary public symbols from the refactored package.
# Import explicit names to keep the shim lightweight and lint-friendly.
from lib.bot_core import (
    BotConfig,
    ETS2ModBot,
    CooldownManager,
    SingleInstanceLock,
    DLCDetector,
    MAJOR_MAP_DLC,
)
from lib.parser import ModParser, ModInfo, ProfileInfo
from lib.cache import ModCache, CACHE_FILE
from lib.decrypt import (
    SIIDecryptor,
    SII_KEY,
    SII_SIGNATURE_ENCRYPTED,
    SII_SIGNATURE_NORMAL,
    HAS_CRYPTO,
)

__all__: List[str] = [
    "BotConfig",
    "ETS2ModBot",
    "CooldownManager",
    "SingleInstanceLock",
    "DLCDetector",
    "MAJOR_MAP_DLC",
    "ModParser",
    "ModInfo",
    "ProfileInfo",
    "ModCache",
    "CACHE_FILE",
    "SIIDecryptor",
    "SII_KEY",
    "SII_SIGNATURE_ENCRYPTED",
    "SII_SIGNATURE_NORMAL",
    "HAS_CRYPTO",
]
