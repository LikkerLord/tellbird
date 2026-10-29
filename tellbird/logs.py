"""Logging: timestamped lines on the console, plus a size-capped log file for
the installed app (which has no visible console)."""

import logging
import logging.handlers
import sys
import threading

log = logging.getLogger("tellbird")


class _LogWriter:
    """File-like object that forwards writes to the log, line by line. Stands
    in for sys.stdout/sys.stderr when a windowed build has none, so stray
    output from libraries isn't lost."""

    def __init__(self, level):
        self._level = level
        self._buf = ""

    def write(self, data):
        self._buf += data
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            if line.strip():
                log.log(self._level, line.rstrip())
        return len(data)

    def flush(self):
        pass


def setup_logging(log_file=None):
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = []
    console = sys.stdout or sys.stderr
    if console is not None:
        handlers.append(logging.StreamHandler(console))
    if log_file is not None:
        # Always keep a log file for the installed app, on every OS: a crash
        # in a console-less build would otherwise vanish with nothing to
        # inspect. Capped at ~3 MB in total.
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        logging.getLogger().addHandler(h)
    # Our own messages at INFO; libraries (aiohttp access log, websockets,
    # ...) only when something is actually wrong.
    logging.getLogger().setLevel(logging.WARNING)
    log.setLevel(logging.INFO)
    logging.captureWarnings(True)

    if log_file is not None:
        if sys.stdout is None:
            sys.stdout = _LogWriter(logging.INFO)
        if sys.stderr is None:
            sys.stderr = _LogWriter(logging.ERROR)

    def excepthook(exc_type, exc, tb):
        log.critical("Unhandled error", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook
    threading.excepthook = lambda a: excepthook(a.exc_type, a.exc_value, a.exc_traceback)
