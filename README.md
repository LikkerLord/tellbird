# Tellbird

Reads your Twitch chat aloud with a local text-to-speech voice. After a
one-time model download it runs fully offline.

## What's in this folder

| File | Purpose |
|---|---|
| `tellbird/` | The bot (same on every OS). |
| `config.example.json` | Settings template. Setup copies this to `config.json` on first run - edit `config.json`, not this file. |
| `tellbird/panel.html` | The control panel the bot serves at http://127.0.0.1:5050/ (test speech, change settings). |
| `abbreviations.json` | Optional: chat shorthand -> spoken words. |
| `emotes.json` | Optional: third-party emotes to skip. |

| OS | Setup | Start |
|---|---|---|
| macOS | `Setup.command` | `Start Tellbird.command` |
| Windows | `windows-setup.bat` | `windows-start.bat` |
| Linux | `linux-setup.sh` | `linux-start.sh` |

## Requirements

Python 3.10-3.14. The setup script looks for a suitable Python and tells
you if none is found.

## First time

- **macOS**: double-click `Setup.command`. If macOS blocks it (downloaded
  file): right-click -> Open -> Open.
- **Windows**: double-click `windows-setup.bat`. If SmartScreen warns:
  "More info" -> "Run anyway".
- **Linux**: run `./linux-setup.sh` in a terminal (or mark executable and
  use "Run in Terminal"). You may need PortAudio (the script tells you).

The setup installs everything, copies `config.example.json` to `config.json`,
and pre-downloads the voice (a few minutes).

## Audio into OBS (per OS)

The bot plays to the device named in `config.json` (`"output_device"`).

- **macOS**: install [BlackHole](https://existential.audio/blackhole/), set
  `output_device` to `"BlackHole 2ch"`, add OBS "Audio Input Capture" on
  BlackHole 2ch.
- **Windows**: install [VB-CABLE](https://vb-audio.com/Cable/), set
  `output_device` to `"CABLE Input (VB-Audio Virtual Cable)"`, add OBS
  "Audio Input Capture" on "CABLE Output".
- **Linux**: easiest - leave `output_device` as `null` and add OBS
  "Application Audio Capture" (PipeWire) on the python process; or create a
  null sink (see `linux-setup.sh` output) and target it.

Simplest everywhere: leave `output_device` as `null` (plays to your normal
output) and let OBS capture Desktop Audio - the downside is you hear the TTS
too.

## Every time

- **macOS**: `Start Tellbird.command`
- **Windows**: `windows-start.bat`
- **Linux**: `./linux-start.sh`

Then write a message in your chat to test. Close the window / Ctrl+C to stop.

## Settings

Open the control panel at http://127.0.0.1:5050/ (or "Open Tellbird panel" in
the tray menu) and use the **Settings** tab. "Save & apply" writes your
changes to `config.json` (comments and layout are kept) and applies them
right away. A few settings (`mode`, `language`, `http_host`, `http_port`,
`show_tray_icon`) are marked *restart* and only take effect after restarting
the bot. The same tab edits `abbreviations.json` and `emotes.json`.

You can still edit `config.json` by hand (annotated; `//` lines are
comments): save it, then click "Reload from file" on the panel or "Reload
config" in the tray menu. No restart needed.

- `mode` - `"twitch"` (read chat) or `"http"` (Firebot, Mix It Up, or anything
  else sends text)
- `voice` - built-in voice (alba, anna, michael, vera, jane, ...)
- `output_device` - see "Audio into OBS" above
- `filters` - optional anti-abuse filters (repeated chars/words/phrases,
  word spam, cross-user copypasta floods)
- `firebot_actions` - optional: run a Firebot preset when a filter fires
- `show_tray_icon` - true (default): also show a system-tray icon with
  Open Tellbird panel / Reload config / Open config folder / Open log folder /
  Skip / Clear / Quit

## Control (works in both modes)

A small local server always runs. Trigger from a browser, Stream Deck, or a
Firebot HTTP Request effect:

- `http://127.0.0.1:5050/skip` - stop the current message, play the next
- `http://127.0.0.1:5050/clear` - drop the whole queue and stop the current
- `http://127.0.0.1:5050/tts` - speak text (`"http"` mode), see below

### Using "http" mode with Mix It Up (or Firebot, or anything else)

In `"http"` mode, `/tts` speaks whatever text it's given instead of reading
Twitch chat directly - useful if you'd rather route messages through a bot
you're already running (filters, moderation, commands) and just have it hand
the final text to this one for voicing.

`/tts` accepts:
- `GET /tts?user=<name>&text=<message>`
- `POST` with a JSON body: `{"user": "...", "text": "..."}`
- `POST` with a form body: `user=...&text=...`
- `POST` with any other body: the raw bytes are spoken as-is, with the
  username (optional) passed as `?user=` in the URL instead.

That last form exists because some bots can't escape the message text for
you. **Mix It Up**'s "HTTP Custom Request" action builds the request body by
substituting `$message` straight into whatever template you type, with no
quote/backslash/newline escaping - so a chat message containing a `"` breaks
a JSON body, and the command silently fails to speak. Avoid that entirely by
sending the raw message as the POST body (no JSON, no escaping needed) and
putting the username in the URL instead:

- Command: **HTTP Custom Request**
- Method: `POST`
- URL: `http://127.0.0.1:5050/tts?user=$username`
- Body: `$message`
- Content Type: `text/plain` (anything other than
  `application/json`/`application/x-www-form-urlencoded` works)

## Notes

- Everything runs locally/offline after the first model download.
- If audio endings sound clipped, raise `tail_padding_seconds` in
  `config.json`.

## Contributing / running from source

The setup scripts above already run the bot from source (`python -m tellbird`),
so there's no separate install step: edit the code and restart.

Code layout (`tellbird/`):

| Module | What's in it |
|---|---|
| `app.py` | `Bot` (speech queue, config reload/save) and `main()` startup |
| `config.py` | Settings schema, validation, comment-preserving `config.json` writer |
| `textproc.py` | Chat message cleanup and the anti-abuse filters |
| `audio.py` | Pocket TTS + playback - the only module that needs torch/PortAudio |
| `twitch.py` | Twitch chat connection |
| `server.py` | Local HTTP server: panel, `/tts`, `/skip`, `/clear`, settings API |
| `tray.py` | System-tray icon |
| `paths.py`, `logs.py` | Where config/log files live, logging setup |
| `firebot.py` | Firebot actions when a filter fires |

To add a setting, add it to `CONFIG_SCHEMA` in `config.py` (and document it
in `config.example.json`); the panel picks it up automatically and the code
reads it as `bot.settings["your.key"]`.

Tests and lint (no torch needed, takes seconds):

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

CI runs both on every push. See [`packaging/README.md`](packaging/README.md)
for how release builds work and `constraints.txt` for the pinned versions
they use.
