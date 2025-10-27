# Changelog

All notable changes to this project will be documented in this file.

The format is based on "Keep a Changelog" (https://keepachangelog.com/en/1.0.0/)
and follows Semantic Versioning (https://semver.org/).

## [v0.0.5] - 2025-10-27

### Highlights
- Major internal refactor: the large monolithic `bot.py` has been split into a small package under `lib/` plus a compatibility shim `bot.py`.
  - New modules: `lib/decrypt.py`, `lib/cache.py`, `lib/parser.py`, `lib/bot_core.py`.
  - A package initializer `lib/__init__.py` re-exports the public API.
  - `bot.py` now acts as a compatibility shim that re-exports the public symbols, preserving the existing import surface for downstream code.
- Added comprehensive test coverage with multiple new test files:
  - `tests/test_core.py`
  - `tests/test_expanded.py`
  - `tests/test_more_coverage.py`
- CI and quality tooling:
  - GitHub Actions workflow `.github/workflows/ci.yml` to run linting, formatting checks, pre-commit, and tests across Python versions.
  - Pre-commit configuration `.pre-commit-config.yaml` to run `black`, `ruff`, and useful hygiene hooks on commit.
- Formatting & lint fixes applied across the repository (`ruff --fix` + `black`).
- Coverage improved (local run) to ~72% overall, with `bot.py` (refactored) covered substantially more than before.

### Release body / description
This release is primarily focused on maintainability, testability, and developer experience:

- The project architecture is modularized: logical concerns have been separated into their own modules so each piece can be developed, tested, and reasoned about independently.
  - `lib/decrypt.py`: SII decryption utilities (handles plain & encrypted SII files; uses optional crypto dependency).
  - `lib/cache.py`: Async-safe `ModCache` implementation with persistence and simple API (`load`, `save`, `get`, `set`, `clear`).
  - `lib/parser.py`: `ModParser` and dataclasses for parsing active mods from ETS2 profiles and from mod folders, with manifest extraction and Steam workshop lookup logic (network calls are `aiohttp`-based and easily mocked).
  - `lib/bot_core.py`: Core runtime components — `BotConfig`, `CooldownManager`, `SingleInstanceLock`, `DLCDetector`, and `ETS2ModBot` (the Twitch bot wiring).
- A compatibility shim (`bot.py`) re-exports the public symbols so user code that imports `bot` will continue to work.
- Tests were added to cover core logic, edge cases, and integration-style behaviors (mocked where appropriate).
- CI was added and hardened (UTF-8 environment, test extras installed, test retries to reduce transient flakes).
- Pre-commit hooks and instructions were added to README to make it easier for contributors to maintain consistent style locally.
- A release tag `v0.0.5` was created and the GitHub release published.

### Notable fixes & improvements
- Improved error handling and logging in multiple modules.
- Single-instance locking logic made more robust (stale-lock cleanup, psutil-aware PID checks).
- Mod name resolution pipeline more testable (cache → manifest → workshop → filename cleaning).
- Message chunking and command cooldown logic consolidated and tested.

### Breaking changes
- Internals have been moved into `lib/` package. There is a compatibility shim (`bot.py`) that re-exports the public API to avoid breaking external code. If you import deeply from the old structure (e.g., relying on module internals), consider updating imports to the new layout:
  - Preferred imports:
    - `from lib import ModParser, ModInfo`
    - `from lib.bot_core import ETS2ModBot, BotConfig`
    - `from lib.decrypt import SIIDecryptor`
  - The previous top-level imports (e.g., `from bot import ModParser`) remain supported via the shim for this release, but future releases may encourage direct package imports.

### Migration / Upgrade notes
- If you previously imported from `bot` (e.g., `from bot import ModParser`), no immediate change is required thanks to the shim. For new code, prefer explicit imports from `lib.*` (e.g., `from lib.parser import ModParser`) for clarity and discoverability.
- If you run CI locally, install the development tooling:
  - pip dev dependencies: `pip install -r requirements.txt` (project deps)
  - dev tools: `pip install pre-commit ruff black pytest pytest-asyncio pycryptodome` (if you want encrypted SII tests to run)
  - Activate pre-commit: `pre-commit install`
  - Run hooks locally: `pre-commit run --all-files`
- To run the test suite locally (in the project's venv):
  - `./venv/bin/python -m pytest -q --disable-warnings`
- To measure coverage locally:
  - `./venv/bin/python -m pip install coverage pytest-cov`
  - `./venv/bin/python -m pytest --cov=.`
  - `./venv/bin/python -m coverage report -m`

### Detailed changes (summary of high-level diffs)
- Added modules:
  - `lib/decrypt.py` — moved SII logic into package
  - `lib/cache.py` — extracted `ModCache`
  - `lib/parser.py` — extracted `ModParser` and dataclasses
  - `lib/bot_core.py` — core runtime classes, `ETS2ModBot`
  - `lib/__init__.py` — package initializer / re-exports
- Modified:
  - `bot.py` — now a compatibility shim exporting the public API
  - `README.md` — added pre-commit and development instructions
  - `.github/workflows/ci.yml` — CI workflow (lint, black, pre-commit, pytest)
  - `.pre-commit-config.yaml` — pre-commit configuration
  - `requirements.txt` — ensured runtime dependencies are present and optional test/devel deps noted
- Added tests:
  - `tests/test_core.py`
  - `tests/test_expanded.py`
  - `tests/test_more_coverage.py`

### Release assets
Attach the following artifacts to the release for convenience (available on the release page):
- Source archives (auto-provided by GitHub):
  - `Source code (zip)` — autogenerated
  - `Source code (tar.gz)` — autogenerated
- Suggested additional assets (to attach manually if you want):
  - `ets2-twitch-mod-bot-v0.0.5.tar.gz` — sdist of the repo
  - `ets2_twitch_mod_bot-0.0.5-py3-none-any.whl` — wheel (if you build a wheel)
  - `windows-build-v0.0.5.zip` — platform-specific binary builds (if produced)
  - `docs/OPTIMIZATION_REPORT.md` — already in repo; consider attaching a PDF export for release notes

(If you want me to build and attach wheels/sdist here, I can prepare them locally and upload them to the release if you give the go-ahead.)

### Contributors
- Primary author / committer for this release: SimulationBuff
- Contributors: (automatically populated on GitHub from commit history)

### Security
- No security-related changes included in this release.
- If you discover any security issue, please open a private issue or contact maintainers directly — do not publish exploits publicly.

---

For more details, check the repository commit history and the files added in this release. If you'd like, I can:
- Produce a more detailed per-file changelog (list of commits/files touched),
- Build distribution artifacts (sdist/wheel) and attach them to this release,
- Create a backport PR or bump version in `setup.py`/packaging metadata.
