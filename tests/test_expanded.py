import asyncio
import os
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from bot import (
    BotConfig,
    DLCDetector,
    ETS2ModBot,
    MAJOR_MAP_DLC,
    ModInfo,
    SIIDecryptor,
    SII_KEY,
    SII_SIGNATURE_ENCRYPTED,
    SII_SIGNATURE_NORMAL,
)


try:
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover - defensive import for test environments
    AES = None


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_sii_decryptor_plain_file(tmp_path):
    """Plain (non-encrypted) SII should be returned as text."""
    content = 'some setting: 1\nactive_mods[0]: "mod|Name"\n'
    # Build file with normal signature then content bytes
    data = struct.pack("<I", SII_SIGNATURE_NORMAL) + content.encode("utf-8")
    p = tmp_path / "profile.sii"
    _write_file(p, data)

    result = asyncio.run(SIIDecryptor.decrypt_file(p))
    assert result is not None
    assert "active_mods" in result
    assert "some setting" in result


@pytest.mark.skipif(AES is None, reason="pycryptodome not available")
def test_sii_decryptor_encrypted_file(tmp_path):
    """Create an encrypted SII-like blob and ensure decryptor recovers original text."""
    # original plaintext
    original_text = 'active_mods[0]: "mod_a|Alpha"\nactive_mods[1]: "mod_b|Bravo"\n'
    # compress first (as decryptor expects zlib-compressed after decrypt)
    compressed = zlib.compress(original_text.encode("utf-8"))

    # PKCS7 padding to AES block size (16)
    block_size = 16
    pad_len = block_size - (len(compressed) % block_size)
    padded = compressed + bytes([pad_len] * pad_len)

    # random IV
    iv = os.urandom(16)
    cipher = AES.new(SII_KEY, AES.MODE_CBC, iv)
    encrypted_payload = cipher.encrypt(padded)

    # build header: 4-byte signature + 32 bytes (HMAC placeholder) + 16-byte IV + 4-byte datasize + payload
    header = (
        struct.pack("<I", SII_SIGNATURE_ENCRYPTED)
        + (b"\x00" * 32)
        + iv
        + struct.pack("<I", len(encrypted_payload))
    )
    blob = header + encrypted_payload

    p = tmp_path / "enc_profile.sii"
    _write_file(p, blob)

    result = asyncio.run(SIIDecryptor.decrypt_file(p))
    assert result is not None
    assert "Alpha" in result
    assert "Bravo" in result


def test_dlc_detector_with_steam_files_and_profiles(tmp_path):
    """DLCDetector should detect DLCs from steam folder files and profile config files."""
    # Create fake steam DLC files
    steam_dir = tmp_path / "steam"
    steam_dir.mkdir()
    # pick a couple of known keys from MAJOR_MAP_DLC
    keys = list(MAJOR_MAP_DLC.keys())[:2]
    for k in keys:
        (steam_dir / f"dlc_{k}.scs").write_text("dummy")

    # Create profiles directory with a profile that contains dlc activation in config
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    profile_folder = profiles_root / "profile1"
    profile_folder.mkdir()
    config_file = profile_folder / "profile.sii"
    # include pattern like dlc_east: 1 and "north" in quotes
    txt = 'dlc_%s: 1\nsome_other_setting: 0\n"%s"\n' % (keys[0], keys[1])
    config_file.write_text(txt)

    cfg = BotConfig(
        twitch_token="t",
        twitch_channel="c",
        ets2_mod_path=tmp_path,
        ets2_profile_path=profiles_root,
        ets2_steam_path=steam_dir,
    )

    detector = DLCDetector(cfg)
    dlcs = asyncio.run(detector.get_active_dlc())

    # Should include the human-readable names from MAJOR_MAP_DLC for both keys
    expected = {MAJOR_MAP_DLC[k] for k in keys}
    assert expected.issubset(set(dlcs))


def test_ets2modbot_format_and_chunking_behavior():
    """Test response formatting and chunked message sending logic."""
    cfg = BotConfig(
        twitch_token="t",
        twitch_channel="c",
        ets2_mod_path=Path("."),
        ets2_profile_path=Path("."),
        ets2_steam_path=Path("."),
    )
    bot = ETS2ModBot(cfg)

    # Test empty response
    empty = bot._format_response([], [])
    assert "No mods or DLC detected" in empty

    # Test formatting with many mods and dlcs, including truncation of long names
    mods = [
        ModInfo(display_name="ShortName", filename="a.scs", load_order=0),
        ModInfo(
            display_name="This Is A Very Long Mod Name That Should Be Truncated In Output",
            filename="b.scs",
            load_order=1,
        ),
    ]
    dlcs = ["Going East!", "Scandinavia"]
    resp = bot._format_response(mods, dlcs)
    # ensure numbering and truncation marker present
    assert "1.ShortName" in resp
    assert "2." in resp
    assert "..." in resp
    assert "DLC" in resp

    # Test _send_chunked_message behavior by capturing messages
    sent = []

    class Ctx:
        async def send(self, message: str):
            sent.append(message)

    # Create a message that will be split: include separators so chunking uses them
    long_message = (
        " | ".join([f"item{i}" for i in range(20)]) + " || " + ", ".join(dlcs)
    )
    # run the async method with a small limit to force multiple chunks and no delay
    asyncio.run(bot._send_chunked_message(Ctx(), long_message, limit=50, delay=0.0))

    # Ensure more than one chunk was sent and that concatenating them yields original content (ignoring whitespace)
    assert len(sent) >= 2
    reconstructed = " ".join(m.strip() for m in sent)
    for part in ["item0", "item19", "Going East!", "Scandinavia"]:
        assert part in reconstructed
