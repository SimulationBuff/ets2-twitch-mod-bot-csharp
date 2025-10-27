# ETS2 Twitch Mod Bot (C#/.NET 8)

Note: This repository now hosts the C#/.NET rewrite of the ETS2 Twitch Mod Bot. The original Python project remains available at:
https://github.com/SimulationBuff/ets2-twitch-mod-bot

The extracted C# history from this repository has been preserved in the branch `preserve-history/main` on the C# remote. If you need to review the original subtree history, see the branch:
https://github.com/SimulationBuff/ets2-twitch-mod-bot-csharp/tree/preserve-history/main

This README describes how to build, test, run and publish the C# solution.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Building](#building)
- [Running the application](#running-the-application)
- [Running tests](#running-tests)
- [Publishing a Windows single-file executable](#publishing-a-windows-single-file-executable)
- [Configuration](#configuration)
- [Twitch integration](#twitch-integration)
- [CI / GitHub Actions](#ci--github-actions)
- [Design & Porting notes](#design--porting-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Prerequisites

- .NET SDK 8.x (download and install from https://dotnet.microsoft.com/)
  - Ensure `dotnet --version` reports a 8.x SDK.
- Windows (for WPF application) — the core library and tests are cross-platform, but the UI is WPF.
- Visual Studio 2022/2023 or Visual Studio Code with C# extensions (recommended for dev).
- (Optional) GitHub CLI or Git credentials if you plan to push/create the repository remotely.

---

## Repository layout

Top-level layout:

- `ETS2TwitchModBot.sln` — solution file
- `src/ETS2TwitchModBot.Core/` — core library (decryption, parser, cache, DLC detect, bot logic)
- `src/ETS2TwitchModBot.App/` — WPF application that hosts the bot and provides a simple UI
- `tests/ETS2TwitchModBot.Tests/` — xUnit unit tests for core logic
- `.github/workflows/` — CI workflows (Windows build, tests and publish)
- `README.md` — this file
- `CHANGELOG.md` — project changelog

---

## Getting started

1. Clone the repository:

   ```
   git clone <your-repo-url>
   cd ets2-twitch-mod-bot-csharp
   ```

2. Restore dependencies:

   ```
   dotnet restore
   ```

3. Open the solution:
   - In Visual Studio: open `ETS2TwitchModBot.sln`.
   - In VS Code: open the workspace folder and allow the C# extension to initialize.

---

## Building

To build the entire solution from the command line:

```
dotnet build ETS2TwitchModBot.sln -c Release
```

This builds the core library, the WPF app, and the tests.

---

## Running the application

Note: The WPF app runs on Windows only.

From Visual Studio:
- Set `ETS2TwitchModBot.App` as the startup project, and run (F5) or Debug > Start Debugging.

From the command line (Windows):

```
cd src/ETS2TwitchModBot.App
dotnet run -c Release
```

The WPF UI provides:
- A Start/Stop bot button.
- A basic log view for info/warn/error messages.
- A panel showing last detected mods and DLC.

The bot reads configuration from `appsettings.json` by default and supports environment variable overrides (see [Configuration](#configuration)).

---

## Running tests

Run unit tests with:

```
dotnet test ETS2TwitchModBot.sln -c Release
```

The test project uses xUnit. Unit tests cover parsing, decryption, cache persistence, cooldown logic, and select bot core functionality.

---

## Publishing a Windows single-file executable

The CI is configured to publish a Windows single-file exe (self-contained). You can also do this locally:

```
dotnet publish src/ETS2TwitchModBot.App/ETS2TwitchModBot.App.csproj -c Release -r win-x64 /p:PublishSingleFile=true /p:PublishTrimmed=false --self-contained true -o ./artifacts/publish/win-x64
```

Notes:
- `PublishTrimmed=true` can reduce artifact size but risks trimming reflection-based code. Use with caution.
- For a fully self-contained exe set runtime identifier (`-r win-x64`) and `--self-contained true`.

---

## Configuration

The application uses `appsettings.json` (and `appsettings.Development.json` for dev overrides) for configuration. Important configuration sections:

- `Twitch`:
  - `ClientId` - Twitch application/client ID.
  - `OAuthToken` - Twitch OAuth token (bot account). Prefer storing tokens securely (see Security).
  - `Channel` - Target Twitch channel name.

- `Paths`:
  - `Ets2ModPath` - ETS2 mod folder path.
  - `Ets2ProfilePath` - ETS2 profiles path.
  - `Ets2SteamPath` - Steam install path (for DLC detection).

- `Cooldowns`:
  - `UserCooldownSeconds` - per-user cooldown.
  - `GlobalCooldownSeconds` - global cooldown per-command.

Environment variables can override appsettings. Example environment variables:
- `TWITCH__OAUTHTOKEN` overrides `Twitch:OAuthToken`
- `PATHS__ETS2MODPATH` overrides `Paths:Ets2ModPath`

For local development you can place a `appsettings.Development.json` in the `src/ETS2TwitchModBot.App` folder (not recommended to commit secrets).

---

## Twitch integration

This project uses `TwitchLib` (recommended C# Twitch client library) to integrate with Twitch chat and commands. Typical flow:

1. Provide a valid OAuth token for a Twitch bot account (with chat:read/chat:edit as required).
2. The bot logs in using `TwitchClient` and joins the configured channel.
3. Commands from chat are processed and route to core logic (cooldown checks, formatting).
4. Bot responds in chat or via whisper (configurable).

Security: Do not commit tokens to the repository. Use environment variables or GitHub Secrets for CI publishing.

---

## CI / GitHub Actions

A workflow is included to:
- Build the solution on a Windows runner.
- Run unit tests with `dotnet test`.
- Publish a Release build via `dotnet publish` producing a Windows single-file executable.
- Attach the published artifact to GitHub Releases automatically (on tag/release).

You can find the workflow under `.github/workflows/dotnet-windows.yml`. The workflow requires repository secrets for any publishing/upload tokens if you integrate additional services.

---

## Design & Porting notes

This C# rewrite aims for full parity with the original Python bot:

- Decryptor: Implements SII encrypted/plaintext handling (AES-256-CBC with zlib decompression fallback).
- Parser: Parses ETS2 profile SII files and `manifest.sii` inside `.scs` archives to extract `mod_name` and `active_mods` lists.
- Cache: JSON file-backed `ModCache` for mapping filenames to display names and avoiding repeated lookups.
- DLC detection: Scans Steam folder and profile `profile.sii` files to detect enabled DLCs.
- Cooldowns & Rate-limits: Per-user and global cooldowns implemented as a `CooldownManager`.
- Single-instance: A cross-process single-instance lock (file-based with PID) implemented for Windows.
- Bot core & UI: Twitch command handling via `TwitchLib` and a WPF UI for runtime control and logging.

Coding style:
- The core logic is implemented as a library project so the UI and any future CLI/service targets can reuse it.
- Dependency Injection (Microsoft.Extensions.DependencyInjection) is used to wire services for testability.

---

## Contributing

Contributions are welcome. Recommended workflow:

1. Fork the repository and create a feature branch.
2. Implement changes and add or update unit tests.
3. Run `dotnet test` locally and ensure the solution builds.
4. Open a pull request against the upstream `main` branch. CI will run and validate.

Please follow the project's coding conventions and maintain good test coverage for core logic changes.

---

## Security

- Do not commit credentials (Twitch OAuth tokens, API keys) to the repo.
- For CI, store secrets in GitHub repository secrets and reference them in workflows.
- Consider using a secure secret store for production deployment.

---

## License

The project uses the same open-source license as the original repository (if applicable). Please check the `LICENSE` file in the repo root for details.
