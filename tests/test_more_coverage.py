import asyncio
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import (
    BotConfig,
    ModCache,
    ModParser,
    SIIDecryptor,
    SingleInstanceLock,
    ETS2ModBot,
    ModInfo,
)


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
    # The code references psutil directly; monkeypatching the function suffices.
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


def _make_scs_with_manifest(path: Path, manifest_name: str):
    """
    Create a simple .scs zip file with a manifest.sii that contains a mod_name entry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        content = f'mod_name: "{manifest_name}"\n'
        zf.writestr("manifest.sii", content)


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
