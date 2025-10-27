import asyncio
import os
import struct
import time
import zlib
import zipfile
from pathlib import Path

import pytest

from bot import (
    BotConfig,
    CooldownManager,
    DLCDetector,
    ETS2ModBot,
    MAJOR_MAP_DLC,
    ModInfo,
    ModCache,
    ModParser,
    SIIDecryptor,
    SII_KEY,
    SII_SIGNATURE_ENCRYPTED,
    SII_SIGNATURE_NORMAL,
    SingleInstanceLock,
)


# ----------------------------
# Helpers used across tests
# ----------------------------
def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_scs_with_manifest(path: Path, manifest_name: str):
    """
    Create a simple .scs zip file with a manifest.sii that contains a mod_name entry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        content = f'mod_name: "{manifest_name}"\n'
        zf.writestr("manifest.sii", content)


# ----------------------------
# Core (original tests/test_core.py)
# ----------------------------
def test_cooldown_manager_user_and_global():
    cm = CooldownManager(user_cooldown=1, global_cooldown=1)

    ok, msg = cm.check_cooldown("tester", "mods")
    assert ok and msg is None

    # Immediately calling again should fail due to user cooldown
    ok2, msg2 = cm.check_cooldown("tester", "mods")
    assert not ok2
    assert "Please wait" in msg2

    # Wait for cooldown to expire
    time.sleep(1.1)

    ok3, msg3 = cm.check_cooldown("tester", "mods")
    assert ok3 and msg3 is None

    # Test global cooldown for refreshmods
    # Use a different user to avoid hitting the user cooldown from earlier calls
    okg, _ = cm.check_cooldown("fresh_user", "refreshmods")
    assert okg

    okg2, mg2 = cm.check_cooldown("other", "refreshmods")
    # Should be blocked by global cooldown
    assert not okg2
    assert "Please wait" in mg2

    time.sleep(1.1)
    okg3, _ = cm.check_cooldown("other", "refreshmods")
    assert okg3


def test_hex_and_filename_and_extract_mods(tmp_path):
    # Minimal BotConfig for parser instantiation
    cfg = BotConfig(
        twitch_token="t",
        twitch_channel="c",
        ets2_mod_path=Path("."),
        ets2_profile_path=Path("."),
        ets2_steam_path=Path("."),
    )

    cache_file = tmp_path / "cache.json"
    cache = ModCache(cache_file)
    parser = ModParser(cfg, cache)

    # hex to readable name ("John")
    hex_name = "4a6f686e"
    assert parser._hex_to_readable_name(hex_name) == "John"

    # clean filename
    cleaned = parser._clean_filename("cool_mod_v1.2_by_author.scs")
    assert cleaned == "Cool Mod V1.2 by Author"

    # extract mods from profile content
    content = "\n".join(
        [
            'active_mods[0]: "mod_a|Alpha Mod"',
            'active_mods[2]: "mod_c|Charlie"',
            'active_mods[1]: "mod_b|Bravo"',
        ]
    )

    mods = parser._extract_mods_from_content(content)
    # Should be sorted reverse by index (2,1,0)
    assert [m.filename for m in mods] == ["mod_c", "mod_b", "mod_a"]
    assert [m.display_name for m in mods] == ["Charlie", "Bravo", "Alpha Mod"]


def test_modcache_set_get_save_load(tmp_path):
    cache_path = tmp_path / "modcache.json"
    cache = ModCache(cache_path)

    # load should not fail on missing file
    asyncio.run(cache.load())

    # set and get
    asyncio.run(cache.set("foo.scs", "Foo Mod"))
    val = asyncio.run(cache.get("foo.scs"))
    assert val == "Foo Mod"

    # Ensure persistence
    new_cache = ModCache(cache_path)
    asyncio.run(new_cache.load())
    assert asyncio.run(new_cache.get("foo.scs")) == "Foo Mod"


# ----------------------------
# Expanded tests (original tests/test_expanded.py)
# ----------------------------
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


try:
    from Crypto.Cipher import AES  # pragma: no cover - conditional on pycryptodome
except Exception:  # pragma: no cover - defensive import for test environments
    AES = None


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
    # Ensure an event loop is available for twitchio Bot initialization
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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


# ----------------------------
# Additional coverage tests (original tests/test_more_coverage.py)
# ----------------------------
def test_single_instance_lock_cleanup_and_release(tmp_path):
    """
    Ensure that SingleInstanceLock handles an invalid/stale lock file by cleaning it up,
    then creates a new lock file on acquire and removes it on release.
    """
    lock_file = tmp_path / "bot.lock"

    # Create a lock file containing invalid data (non-integer) so _handle_existing_lock
    # goes through the ValueError path and unlinks it.
    lock_file.write_text("not_a_pid")

    lock = SingleInstanceLock(lock_file=lock_file)

    # Acquire should clean up the invalid lock file and create a new one
    asyncio.run(lock.acquire())

    assert lock_file.exists(), "Lock file should be recreated on acquire"
    content = lock_file.read_text().strip()
    assert content.isdigit(), "Lock file should contain a PID"
    assert int(content) == os.getpid(), "Lock file should contain current process PID"

    # Release should remove the lock file
    lock.release()
    assert not lock_file.exists(), "Lock file should be removed on release"


def test_single_instance_lock_existing_running_pid_triggers_exit(tmp_path, monkeypatch):
    """
    If the lock file contains a numeric PID and that PID exists (psutil.pid_exists True),
    the lock acquire flow should call sys.exit(1). We assert SystemExit is raised.
    """
    lock_file = tmp_path / "bot.lock"
    # Put the number 12345 into the lock file
    lock_file.write_text("12345")

    lock = SingleInstanceLock(lock_file=lock_file)

    # Monkeypatch psutil.pid_exists to return True and ensure HAS_PSUTIL behaves as if psutil exists.
    try:
        import psutil

        monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    except Exception:
        # If psutil not installed in the environment, skip this part
        pytest.skip("psutil not installed; skipping running-PID exit check")

    # Acquire should call sys.exit(1) which raises SystemExit in Python
    with pytest.raises(SystemExit):
        asyncio.run(lock.acquire())


def test_sii_decryptor_handles_short_file(tmp_path):
    """
    Files shorter than the expected header size should gracefully return None.
    """
    p = tmp_path / "short.sii"
    # Write fewer than 4 bytes
    p.write_bytes(b"\x00\x01")

    result = asyncio.run(SIIDecryptor.decrypt_file(p))
    assert result is None, "Short/non-sensical SII files should return None"


def test_modparser_folder_parsing_and_cache_and_workshop_lookup(tmp_path, monkeypatch):
    """
    Test ModParser._parse_from_folder with:
    - an scs file that contains a manifest.sii -> name extracted
    - an scs file without manifest -> falls back to steam lookup (mocked)
    Also asserts ModCache persistence behavior via _get_mod_display_name.
    """
    # Setup configuration
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    cfg = BotConfig(
        twitch_token="x",
        twitch_channel="y",
        ets2_mod_path=mods_dir,
        ets2_profile_path=tmp_path / "profiles",
        ets2_steam_path=tmp_path / "steam",
    )

    # Create two scs files: one with manifest, one without
    with_manifest = mods_dir / "with_manifest.scs"
    no_manifest = mods_dir / "no_manifest.scs"

    _make_scs_with_manifest(with_manifest, "Manifested Mod")
    # create an empty zip (no manifest)
    with zipfile.ZipFile(no_manifest, "w") as zf:
        zf.writestr("somefile.txt", "data")

    cache_file = tmp_path / "cache.json"
    cache = ModCache(cache_file)

    parser = ModParser(cfg, cache)

    # Mock the steam lookup for files without manifest to avoid network calls
    async def fake_lookup(mod_file):
        return "Workshop Resolved Name"

    monkeypatch.setattr(parser, "_lookup_steam_workshop", fake_lookup)

    # Parse from folder (this should call our mocked _lookup_steam_workshop)
    result = asyncio.run(parser._parse_from_folder())

    # We expect both files returned, order is alphabetical by filename
    filenames = [m.filename for m in result]
    assert "no_manifest.scs" in filenames
    assert "with_manifest.scs" in filenames

    # Ensure display names are as expected (manifested name and workshop name)
    name_map = {m.filename: m.display_name for m in result}
    assert name_map["with_manifest.scs"] == "Manifested Mod"
    assert name_map["no_manifest.scs"] == "Workshop Resolved Name"

    # Also verify cache persisted the lookup result for the no-manifest file
    # Create a fresh ModCache pointing to same file and load it
    new_cache = ModCache(cache_file)
    asyncio.run(new_cache.load())
    cached = asyncio.run(new_cache.get("no_manifest.scs"))
    assert cached == "Workshop Resolved Name"


def test_ets2modbot_event_command_error_sends_message(tmp_path):
    """
    Ensure ETS2ModBot.event_command_error does not raise and attempts to notify the chat.
    """
    cfg = BotConfig(
        twitch_token="x",
        twitch_channel="y",
        ets2_mod_path=tmp_path / "mods",
        ets2_profile_path=tmp_path / "profiles",
        ets2_steam_path=tmp_path / "steam",
    )

    # Ensure an event loop exists for twitchio Bot initialization
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    bot = ETS2ModBot(cfg)

    # Create a fake context with an author and a send coroutine that records messages
    recorded = []

    class FakeAuthor:
        def __init__(self, name):
            self.name = name

    class FakeCtx:
        def __init__(self):
            self.author = FakeAuthor("tester")

        async def send(self, message):
            recorded.append(message)

    # Call the handler with an arbitrary exception
    asyncio.run(bot.event_command_error(FakeCtx(), Exception("boom")))

    # Ensure a message was sent and contains the author's name and an error indicator
    assert any("tester" in m and "error" in m.lower() for m in recorded)
