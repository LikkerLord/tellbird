<p align="center">
  <img src="docs/logo-dark.png" alt="Tellbird - your chat, out loud" width="640">
</p>

<p align="center"><a href="https://tellbird.org"><b>tellbird.org</b></a></p>

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

1. **Start Tellbird.** A small window shows its progress and closes once it's
   ready, and a Tellbird icon appears in your system tray (Windows: bottom
   right, maybe behind the `^` arrow; macOS: top menu bar). The first start
   downloads the voice model, which takes a few minutes - later starts are
   quick.
2. **Open the control panel:** click the tray icon -> **Open Tellbird panel**,
   or go to <http://127.0.0.1:5050/> in your browser. It shows the download's
   progress until Tellbird is ready.
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
  set **Output device** to **CABLE Input (VB-Audio Virtual Cable), Windows
  DirectSound**. In OBS add an **Audio Input Capture** source and pick
  **CABLE Output**.
- **macOS:** install [BlackHole](https://existential.audio/blackhole/) (2ch).
  Set **Output device** to `BlackHole 2ch`, and add an OBS **Audio Input
  Capture** source on **BlackHole 2ch**.
- **Linux (PipeWire):** leave the output device empty and add an OBS
  **Application Audio Capture** source for Tellbird.

Pick the **Output device** from the list in the panel's settings, or keep
**System default**. A device you plug in while Tellbird runs shows up after a
restart.

## Everyday use

- **Watch the queue:** the panel's **Speak** tab shows what's being read
  right now and every message waiting, live. Remove a single message with its
  **×** button.
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

- **Voice** - pick one of the built-in voices from the list.
- **Use the small model** - less delay and CPU use, for English only.
- **Read username** / **Username format** - "Alice says: hello" or just
  "hello".
- **Ignored users** - bots like Nightbot and StreamElements are skipped by
  default; add your own.
- **Per-user cooldown** - limit how often one person gets read.
- **Filters** - collapse "aaaaaa", "lol lol lol lol", repeated phrases; skip
  word spam, very long words, repeated messages and copypasta floods.
- **Abbreviations** - teach it chat slang ("brb" -> "be right back").
- **Acronyms** - words read letter by letter ("GPU" -> "G-P-U"). Written in
  capitals, an entry only matches capitals, so "IT" leaves the word "it" alone;
  in lowercase it matches any case.
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

`user` is the chatter's login. It's what **Ignored users**, the cooldown and
the repeat filters go by, so send it whenever you have it. Text is spoken as
it is, unless you also send `name`: then Tellbird reads it like Twitch chat,
e.g. "Alice says: hello", following **Read username** and **Username format**.

**Firebot: read chat with custom names.** Import
[`tellbird-chat.firebotsetup`](examples/firebot/tellbird-chat.firebotsetup)
(set **Mode** to `http` first). Every chat message is then read as
"name says: message", where the name is the chatter's display name, or the
one they picked:

- `!ttsname Big Al` - subs, VIPs and mods pick the name Tellbird uses for them
- `!changename @user Name`, `!removename @user` - mods change or remove one
- `!revokename @user` - mods remove someone's name and stop them from setting
  a new one (they get the "TTS name revoked" role in Firebot)
- `!clearnames` - the streamer removes all custom names

It also adds **Skip TTS** and **Clear TTS queue** quick actions. Bots and
`!commands` are skipped by Tellbird itself: add your Firebot bot account to
**Ignored users** on the panel, or its replies get read too. Thanks to
DesignedByJoe for the original setup.

> **Send the message exactly as it was written, capitals included.** Tellbird
> tells the acronym "IT" (read "I-T") from the word "it" by its capitals. If
> your bot sends the text in all lowercase, or uses a variable that does,
> capitalised acronyms are read as plain words.

**Firebot reactions to filters:** Tellbird can also *trigger* Firebot when a
filter catches spam - e.g. to time someone out. See **Firebot actions** on
the Settings tab.

## Bot commands for the word lists

You can let mods add words from chat, e.g. `!acronym GPU`. Point a bot
command at one of these URLs with a `POST` request, and send the command's
words as a plain-text body, as in the Mix It Up example above. It works in
both modes.

| URL (`http://127.0.0.1:5050` + ...) | Body | Does |
|---|---|---|
| `/api/lists/acronyms/add` | `GPU CPU` | Adds each word to the acronyms |
| `/api/lists/acronyms/remove` | `GPU` | Removes it |
| `/api/lists/abbreviations/add` | `brb = be right back` | Adds or replaces an abbreviation |
| `/api/lists/abbreviations/remove` | `brb` | Removes it |
| `/api/lists/emotes/add` / `remove` | `catJAM` | Adds / removes an emote to leave out |

`brb be right back` (without the `=`) works too: the first word is the
abbreviation. The reply is a short sentence like "Added to the acronyms:
GPU." that your bot can post in chat. Changes apply right away and are
saved to the word list files.

Tellbird can't tell who ran the command, so **limit these commands to mods**
(or just yourself) in your bot's command settings.

**Firebot: ready-made commands.** Import
[`tellbird-word-lists.firebotsetup`](examples/firebot/tellbird-word-lists.firebotsetup)
with Firebot's **Import Setup** (File menu). It adds three commands for mods
and the streamer, and the bot replies with Tellbird's answer:

- `!acronym add GPU` / `!acronym remove GPU`
- `!abbreviation add brb = be right back` / `!abbreviation remove brb` (or `!abbr`)
- `!ttsemote add catJAM` / `!ttsemote remove catJAM`

Firebot asks for Tellbird's address while importing; keep the default unless
you changed Tellbird's HTTP host or port. You can rename the commands
afterwards.

## Troubleshooting

- **Nothing is read:** check the panel's channel name (no `#`). Messages
  starting with `!` and messages from ignored users are skipped on purpose.
- **No sound / wrong device:** set **Output device** to **System default** to use your
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

## Thanks

- [DesignedByJoe](https://www.twitch.tv/designedbyjoe) - helped shape the
  initial requirements and does a whole lot of testing.
- [NonStickOcean](https://www.twitch.tv/nonstickocean) - helped testing and
  finding some bugs.

## License

MIT - see [LICENSE](LICENSE).
