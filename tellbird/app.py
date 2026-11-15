"""The running bot: settings, the speech queue, config reload/save, and
startup (tray app or plain console)."""

import asyncio
import logging
import threading
import time
import urllib.request

from . import firebot, startup
from .config import (CONFIG_SCHEMA, KNOWN_VOICES, SCHEMA_BY_KEY, WORD_LISTS, ConfigError,
                     RunningConfig, coerce, model_name, read_config_file, read_word_lists,
                     write_config, write_json)
from .speechqueue import QueueItem, SpeechQueue
from .textproc import ChatFilter, WordLists, strip_native_emotes

log = logging.getLogger("tellbird")


def edit_list(name, current, action, text):
    """Apply a bot command ("add"/"remove" plus the command's text) to the
    contents of one word list. Returns (new contents, reply for the chat);
    raises ValueError with a usage hint for the chat on bad input."""
    words = text.split()
    if WORD_LISTS[name] is dict:
        if action == "add":
            if "=" in text:
                key, _, expansion = (s.strip() for s in text.partition("="))
            else:
                key, expansion = (words[0], " ".join(words[1:])) if words else ("", "")
            if not key or not expansion or len(key.split()) > 1:
                raise ValueError("Use it like: brb = be right back")
            new = {k: v for k, v in current.items() if k.lower() != key.lower()}
            new[key] = expansion
            return new, f'{key} now reads as "{expansion}".'
        gone = [w for w in words if w.lower() in {k.lower() for k in current}]
        new = {k: v for k, v in current.items() if k.lower() not in {w.lower() for w in gone}}
    else:
        if action == "add":
            if not words:
                raise ValueError(f"Say what to add to the {name}.")
            # A lowercase acronym matches any case, so "npc" already covers "NPC".
            any_case = {w for w in current if w == w.lower()} if name == "acronyms" else set()
            added = [w for w in dict.fromkeys(words)
                     if w not in current and w.lower() not in any_case]
            if not added:
                return current, f"Already in the {name}: {', '.join(words)}."
            return current + added, f"Added to the {name}: {', '.join(added)}."
        gone = [w for w in words if w in current]
        new = [w for w in current if w not in gone]
    if not words:
        raise ValueError(f"Say what to remove from the {name}.")
    if not gone:
        return current, f"Not in the {name}: {', '.join(words)}."
    return new, f"Removed from the {name}: {', '.join(gone)}."


def _changed_lists(new, old):
    """Names of the word lists that differ between two WordLists."""
    return [name for name in WORD_LISTS if getattr(new, name) != getattr(old, name)]


