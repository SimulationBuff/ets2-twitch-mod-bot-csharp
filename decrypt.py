"""
decrypt.py

SII decryption utilities for ETS2 Twitch Mod Bot.

Provides:
- SII_KEY, SII_SIGNATURE_NORMAL, SII_SIGNATURE_ENCRYPTED constants
- SIIDecryptor with async `decrypt_file` for reading plain or encrypted SII files

This module is intentionally lightweight and dependency-friendly:
- If `pycryptodome` is not available, the decryptor falls back to reading
  the file as text (best-effort).
"""

from pathlib import Path
from typing import Optional
import logging
import struct
import zlib

import aiofiles

# Optional Crypto dependency (pycryptodome)
try:
    from Crypto.Cipher import AES  # type: ignore

    HAS_CRYPTO = True
except Exception:  # pragma: no cover - optional dependency
    AES = None  # type: ignore
    HAS_CRYPTO = False

# SII decryption constants (copied from ETS2 community research)
SII_KEY = bytes(
    [
        0x2A,
        0x5F,
        0xCB,
        0x17,
        0x91,
        0xD2,
        0x2F,
        0xB6,
        0x02,
        0x45,
        0xB3,
        0xD8,
        0x36,
        0x9E,
        0xD0,
        0xB2,
        0xC2,
        0x73,
        0x71,
        0x56,
        0x3F,
        0xBF,
        0x1F,
        0x3C,
        0x9E,
        0xDF,
        0x6B,
        0x11,
        0x82,
        0x5A,
        0x5D,
        0x0A,
    ]
)

# Signatures for SII files
SII_SIGNATURE_ENCRYPTED = 0x43736353  # "ScsC" - encrypted header signature
SII_SIGNATURE_NORMAL = 0x4E696953  # "SiiN" - normal/plaintext signature

__all__ = [
    "SIIDecryptor",
    "HAS_CRYPTO",
    "SII_SIGNATURE_ENCRYPTED",
    "SII_SIGNATURE_NORMAL",
]


class SIIDecryptor:
    """Utility for decrypting ETS2 SII profile files.

    Usage:
        content = await SIIDecryptor.decrypt_file(Path("profile.sii"))
    Returns:
        A str with the file contents if readable/decrypted, otherwise None.
    """

    @staticmethod
    async def decrypt_file(file_path: Path) -> Optional[str]:
        """Decrypt an SII file if encrypted, otherwise return plain text.

        The function handles three cases:
        - If the optional crypto dependency is not available, attempt to read
          the file as UTF-8 text and return it (best-effort).
        - If the file begins with the plaintext signature, decode and return it.
        - If the file begins with the encrypted signature, attempt AES-CBC
          decryption followed by zlib decompression (where applicable).

        Returns None on unrecoverable errors or if the file doesn't appear to
        be a valid SII file.
        """
        # If Crypto dependency not installed, fallback to reading as text.
        if not HAS_CRYPTO:
            try:
                async with aiofiles.open(
                    file_path, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    return await f.read()
            except Exception:
                logging.debug("SIIDecryptor: fallback text read failed", exc_info=True)
                return None

        try:
            async with aiofiles.open(file_path, "rb") as f:
                data = await f.read()

            if not data or len(data) < 4:
                return None

            signature = struct.unpack("<I", data[:4])[0]

            if signature == SII_SIGNATURE_NORMAL:
                # Plaintext SII file
                return data.decode("utf-8", errors="ignore")

            if signature == SII_SIGNATURE_ENCRYPTED:
                return await SIIDecryptor._decrypt_encrypted(data)

            # Unknown signature
            logging.debug("SIIDecryptor: unknown signature for file %s", file_path)
            return None

        except Exception as exc:
            logging.error("SIIDecryptor: error decrypting %s: %s", file_path, exc)
            logging.debug("SIIDecryptor: exception details", exc_info=True)
            return None

    @staticmethod
    async def _decrypt_encrypted(data: bytes) -> Optional[str]:
        """Decrypt an encrypted SII blob.

        Expected format:
            4 bytes   -> signature (SII_SIGNATURE_ENCRYPTED)
            32 bytes  -> HMAC placeholder (ignored)
            16 bytes  -> IV
            4 bytes   -> data size (may be ignored)
            rest      -> AES-encrypted payload (AES-256-CBC)

        After AES decryption, the payload is often zlib-compressed. The method
        first tries to decompress; if that fails, it returns the decrypted
        bytes as UTF-8 text as a fallback.
        """
        # header size: 4 (sig) + 32 (hmac) + 16 (iv) + 4 (datasize)
        header_size = 4 + 32 + 16 + 4
        if len(data) < header_size:
            logging.debug("SIIDecryptor: encrypted data shorter than header size")
            return None

        try:
            iv = data[36:52]  # bytes 36..51 (16 bytes)
            # encrypted payload typically starts after the 56th byte (4+32+16+4)
            encrypted_payload = data[56:]

            # Create AES cipher (expecting a 32-byte key for AES-256)
            cipher = AES.new(SII_KEY, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted_payload)

            # Attempt zlib decompression (most common)
            try:
                decompressed = zlib.decompress(decrypted)
                return decompressed.decode("utf-8", errors="ignore")
            except zlib.error:
                # If not compressed, try interpreting decrypted bytes directly
                return decrypted.decode("utf-8", errors="ignore")

        except Exception as exc:
            logging.error("SIIDecryptor: failed to decrypt encrypted SII blob: %s", exc)
            logging.debug("SIIDecryptor: exception details", exc_info=True)
            return None
