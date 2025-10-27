# Minimal, non-interactive setup.py for packaging
#
# Interactive configuration helper has been moved to:
#   `scripts/setup_config.py`
#
# This file now provides a conventional setuptools-based packaging entrypoint
# so build tools (pip, CI) can install the project without triggering prompts.

from pathlib import Path
from setuptools import setup, find_packages


def _read_requirements():
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        return []
    lines = req_file.read_text(encoding="utf-8").splitlines()
    reqs = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return reqs


long_description = ""
readme_path = Path(__file__).parent / "README.md"
if readme_path.exists():
    long_description = readme_path.read_text(encoding="utf-8")


setup(
    name="ets2-twitch-mod-bot",
    version="0.0.5",
    description="ETS2 Twitch Mod Bot - control ETS2 mods via Twitch chat",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=("tests", "docs", "artifacts")),
    include_package_data=True,
    install_requires=_read_requirements(),
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            # Interactive setup script was moved to scripts/setup_config.py
            # Use `ets2bot-setup` to invoke the interactive setup once that module exists.
            "ets2bot-setup = scripts.setup_config:run_setup_interactive",
        ]
    },
)