class Bot:
    """Everything the running bot needs, with no globals. `speaker` is an
    audio.Speaker (or a stand-in with the same methods, in tests); `audio`
    is the module providing check_output_device/output_device_names. Without
    them, run() loads the real ones in the background after starting the panel."""

    def __init__(self, paths, speaker=None, audio=None):
        self.paths = paths
        self.speaker = speaker
        self.audio = audio
        self.startup = startup.Startup()
        self._loaded_speaker = None    # kept across retries: the model outlives a bad voice
        self._retry = asyncio.Event()
        self.running = RunningConfig()
        values, warnings = read_config_file(paths.config)
        for w in warnings:
            log.warning(f"[config] {w}")
        self.settings, _ = self.running.apply(values)
        self.word_lists = WordLists(**read_word_lists(paths))
        self.chat_filter = ChatFilter(self.settings, self.word_lists, on_filter=self._on_filter)
        if speaker is not None:
            speaker.set_voice(self.settings["voice"])
            speaker.set_output_device(self.settings["output_device"])
        self.queue = SpeechQueue()
        self.current = None            # the QueueItem being spoken right now
        self.current_started = 0.0
        self.loop: asyncio.AbstractEventLoop | None = None   # set by run(); used by the tray
        self.twitch = None
        self._reload_lock = asyncio.Lock()
        self._lists_lock = asyncio.Lock()       # bot commands editing a word list

    def _on_filter(self, filter_name, user, text):
        firebot.fire(self.settings, filter_name, user, text)

    # ------------------------------ Speaking ---------------------------------
    async def enqueue(self, spoken, message="", user="", source=""):
        item = QueueItem(spoken, message or spoken, user, source)
        self.queue.put(item)
        return item

    async def on_chat_message(self, login, display, message, emotes_tag):
        if self.chat_filter.should_skip(login, message):
            return
        cleaned = self.chat_filter.clean_text(strip_native_emotes(message, emotes_tag), login)
        if not cleaned:
            return
        log.info(f"{display}: {cleaned}")
        spoken = self.chat_filter.build_spoken(display, cleaned)
        await self.enqueue(spoken, cleaned, display, "chat")

    async def speech_worker(self):
        loop = asyncio.get_running_loop()
        while True:
            item = await self.queue.get()
            self.current, self.current_started = item, time.time()
            try:
                await loop.run_in_executor(None, self.speaker.speak_blocking, item.spoken,
                                           self.settings["tail_padding_seconds"], item.cancel)
            except Exception as e:
                log.error(f"[TTS error] {e}")
            finally:
                self.current = None

    def skip(self):
        """Abort the current message; the worker moves on to the next."""
        current = self.current
        if current:
            current.cancel.set()

    async def clear(self):
        """Drop everything queued and abort the current message. Returns count dropped."""
        dropped = self.queue.clear()
        self.skip()
        return dropped

    def remove_queued(self, item_id):
        """Drop one waiting message; False if it's already gone (or playing)."""
        return self.queue.remove(item_id)

    def queue_snapshot(self):
        """What's being spoken and what's waiting, for the panel's queue monitor.
        `now` lets the page work out ages without trusting its own clock."""
        current = self.current
        return {
            "now": time.time(),
            "current": current and {**current.as_dict(), "started_at": self.current_started},
            "queued": [item.as_dict() for item in self.queue.items()],
        }

    # --------------------------- Config reload/save ---------------------------
    async def reload_config(self):
        """Re-read config.json and the word lists and apply them to the
        running bot. Returns a summary dict for the panel / log."""
        loop = asyncio.get_running_loop()
        async with self._reload_lock:
            values, warnings = read_config_file(self.paths.config)
            word_lists = WordLists(**read_word_lists(self.paths))
            old_voice = self.settings["voice"]
            if values["voice"] != old_voice:
                try:
                    await loop.run_in_executor(None, self.speaker.set_voice, values["voice"])
                    log.info(f"[config] voice switched to {values['voice']}")
                except ValueError as e:
                    warnings.append(f"voice: {e} - keeping {old_voice!r}")
                    values["voice"] = old_voice
            self.settings, changed = self.running.apply(values)
            self.chat_filter.settings = self.settings
            changed.update(_changed_lists(word_lists, self.word_lists))
            self.word_lists = self.chat_filter.word_lists = word_lists
            if "output_device" in changed:
                self.speaker.set_output_device(self.settings["output_device"])
                log.info(f"[config] output device: {self.settings['output_device'] or 'default'}")
            if "channel" in changed and self.twitch is not None:
                await self.twitch.rejoin()
            pending = self.running.restart_pending(values)
            for w in warnings:
                log.warning(f"[config] {w}")
            log.info(f"[config] reloaded ({len(changed)} setting(s) changed)"
                     + (f", restart needed for: {', '.join(pending)}" if pending else ""))
            return {"changed": sorted(changed), "warnings": warnings, "restart_pending": pending}

    async def save_config(self, updates, word_lists=None):
        """Validate panel edits (settings, and {name: contents} for the word
        lists being changed), write them to the files, then reload. Raises
        ValueError with {key: message} on invalid input."""
        word_lists = word_lists or {}
        loop = asyncio.get_running_loop()
        errors, clean = {}, {}
        for key, value in updates.items():
            field = SCHEMA_BY_KEY.get(key)
            if field is None:
                errors[key] = "unknown setting"
                continue
            try:
                clean[key] = coerce(field, value, strict=True)
            except ValueError as e:
                errors[key] = str(e)
        if "voice" in clean and clean["voice"] != self.settings["voice"]:
            try:
                # Loads (and caches) the voice, so the reload below is instant.
                await loop.run_in_executor(None, self.speaker.load_voice, clean["voice"])
            except ValueError as e:
                errors["voice"] = str(e)
        if "output_device" in clean:
            try:
                self.audio.check_output_device(clean["output_device"])
            except ValueError as e:
                errors["output_device"] = str(e)
        for name, value in word_lists.items():
            if name not in WORD_LISTS:
                errors[name] = "unknown word list"
            elif WORD_LISTS[name] is dict and not (
                    isinstance(value, dict) and all(isinstance(v, str) for v in value.values())):
                errors[name] = "must map each abbreviation to its expansion"
            elif WORD_LISTS[name] is list and not (
                    isinstance(value, list) and all(isinstance(v, str) for v in value)):
                errors[name] = "must be a list of words"
        if errors:
            raise ValueError(errors)
        async with self._reload_lock:
            if clean:
                write_config(self.paths.config, clean)
            for name, value in word_lists.items():
                write_json(self.paths.word_list(name), value)
        return await self.reload_config()

    async def edit_word_list(self, name, action, text):
        """A bot command adding to / removing from a word list (see
        edit_list). Works on the saved file, so hand edits aren't lost.
        Returns the reply for the chat."""
        async with self._lists_lock:
            current = read_word_lists(self.paths)[name]
            new, reply = edit_list(name, current, action, text)
            if new != current:
                await self.save_config({}, {name: new})
                log.info(f"[lists] {reply}")
            return reply

    def config_snapshot(self):
        """Everything the settings page needs: schema, saved values, word
        lists, and whether the saved files differ from what's running."""
        active = self.running.active
        snap = {"schema": CONFIG_SCHEMA, "running": active,
                "suggest": {"voices": self.audio.voice_names() if self.audio else KNOWN_VOICES,
                            "devices": self.audio.output_device_names() if self.audio else []},
                "config_path": str(self.paths.config)}
        try:
            values, warnings = read_config_file(self.paths.config)
            saved_lists = read_word_lists(self.paths)
        except ConfigError as e:
            return {**snap, "error": str(e), "values": active,
                    **{name: getattr(self.word_lists, name) for name in WORD_LISTS}}
        # Saved but not yet running, e.g. config.json was edited by hand.
        unapplied = sorted(k for k, v in values.items()
                           if not SCHEMA_BY_KEY[k].get("restart") and active.get(k) != v)
        unapplied += _changed_lists(WordLists(**saved_lists), self.word_lists)
        return {**snap, "values": values, "warnings": warnings, **saved_lists,
                "restart_pending": self.running.restart_pending(values), "unapplied": unapplied}

    # ------------------------------- Startup ---------------------------------
    @property
    def speech_ready(self):
        return self.speaker is not None

    def status_snapshot(self):
        return {**self.startup.snapshot(), "speech_ready": self.speech_ready}

    def retry_startup(self):
        """Try loading again after a failure; False if nothing has failed."""
        if self.startup.stage != startup.FAILED:
            return False
        self._retry.set()
        return True

    def _load_speech(self):
        """The slow part of starting: may download the model. Blocking. The
        config is read again because a retry may follow a fix to the file."""
        from . import audio
        self.startup.set(startup.LOADING)
        values, _ = read_config_file(self.paths.config)
        self.settings, _ = self.running.apply(values)
        self.chat_filter.settings = self.settings
        with audio.report_downloads(self.startup.download_progress):
            if self._loaded_speaker is None:
                self._loaded_speaker = audio.Speaker(model_name(self.settings))
            self.startup.set(startup.VOICE)
            self._loaded_speaker.set_voice(self.settings["voice"])
        self._loaded_speaker.set_output_device(self.settings["output_device"])
        self.audio, self.speaker = audio, self._loaded_speaker

    def _in_background(self, func):
        """Run func on a daemon thread, unlike run_in_executor, so quitting
        during a download doesn't wait for it to finish."""
        future = self.loop.create_future()

        def target():
            try:
                func()
            except Exception as e:
                self.loop.call_soon_threadsafe(future.set_exception, e)
            else:
                self.loop.call_soon_threadsafe(future.set_result, None)

        threading.Thread(target=target, daemon=True).start()
        return future

    async def _load_until_ready(self):
        while not self.speech_ready:
            try:
                await self._in_background(self._load_speech)
            except (ConfigError, ValueError) as e:
                self.startup.fail(str(e))
            except Exception as e:
                log.exception("[startup] loading failed:")
                self.startup.fail(f"Couldn't load the voice model: {e}")
            else:
                return
            await self._retry.wait()
            self._retry.clear()

    # --------------------------------- Run -----------------------------------
    async def run(self):
        from .server import serve
        self.loop = asyncio.get_running_loop()
        # The control server (/skip, /clear, /tts) runs in BOTH modes so the
        # skip URL always works; twitch mode additionally reads the chat.
        port = self.settings["http_port"]
        try:
            await serve(self, self.settings["http_host"], port)
        except OSError as e:
            # Not another Tellbird (main() checked): something else has the port.
            self.startup.fail(f"Couldn't open port {port}, another program may be using it. "
                              f"Set a different http_port in config.json. ({e})")
            return
        await self._load_until_ready()
        tasks = [self.speech_worker()]
        if self.settings["mode"] == "twitch":
            from .twitch import TwitchChat
            self.startup.set(startup.CONNECTING)
            self.twitch = TwitchChat(lambda: self.settings.channel, self.on_chat_message,
                                     on_connected=lambda: self.startup.set(startup.READY))
            tasks.append(self.twitch.run())
        else:
            self.startup.set(startup.READY)
        await asyncio.gather(*tasks)


