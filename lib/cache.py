# lib/cache.py
"""
Asynchronous ModCache implementation.

Provides a simple async-safe in-memory cache with persistence to disk.
This module is intended to replace the previous inline ModCache implementation
and centralize cache behavior for easier testing and reuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional

import aiofiles

CACHE_FILE = Path("cache.json")

__all__ = ["ModCache", "CACHE_FILE"]


class ModCache:
    """Async mod name cache with persistence.

    Usage:
        cache = ModCache()  # uses default CACHE_FILE
        await cache.load()
        await cache.set("foo.scs", "Foo Mod")
        name = await cache.get("foo.scs")
    """

    def __init__(self, cache_file: Path = CACHE_FILE) -> None:
        self.cache_file: Path = cache_file
        self._cache: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load cache from disk into memory.

        If the file doesn't exist this is a no-op. Any read/parsing errors
        will be logged and the in-memory cache will be reset.
        """
        if not self.cache_file.exists():
            logging.debug(
                "ModCache.load: cache file does not exist: %s", self.cache_file
            )
            return

        try:
            async with aiofiles.open(self.cache_file, "r", encoding="utf-8") as f:
                content = await f.read()
                # Defensive: if file is empty, keep empty dict
                if not content:
                    self._cache = {}
                else:
                    self._cache = json.loads(content)
            logging.info(
                "ModCache.load: loaded %d entries from %s",
                len(self._cache),
                self.cache_file,
            )
        except Exception as exc:
            logging.warning(
                "ModCache.load: failed to load cache %s: %s", self.cache_file, exc
            )
            self._cache = {}

    async def save(self) -> None:
        """Persist the in-memory cache to disk.

        Saves atomically by ensuring the parent directory exists. Errors are logged
        but not propagated (this keeps callers simple).
        """
        try:
            # Ensure parent directory exists (no-op if parent is current dir)
            if self.cache_file.parent:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(self.cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._cache, indent=2, ensure_ascii=False))
            logging.debug(
                "ModCache.save: saved %d entries to %s",
                len(self._cache),
                self.cache_file,
            )
        except Exception as exc:
            logging.error(
                "ModCache.save: failed to save cache to %s: %s", self.cache_file, exc
            )

    async def get(self, key: str) -> Optional[str]:
        """Get a value from the cache, or None if not present."""
        async with self._lock:
            return self._cache.get(key)

    async def set(self, key: str, value: str) -> None:
        """Set a value in the cache and persist to disk.

        This method acquires a lock to ensure concurrent callers don't conflict.
        """
        async with self._lock:
            self._cache[key] = value
            await self.save()

    async def clear(self) -> None:
        """Clear the in-memory cache and remove the cache file if present."""
        async with self._lock:
            self._cache.clear()
            try:
                if self.cache_file.exists():
                    self.cache_file.unlink()
                    logging.info(
                        "ModCache.clear: removed cache file %s", self.cache_file
                    )
            except Exception as exc:
                logging.warning(
                    "ModCache.clear: failed to remove cache file %s: %s",
                    self.cache_file,
                    exc,
                )

    # Utility methods useful for tests and debugging
    async def to_dict(self) -> Dict[str, str]:
        """Return a shallow copy of the cache dict (async-safe)."""
        async with self._lock:
            return dict(self._cache)

    async def __contains__(self, key: str) -> bool:
        async with self._lock:
            return key in self._cache
