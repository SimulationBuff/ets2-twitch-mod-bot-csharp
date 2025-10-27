"""
Interactive setup helper for ETS2 Twitch Mod Bot.

This module provides `run_setup_interactive()` which was extracted from the
original `setup.py` to avoid executing interactive prompts during packaging or
installation. It can be run directly as a script::

    python -m scripts.setup_config
    python scripts/setup_config.py

The function supports using environment variables or CLI flags to run
non-interactively (helpful for CI or automated provisioning):

- ETS2_TWITCH_TOKEN
- ETS2_TWITCH_CHANNEL
- ETS2_MOD_PATH
- ETS2_PROFILE_PATH
- ETS2_STEAM_PATH
- ETS2BOT_CONFIG_PATH    -> path to write config.json
- ETS2BOT_USE_DEFAULTS   -> if set to "1", accept defaults without prompting

CLI flags:
- --yes / -y           : Accept defaults for any missing values (equivalent to ETS2BOT_USE_DEFAULTS=1)
- --config PATH         : Write config to PATH (defaults to ./config.json)
- --non-interactive     : Do not prompt; require env vars or defaults

The resulting configuration file is a JSON with these keys:
- twitch_token
- twitch_channel
- ets2_mod_path
- ets2_profile_path
- ets2_steam_path
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Optional


DEFAULT_MOD_PATH = os.path.expanduser("~/Documents/Euro Truck Simulator 2/mod")
DEFAULT_PROFILE_PATH = os.path.expanduser("~/Documents/Euro Truck Simulator 2/profiles")
DEFAULT_STEAM_PATH = (
    "C:/Program Files (x86)/Steam/steamapps/common/Euro Truck Simulator 2"
)


def _prompt(prompt_text: str, default: Optional[str] = None) -> str:
    """Prompt the user with an optional default; return resulting string."""
    if default:
        prompt = f"{prompt_text} [{default}]: "
    else:
        prompt = f"{prompt_text}: "
    try:
        res = input(prompt).strip()
    except EOFError:
        # In non-interactive shells input() can raise EOFError; return default or empty
        return default or ""
    if res == "" and default is not None:
        return default
    return res


def _ensure_parent(path: Path) -> None:
    """Ensure parent directory exists for the target config file."""
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def gather_config(
    *,
    interactive: bool = True,
    use_defaults: bool = False,
    env_prefix: str = "",
) -> Dict[str, str]:
    """
    Gather configuration values.

    Priority:
      1. Environment variables (e.g., ETS2_TWITCH_TOKEN)
      2. Interactive prompt (if enabled)
      3. Defaults (if use_defaults True) or empty string

    env_prefix can be supplied to use different environment variable names; for
    the main flow leave it empty.
    """

    def env(name: str) -> Optional[str]:
        return os.environ.get(f"{env_prefix}{name}")

    cfg = {}

    # Twitch token and channel
    token = env("ETS2_TWITCH_TOKEN") or env("TWITCH_TOKEN") or env("twitch_token")
    channel = (
        env("ETS2_TWITCH_CHANNEL") or env("TWITCH_CHANNEL") or env("twitch_channel")
    )

    if not token:
        if interactive and not use_defaults:
            token = _prompt(
                "Enter your Twitch OAuth token (get from https://twitchtokengenerator.com/)"
            )
        elif use_defaults:
            token = ""
        else:
            token = ""
    if not channel:
        if interactive and not use_defaults:
            channel = _prompt("Enter your Twitch channel name")
        elif use_defaults:
            channel = ""
        else:
            channel = ""

    # Paths
    mod_path = env("ETS2_MOD_PATH") or env("ETS2_MOD_DIR") or DEFAULT_MOD_PATH
    profile_path = (
        env("ETS2_PROFILE_PATH") or env("ETS2_PROFILE_DIR") or DEFAULT_PROFILE_PATH
    )
    steam_path = env("ETS2_STEAM_PATH") or env("ETS2_STEAM_DIR") or DEFAULT_STEAM_PATH

    if interactive and not use_defaults:
        mod_path = _prompt("Enter ETS2 mod folder path", mod_path)
        profile_path = _prompt("Enter ETS2 profiles folder path", profile_path)
        steam_path = _prompt("Enter ETS2 Steam install path", steam_path)
    else:
        # If non-interactive and env provides overrides, the above picked them; otherwise keep defaults
        pass

    cfg["twitch_token"] = token
    cfg["twitch_channel"] = channel
    cfg["ets2_mod_path"] = str(Path(mod_path).expanduser())
    cfg["ets2_profile_path"] = str(Path(profile_path).expanduser())
    cfg["ets2_steam_path"] = str(Path(steam_path).expanduser())

    return cfg


def write_config(cfg: Dict[str, str], path: Path) -> None:
    """Write JSON config to the given path atomically (best-effort)."""
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        # Try atomic replace
        tmp.replace(path)
    except Exception:
        # Fallback to simple write
        path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


def run_setup_interactive(
    config_path: Optional[str] = None,
    non_interactive: bool = False,
    accept_defaults: bool = False,
) -> Path:
    """
    Main interactive entrypoint.

    Returns the path to the written config file.
    """
    # Determine target path
    env_cfg_path = os.environ.get("ETS2BOT_CONFIG_PATH") or os.environ.get(
        "CONFIG_PATH"
    )
    if config_path:
        target = Path(config_path)
    elif env_cfg_path:
        target = Path(env_cfg_path)
    else:
        target = Path("config.json")

    use_defaults_env = os.environ.get("ETS2BOT_USE_DEFAULTS", "")
    use_defaults = accept_defaults or (use_defaults_env == "1")

    interactive = not non_interactive and (os.isatty(0) or os.isatty(1))

    cfg = gather_config(interactive=interactive, use_defaults=use_defaults)

    print("\nConfiguration summary:")
    for k, v in cfg.items():
        display = v if v else "<empty>"
        print(f"  {k}: {display}")

    if interactive and not use_defaults:
        confirm = _prompt("Write configuration to '{}'? (y/n)".format(target), "y")
        if confirm.lower() not in ("y", "yes"):
            print("Aborting without writing configuration.")
            raise SystemExit(1)

    write_config(cfg, target)
    print(f"\nConfiguration written to: {target.resolve()}")
    return target


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Interactive setup for ETS2 Twitch Mod Bot")
    p.add_argument(
        "-y",
        "--yes",
        dest="yes",
        action="store_true",
        help="Accept defaults / do not prompt (equivalent to ETS2BOT_USE_DEFAULTS=1)",
    )
    p.add_argument(
        "--config",
        dest="config_path",
        help="Path to write configuration JSON (default: ./config.json)",
        default=None,
    )
    p.add_argument(
        "--non-interactive",
        dest="non_interactive",
        action="store_true",
        help="Do not prompt; require environment variables or use defaults",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        run_setup_interactive(
            config_path=args.config_path,
            non_interactive=args.non_interactive,
            accept_defaults=args.yes,
        )
        return 0
    except SystemExit as se:
        # Propagate explicit exit code if present
        code = int(se.code) if isinstance(se.code, int) else 1
        return code
    except Exception as exc:  # pragma: no cover - top-level script robustness
        print(f"Error during setup: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
