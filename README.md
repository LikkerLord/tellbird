# Tellbird

Tellbird reads your Twitch chat aloud with a natural-sounding voice, ready to
put on stream through OBS.

- **Runs on your own computer.** The voice is generated locally - no account,
  no API key, no cloud service. After a one-time download it works offline.
- **No login needed.** It just listens to your channel's public chat.
- **Keeps chat spam in check:** skips commands and bots, removes links and
  emotes, tames "aaaaaaaa" and copypasta floods.
- **Works with Firebot, Mix It Up and Stream Deck** if you want more control
  over what gets read.

## Install

Download the file for your system from the
[latest release](https://github.com/LikkerLord/tellbird/releases/latest):

| System | File | How to install |
|---|---|---|
| Windows | `Tellbird-<version>-Setup.exe` | Run it. If Windows SmartScreen warns, click **More info -> Run anyway**. |
| macOS (Apple Silicon) | `Tellbird-macos.dmg` | Open it and drag Tellbird to Applications. If macOS says it can't be opened, right-click the app -> **Open**, or allow it under System Settings -> Privacy & Security -> **Open Anyway**. |
| Linux | `Tellbird-<version>-x86_64.AppImage` | Make it executable (right-click -> Properties -> "Allow executing", or `chmod +x Tellbird-*.AppImage`) and double-click it. |

The warnings appear because the installers aren't code-signed yet; they're
safe to skip for files downloaded from the link above.

## First start

1. **Start Tellbird.** The first start downloads the voice model, which takes
   a few minutes - later starts are quick. A Tellbird icon appears in your
   system tray (Windows: bottom right, maybe behind the `^` arrow; macOS: top
   menu bar).
2. **Open the control panel:** click the tray icon -> **Open Tellbird panel**,
   or go to <http://127.0.0.1:5050/> in your browser.
3. **Settings tab:** enter your **Twitch channel** name and click
   **Save & apply**.
4. **Test it:** type a line on the panel's **Test** tab and click **Speak**, or
   write a message in your chat. You should hear it.

## Get the voice into OBS

**Easiest (all systems):** do nothing - Tellbird plays through your normal
speakers and OBS picks it up with **Desktop Audio**. The downside: you hear
it too, and so does anything else you capture from your desktop.

**Separate audio source (recommended):** send Tellbird to a virtual audio
cable, then capture that cable in OBS on its own. This gives it its own
volume slider and keeps it out of your headphones if you want.

- **Windows:** install [VB-CABLE](https://vb-audio.com/Cable/). In the panel,
  set **Output device** to `CABLE Input`. In OBS add an **Audio Input
  Capture** source and pick **CABLE Output**.
- **macOS:** install [BlackHole](https://existential.audio/blackhole/) (2ch).
  Set **Output device** to `BlackHole 2ch`, and add an OBS **Audio Input
  Capture** source on **BlackHole 2ch**.
- **Linux (PipeWire):** leave the output device empty and add an OBS
  **Application Audio Capture** source for Tellbird.

The **Output device** field on the panel suggests the devices it finds; leave
it empty for your system default.

## Everyday use

- **Skip** the current message or **clear** the whole queue from the tray
  menu or the panel.
- **Stream Deck / hotkeys:** any button that can open a URL works:
  - `http://127.0.0.1:5050/skip` - stop the current message, play the next
  - `http://127.0.0.1:5050/clear` - drop everything waiting and stop
- **Quit** from the tray menu.

## Settings

Everything is on the panel's **Settings** tab, with an explanation next to
each option. **Save & apply** takes effect immediately; the few settings
marked *restart* need Tellbird to be restarted. Highlights:

- **Voice** - pick one of the built-in voices (alba, anna, michael, vera,
  jane, eve, george, mary, paul, charles).
- **Read username** / **Username format** - "Alice says: hello" or just
  "hello".
- **Ignored users** - bots like Nightbot and StreamElements are skipped by
  default; add your own.
- **Per-user cooldown** - limit how often one person gets read.
- **Filters** - collapse "aaaaaa", "lol lol lol lol", repeated phrases; skip
  word spam, very long words, repeated messages and copypasta floods.
- **Abbreviations** - teach it chat slang ("brb" -> "be right back").
- **Emotes** - names of BTTV/FFZ/7TV emotes to leave out (Twitch's own emotes
  are removed automatically).

Your settings are stored in `config.json` in Tellbird's settings folder (tray
menu -> **Open config folder**). You can edit that file by hand too - lines
starting with `//` are comments - then click **Reload from file** on the panel.

## Using Firebot, Mix It Up or another bot

Instead of reading chat itself, Tellbird can speak only what another bot
sends it - handy if that bot already decides what should be read (channel
point redeems, a `!tts` command, ...). Set **Mode** to `http` on the panel
and restart Tellbird. Your bot then sends text to `http://127.0.0.1:5050/tts`:

- **Firebot:** an **HTTP Request** effect pointed at that URL. Sending the
  text as a plain-text body with the username in the URL, as in the Mix It
  Up example below, avoids any problems with special characters.
- **Mix It Up:** an **HTTP Custom Request** action with
  - Method: `POST`
  - URL: `http://127.0.0.1:5050/tts?user=$username`
  - Body: `$message`
  - Content Type: `text/plain`

  Send the message as plain text like this rather than as JSON: Mix It Up
  doesn't escape quotes, so a chat message containing `"` would break a JSON
  body and silently not be read.

Other tools can also `POST` JSON (`{"user": "...", "text": "..."}`) or a form
(`user=...&text=...`).

**Firebot reactions to filters:** Tellbird can also *trigger* Firebot when a
filter catches spam - e.g. to time someone out. See **Firebot actions** on
the Settings tab.

## Troubleshooting

- **Nothing is read:** check the panel's channel name (no `#`). Messages
  starting with `!` and messages from ignored users are skipped on purpose.
- **No sound / wrong device:** clear the **Output device** field to use your
  default speakers and test again from the panel.
- **The last word gets cut off:** raise **Tail padding** in the settings.
- **No tray icon on Linux (GNOME):** install the "AppIndicator and KStatusNotifierItem
  Support" extension, or just use the panel at <http://127.0.0.1:5050/>.
- **No sound on Linux:** install PortAudio (`sudo apt install libportaudio2`
  or `sudo dnf install portaudio`).
- **Something else:** the log file shows what's going on - tray menu ->
  **Open log folder** -> `tellbird.log`. Please include it when you
  [report a problem](https://github.com/LikkerLord/tellbird/issues).

Settings folder locations, if you need them without the tray:
Windows `%APPDATA%\tellbird`, macOS `~/Library/Application Support/tellbird`,
Linux `~/.config/tellbird`.

## Running from source / contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT - see [LICENSE](LICENSE).