def already_running(host, port):
    """True if a Tellbird already answers on this address. Proxies are
    skipped: they'd answer for localhost themselves, or fail."""
    from .server import APP_HEADER
    from .tray import panel_url
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(panel_url(host, port) + "health", timeout=1) as r:
            return r.headers.get(APP_HEADER) is not None
    except Exception:
        return False


def main():
    from . import __version__, paths as paths_mod
    from .logs import setup_logging

    paths = paths_mod.prepare()
    setup_logging(paths.log_file)
    log.info(f"Tellbird {__version__}")
    for note in paths.notes:
        log.info(note)

    from . import tray
    try:
        bot = Bot(paths)
    except (ConfigError, ValueError) as e:
        # No settings means no port for the panel, so the tray is all there is.
        log.error(str(e))
        if not tray.available():
            raise SystemExit(str(e))
        try:
            tray.run_failed(paths, str(e))
        except KeyboardInterrupt:
            pass
        return
    if already_running(bot.settings["http_host"], bot.settings["http_port"]):
        # Launched again: the running one carries on, this one goes quietly.
        log.info("Tellbird is already running, exiting.")
        return
    log.info(f"Mode: {bot.settings['mode']} | "
             f"Output: {bot.settings['output_device'] or 'default'}")
    if bot.settings["show_splash"]:
        from . import splash
        splash.launch(tray.panel_url(bot.settings["http_host"], bot.settings["http_port"]))

    if bot.settings["show_tray_icon"] and tray.available():
        worker = threading.Thread(target=lambda: asyncio.run(bot.run()), daemon=True)
        worker.start()
        try:
            tray.run(bot)
        except KeyboardInterrupt:
            log.info("Stopped.")
        except Exception:
            log.exception("[tray] unavailable, continuing without it:")
            worker.join()
    else:
        try:
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            log.info("Stopped.")
        if bot.startup.error:
            raise SystemExit(bot.startup.error)
