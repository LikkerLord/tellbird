# Running from source & contributing

## Running from source

Needs Python 3.10-3.14. The setup scripts find a suitable Python, create a
virtual environment, install the dependencies, copy `config.example.json` to
`config.json` and pre-download the voice model (a few minutes).

| OS | Setup (once) | Start |
|---|---|---|
| Windows | `windows-setup.bat` | `windows-start.bat` |
| macOS | `Setup.command` | `Start Tellbird.command` |
| Linux | `./linux-setup.sh` | `./linux-start.sh` |

The start scripts run `python -m tellbird`. Running from source, the config
files live next to the code (in the repo root) instead of the per-user
settings folder, and the log goes to the console only.

## Code layout

| Module | What's in it |
|---|---|
| `tellbird/app.py` | `Bot` (speech queue, config reload/save) and `main()` startup |
| `tellbird/config.py` | Settings schema, validation, comment-preserving `config.json` writer |
| `tellbird/textproc.py` | Chat message cleanup and the anti-abuse filters |
| `tellbird/audio.py` | Pocket TTS + playback - the only module that needs torch/PortAudio |
| `tellbird/twitch.py` | Twitch chat connection |
| `tellbird/server.py` | Local HTTP server: panel, `/tts`, `/skip`, `/clear`, settings API |
| `tellbird/tray.py` | System-tray icon |
| `tellbird/paths.py`, `logs.py` | Where config/log files live, logging setup |
| `tellbird/firebot.py` | Firebot actions when a filter fires |
| `tellbird/panel.html` | The control panel (plain HTML/JS, built from the schema) |

To add a setting, add it to `CONFIG_SCHEMA` in `config.py` and document it in
`config.example.json` (a test checks that). The panel picks it up
automatically and the code reads it as `bot.settings["your.key"]`.

## Tests and lint

They don't need torch or a sound card and take seconds:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

CI runs both on every push and pull request.

## Releases

Pushing a tag like `v1.2.3` builds the Windows, macOS and Linux installers
and publishes a GitHub release; see [packaging/README.md](packaging/README.md).
Release builds install the exact versions in `constraints.txt`. Bump the
version in `tellbird/__init__.py` to match the tag.
