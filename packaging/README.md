# Packaging & release

This turns the bot into installers for Windows, macOS and Linux, built
automatically by GitHub Actions.

## How the release cycle works
1. Commit everything (including the `packaging/` folder and the workflow).
2. Tag a version and push it:
   ```
   git tag v1.0.0
   git push origin v1.0.0
   ```
3. GitHub Actions builds on all three OSes and attaches the results to a new
   GitHub Release:
   - Windows: `Tellbird-<ver>-Setup.exe` (Inno Setup installer, signed if
     you've added the Windows signing secrets below)
   - macOS:   `Tellbird-macos.dmg` (signed + notarized if you've added the
     Apple signing secrets below)
   - Linux:   `Tellbird-<ver>-x86_64.AppImage` (double-click, no install)
   `workflow_dispatch` lets you run a build without tagging, to test.

## What it does under the hood
- Installs **CPU-only PyTorch** (the default wheel bundles CUDA and is huge).
- Freezes the app with **PyInstaller** using `packaging/tellbird.spec`, built
  windowless (`console=False`) since the bot runs as a **system-tray app**:
  the tray menu (via `pystray`) has Open Tellbird panel (the control panel the
  bot serves at http://127.0.0.1:5050/), Reload config, Open config folder,
  Open log folder, Skip current, Clear queue, and Quit. Set `"show_tray_icon": false` in `config.json` to run as a plain
  console app instead; it also falls back to console mode automatically if
  the tray backend can't start (e.g. headless Linux with no display).
- Since there's no console window, all `print()` output goes to a log file
  instead: `tellbird.log` next to `config.json` in the per-user folder below.
- On first run the app copies `config.json` / `abbreviations.json` /
  `emotes.json` into a per-user folder so they stay editable:
  - Windows `%APPDATA%\tellbird`
  - macOS   `~/Library/Application Support/tellbird`
  - Linux   `~/.config/tellbird`

## Code signing / notarization (optional, off by default)
The workflow signs and notarizes automatically once you add these as **repo
secrets** (Settings -> Secrets and variables -> Actions). Leave them unset and
the build still works, just unsigned/unnotarized as before.

- **Windows** (needs an OV/EV code-signing certificate):
  - `WINDOWS_CERTIFICATE_BASE64` - your `.pfx` file, base64-encoded
    (`base64 -w0 cert.pfx`)
  - `WINDOWS_CERTIFICATE_PASSWORD` - its password
- **macOS** (needs an Apple Developer account, $99/yr):
  - `MACOS_CERTIFICATE_BASE64` - your "Developer ID Application" `.p12`,
    base64-encoded (`base64 -i cert.p12 | tr -d '\n'`)
  - `MACOS_CERTIFICATE_PASSWORD` - its password
  - `MACOS_CERTIFICATE_IDENTITY` - the signing identity string, e.g.
    `Developer ID Application: Your Name (TEAMID1234)`
  - `APPLE_ID` - your Apple ID email
  - `APPLE_ID_PASSWORD` - an **app-specific password** (generate one at
    appleid.apple.com), not your real Apple ID password
  - `APPLE_TEAM_ID` - your 10-character Apple Developer Team ID

Without these: Windows SmartScreen will warn ("More info -> Run anyway") and
macOS Gatekeeper will block the unsigned `.app` ("right-click -> Open" works
around it).
