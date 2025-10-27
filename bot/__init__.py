"""
Compatibility package shim `bot`.

This package provides the legacy import surface that older code and the test
suite expect when doing `from bot import ...`. It re-exports the public symbols
from the refactored `lib` package so imports continue to work whether `bot` is
a module or a package.

The shim intentionally keeps a small surface and imports explicit names to
avoid surprising side-effects at import time.
"""

from typing import List

try:
    # Core bot and runtime objects
    from lib.bot_core import (
        BotConfig,
        ETS2ModBot,
        CooldownManager,
        SingleInstanceLock,
        DLCDetector,
        MAJOR_MAP_DLC,
    )

    # Parser-related objects
    from lib.parser import ModParser, ModInfo, ProfileInfo

    # Cache utilities
    from lib.cache import ModCache, CACHE_FILE

    # Decrypt utilities / constants
    from lib.decrypt import (
        SIIDecryptor,
        SII_KEY,
        SII_SIGNATURE_ENCRYPTED,
        SII_SIGNATURE_NORMAL,
        HAS_CRYPTO,
    )

except (
    Exception
) as _exc:  # pragma: no cover - this should surface immediately if lib is broken
    raise ImportError(
        "Failed to import internals for bot compatibility shim. Ensure the "
        "'lib' package is present and importable. Original error: %s" % (_exc,)
    ) from _exc


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
