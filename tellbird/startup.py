"""What the app is doing while it starts, for the panel and the tray. The model
loads in the background, so this is what they show until it's ready."""

import logging
import threading
import time

log = logging.getLogger("tellbird")

STARTING = "starting"
LOADING = "loading"
DOWNLOADING = "downloading"
VOICE = "voice"
CONNECTING = "connecting"
READY = "ready"
FAILED = "failed"

MESSAGES = {
    STARTING: "Starting…",
    LOADING: "Loading the voice model…",
    DOWNLOADING: "Downloading the voice model…",
    VOICE: "Loading the voice…",
    CONNECTING: "Connecting to Twitch…",
    READY: "Ready",
    FAILED: "Couldn't start",
}

# A start this slow gets a "ready" notification even without a download.
SLOW_START_SECONDS = 60


class Startup:
    """The current stage. set() can be called from any thread; listeners run
    on the calling thread (and once right away when they subscribe)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = []
        self._started = time.monotonic()
        self._before_download = LOADING
        self._had_trouble = False       # a download or failure the user should hear the end of
        self.stage = STARTING
        self.error = None
        self.done = 0
        self.total = 0

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)
            listener(self)

    def set(self, stage, error=None):
        with self._lock:
            if stage == self.stage and error == self.error:
                return
            self.stage, self.error = stage, error
            if stage == FAILED:
                self._had_trouble = True
                log.error(f"[startup] {error}")
            else:
                log.info(f"[startup] {MESSAGES[stage]}")
            for listener in self._listeners:
                listener(self)

    def fail(self, error):
        self.set(FAILED, error)

    def download_progress(self, done, total):
        """Bytes so far across the files being downloaded; moves in and out of
        the download stage as files start and finish."""
        self.done, self.total = done, total
        if done < total and self.stage != DOWNLOADING:
            self._before_download = self.stage
            self._had_trouble = True
            self.set(DOWNLOADING)
        elif done >= total and self.stage == DOWNLOADING:
            self.set(self._before_download)

    def snapshot(self):
        return {"stage": self.stage, "message": MESSAGES[self.stage], "error": self.error,
                "done": self.done, "total": self.total}

    def menu_text(self, mode):
        if self.stage == READY:
            return f"Tellbird ({mode} mode)"
        return f"Tellbird: {MESSAGES[self.stage]}"

    def announcement(self):
        """-> the notification worth showing for this stage, or None. Normal
        starts stay quiet."""
        if self.stage == DOWNLOADING:
            return "Downloading the voice model. This only happens once and may take a few minutes."
        if self.stage == FAILED:
            return self.error
        if self.stage == READY and (self._had_trouble or
                                    time.monotonic() - self._started > SLOW_START_SECONDS):
            return "Tellbird is ready. Click the tray icon to open the panel."
        return None
