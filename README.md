# ETS2 Twitch Mod Bot

A Twitch chat bot that responds to `!mods` with your ETS2 mod list (correct load order) + installed DLC. Perfect for convoy setups.

## Setup

1. Install Python 3.9+
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the setup wizard:

```bash
python setup.py
```

4. Start the bot:

```bash
python bot.py
```

## Twitch OAuth Token

Use a Twitch Token Generator to create a token. Scopes needed:

- `chat:read`
- `chat:edit`

## Development

This repository includes a pre-commit configuration that runs formatting and linting tools (Black and Ruff) as well as some basic hygiene hooks. To use these hooks locally and ensure your commits meet the project's style and linting rules, follow the steps below.

1. Install pre-commit (add it to your dev dependencies if desired):

```bash
pip install pre-commit
```

2. Install the git hooks into your local repo:

```bash
pre-commit install
```

3. Run all hooks against the repository (useful before opening a PR):

```bash
pre-commit run --all-files
```

4. Run specific hooks if you prefer (examples):

- Run Black across the repo:
```bash
pre-commit run black --all-files
```

- Run Ruff (lint and auto-fix where configured):
```bash
pre-commit run ruff --all-files
```

Notes:
- The `.pre-commit-config.yaml` in the repository configures which hooks run and whether any should automatically fix issues (e.g., Black formats files; Ruff may be configured with `--fix`).
- CI runs pre-commit checks as part of the build. There's an optional CI workflow input (`auto_format`) that, if enabled by a maintainer, will apply pre-commit fixes in CI and push them back to the branch.
- It's recommended to run `pre-commit run --all-files` locally before creating a PR to avoid style/lint failures in CI.
