"""
lib package

Convenience imports and public API re-exports for the refactored modules.

This module re-exports the primary classes and helpers from the submodules so
 callers can import from `lib` directly (e.g. `from lib import ModParser`).
"""

from .cache import ModCache, CACHE_FILE as CACHE_FILE  # type: ignore
from .parser import ModParser, ModInfo, ProfileInfo  # type: ignore
from .bot_core import (  # type: ignore
    BotConfig,
    CooldownManager,
    SingleInstanceLock,
    DLCDetector,
    ETS2ModBot,
    MAJOR_MAP_DLC,
    LOCK_FILE,
)
from .decrypt import (  # type: ignore
    SIIDecryptor,
    HAS_CRYPTO,
    SII_SIGNATURE_ENCRYPTED,
    SII_SIGNATURE_NORMAL,
)

__all__ = [
    # cache
    "ModCache",
    "CACHE_FILE",
    # parser
    "ModParser",
    "ModInfo",
    "ProfileInfo",
    # bot core
    "BotConfig",
    "CooldownManager",
    "SingleInstanceLock",
    "DLCDetector",
    "ETS2ModBot",
    "MAJOR_MAP_DLC",
    "LOCK_FILE",
    # decrypt
    "SIIDecryptor",
    "HAS_CRYPTO",
    "SII_SIGNATURE_ENCRYPTED",
    "SII_SIGNATURE_NORMAL",
]

# Package version (optional)
__version__ = "0.1.0"
