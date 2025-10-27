import asyncio
import time
from pathlib import Path

import pytest

from bot import CooldownManager, ModCache, ModParser, BotConfig


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
    okg, _ = cm.check_cooldown("tester", "refreshmods")
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
