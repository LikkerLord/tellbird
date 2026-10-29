"""The running bot: settings, the speech queue, config reload/save, and
startup (tray app or plain console)."""

import asyncio
import logging
import threading

from . import firebot
from .config import (CONFIG_SCHEMA, KNOWN_VOICES, SCHEMA_BY_KEY, ConfigError, RunningConfig,
                     coerce, read_config_file, read_word_lists, write_config, write_json)
from .textproc import ChatFilter, WordLists

log = logging.getLogger("tellbird")


class Bot:
    """Everything the running bot needs, with no globals. `speaker` is an
    audio.Speaker (or a stand-in with the same methods, in tests); `audio`
    is the module providing check_output_device/output_device_names."""

    def __init__(self, paths, speaker, audio):
        self.paths = paths
        self.speaker = speaker
        self.audio = audio
        self.running = RunningConfig()
        values, warnings = read_config_file(paths.config)
        for w in warnings:
            log.warning(f"[config] {w}")
        self.settings, _ = self.running.apply(values)
        self.word_lists = WordLists(*read_word_lists(paths.abbreviations, paths.emotes))
        self.chat_filter = ChatFilter(self.settings, self.word_lists, on_filter=self._on_filter)
        speaker.set_voice(self.settings["voice"])
        speaker.set_output_device(self.settings["output_device"])
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None   # set by run(); used by the tray
        self.twitch = None
        self._reload_lock = asyncio.Lock()

    def _on_filter(self, filter_name, user, text):
        firebot.fire(self.settings, filter_name, user, text)

    # ------------------------------ Speaking ---------------------------------
    async def enqueue(self, text):
        await self.queue.put(text)

    async def on_chat_message(self, login, display, message, emotes_tag):
        from .textproc import strip_native_emotes
        if self.chat_filter.should_skip(login, message):
            return
        cleaned = self.chat_filter.clean_text(strip_native_emotes(message, emotes_tag), login)
        if not cleaned:
            return
        log.info(f"{display}: {cleaned}")
        await self.enqueue(self.chat_filter.build_spoken(display, cleaned))

    async def speech_worker(self):
        loop = asyncio.get_running_loop()
        while True:
            text = await self.queue.get()
            try:
                await loop.run_in_executor(None, self.speaker.speak_blocking, text,
                                           self.settings["tail_padding_seconds"])
            except Exception as e:
                log.error(f"[TTS error] {e}")
            finally:
                self.queue.task_done()

    def skip(self):
        """Abort the current message; the worker moves on to the next."""
        self.speaker.stop()

    async def clear(self):
        """Drop everything queued and abort the current message. Returns count dropped."""
        dropped = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.queue.task_done()
            dropped += 1
        self.speaker.stop()
        return dropped

    # --------------------------- Config reload/save ---------------------------
    async def reload_config(self):
        """Re-read config.json, abbreviations.json and emotes.json and apply
        them to the running bot. Returns a summary dict for the panel / log."""
        loop = asyncio.get_running_loop()
        async with self._reload_lock:
            values, warnings = read_config_file(self.paths.config)
            word_lists = WordLists(*read_word_lists(self.paths.abbreviations, self.paths.emotes))
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
            if word_lists.abbreviations != self.word_lists.abbreviations:
                changed.add("abbreviations")
            if word_lists.emotes != self.word_lists.emotes:
                changed.add("emotes")
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

    async def save_config(self, updates, abbreviations=None, emotes=None):
        """Validate panel edits, write them to the config files, then reload.
        Raises ValueError with {key: message} on invalid input."""
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
        if abbreviations is not None and not (
                isinstance(abbreviations, dict)
                and all(isinstance(v, str) for v in abbreviations.values())):
            errors["abbreviations"] = "must map each abbreviation to its expansion"
        if emotes is not None and not (
                isinstance(emotes, list) and all(isinstance(e, str) for e in emotes)):
            errors["emotes"] = "must be a list of emote names"
        if errors:
            raise ValueError(errors)
        async with self._reload_lock:
            if clean:
                write_config(self.paths.config, clean)
            if abbreviations is not None:
                write_json(self.paths.abbreviations, abbreviations)
            if emotes is not None:
                write_json(self.paths.emotes, emotes)
        return await self.reload_config()

    def config_snapshot(self):
        """Everything the settings page needs: schema, saved values, word
        lists, and whether the saved files differ from what's running."""
        active = self.running.active
        snap = {"schema": CONFIG_SCHEMA, "running": active,
                "suggest": {"voices": KNOWN_VOICES, "devices": self.audio.output_device_names()},
                "config_path": str(self.paths.config)}
        try:
            values, warnings = read_config_file(self.paths.config)
            abbreviations, emotes = read_word_lists(self.paths.abbreviations, self.paths.emotes)
        except ConfigError as e:
            return {**snap, "error": str(e), "values": active,
                    "abbreviations": self.word_lists.abbreviations,
                    "emotes": self.word_lists.emotes}
        # Saved but not yet running, e.g. config.json was edited by hand.
        unapplied = sorted(k for k, v in values.items()
                           if not SCHEMA_BY_KEY[k].get("restart") and active.get(k) != v)
        saved_lists = WordLists(abbreviations, emotes)
        if saved_lists.abbreviations != self.word_lists.abbreviations:
            unapplied.append("abbreviations")
        if saved_lists.emotes != self.word_lists.emotes:
            unapplied.append("emotes")
        return {**snap, "values": values, "warnings": warnings,
                "abbreviations": abbreviations, "emotes": emotes,
                "restart_pending": self.running.restart_pending(values), "unapplied": unapplied}

    # --------------------------------- Run -----------------------------------
    async def run(self):
        from .server import serve
        self.loop = asyncio.get_running_loop()
        # The control server (/skip, /clear, /tts) runs in BOTH modes so the
        # skip URL always works; twitch mode additionally reads the chat.
        await serve(self, self.settings["http_host"], self.settings["http_port"])
        tasks = [self.speech_worker()]
        if self.settings["mode"] == "twitch":
            from .twitch import TwitchChat
            self.twitch = TwitchChat(lambda: self.settings.channel, self.on_chat_message)
            tasks.append(self.twitch.run())
        await asyncio.gather(*tasks)


def main():
    from . import __version__, paths as paths_mod
    from .logs import setup_logging

    paths = paths_mod.prepare()
    setup_logging(paths.log_file)
    log.info(f"Tellbird {__version__}")
    for note in paths.notes:
        log.info(note)

    # Read the config before the (slow) model load so a broken config.json
    # fails right away.
    try:
        values, _ = read_config_file(paths.config)
    except ConfigError as e:
        raise SystemExit(str(e))
    from . import audio
    speaker = audio.Speaker(values["language"])
    try:
        bot = Bot(paths, speaker, audio)
    except (ConfigError, ValueError) as e:
        raise SystemExit(str(e))
    log.info(f"Pocket TTS ready. Mode: {bot.settings['mode']} | "
             f"Output: {bot.settings['output_device'] or 'default'}")

    from . import tray
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
