"""A small window shown while Tellbird starts, closed once it's ready.

Kept apart on purpose: it runs as its own process (the tray owns the main
thread, and on macOS any window needs it too) and only talks to the app the
way the panel does, through the local server's /api/status and /api/retry.
If anything about it fails, Tellbird runs as if it didn't exist. To remove
the feature, delete this file, the launch() call in app.main(), the
"--splash" branch in __main__.py and the show_splash setting."""

import json
import subprocess
import sys
import threading
import time
import urllib.request

from .paths import FROZEN, ICON_PATH, SOURCE_ROOT

POLL_SECONDS = 0.25
WAIT_FOR_SERVER = 30     # never reached the app: it isn't coming, so close
LOST_SERVER = 3          # reached it, then lost it: Tellbird quit, so close
READY_LINGER_MS = 600    # "Ready" stays up this long before closing
CONNECTING_GIVE_UP = 10  # speech works; Twitch keeps retrying in the tray

# The panel's colours.
SURFACE, FIELD, LINE = "#191c2c", "#0d0f19", "#2a2f48"
INK, MUTED, BRAND, BRAND_HOVER, RED = "#eef0fa", "#959cb8", "#4d66ff", "#5c74ff", "#ff6b6b"
WIDTH = 340
LOGO_SIZE = 64


def launch(url):
    """Start the splash for the app served at `url`. Never raises."""
    if FROZEN:
        cmd = [sys.executable, "--splash", url]
    else:
        cmd = [sys.executable, "-m", "tellbird.splash", url]
    try:
        proc = subprocess.Popen(cmd, cwd=SOURCE_ROOT, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    # Reap it when it exits, so it doesn't linger as a zombie process.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def describe(status):
    """What to show for a /api/status answer (None: no answer yet)."""
    if status is None:
        return {"title": "Starting…", "detail": "", "progress": "busy"}
    stage = status.get("stage")
    if stage == "failed":
        return {"title": "Tellbird couldn't start", "detail": status.get("error") or "",
                "progress": None, "failed": True}
    if stage == "ready":
        return {"title": "Ready", "detail": "", "progress": 1.0, "ready": True}
    if stage == "downloading" and status.get("total"):
        mb = round(status["done"] / 1e6), round(status["total"] / 1e6)
        return {"title": status.get("message", ""),
                "detail": f"{mb[0]} of {mb[1]} MB. This only happens once.",
                "progress": status["done"] / status["total"]}
    detail = ""
    if stage == "connecting":
        detail = "Tellbird keeps trying in the background."
    return {"title": status.get("message", ""), "detail": detail, "progress": "busy"}


class _Poller:
    """Fetches /api/status on a background thread, so a slow answer never
    freezes the window."""

    def __init__(self, url):
        self.url = url
        # A proxy would answer for localhost itself, or fail.
        self._open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self.status = None
        self.started = time.monotonic()
        self.last_ok = None

    def run(self):
        while True:
            try:
                with self._open(self.url + "api/status", timeout=2) as r:
                    self.status = json.load(r)
                self.last_ok = time.monotonic()
            except Exception:
                pass
            time.sleep(POLL_SECONDS)

    def gone(self):
        """True once the app is clearly not (or no longer) there."""
        now = time.monotonic()
        if self.last_ok is None:
            return now - self.started > WAIT_FOR_SERVER
        return now - self.last_ok > LOST_SERVER

    def retry(self):
        def post():
            try:
                req = urllib.request.Request(self.url + "api/retry", data=b"", method="POST")
                self._open(req, timeout=2).close()
            except Exception:
                pass
        threading.Thread(target=post, daemon=True).start()


def _logo(icon):
    """The app icon at LOGO_SIZE. Its corners are opaque white (made for the
    tray), which would show as a white frame here, so they're cut away."""
    try:
        from PIL import Image, ImageDraw, ImageTk
        im = Image.open(ICON_PATH).convert("RGBA")
        last = im.width - 1
        for corner in ((0, 0), (last, 0), (0, last), (last, last)):
            ImageDraw.floodfill(im, corner, (0, 0, 0, 0), thresh=100)
        return ImageTk.PhotoImage(im.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS))
    except Exception:
        return icon.subsample(max(1, icon.width() // LOGO_SIZE))


def _window(url):
    import tkinter as tk
    from tkinter import font as tkfont

    if sys.platform == "win32":
        try:                                   # otherwise blurry on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    poller = _Poller(url)
    threading.Thread(target=poller.run, daemon=True).start()

    root = tk.Tk(className="Tellbird")
    root.title("Tellbird")
    root.configure(bg=SURFACE, padx=32, pady=26)
    root.resizable(False, False)
    icon = tk.PhotoImage(file=str(ICON_PATH))
    root.iconphoto(True, icon)

    family = tkfont.nametofont("TkDefaultFont").actual("family")
    big = tkfont.Font(family=family, size=17, weight="bold")
    medium = tkfont.Font(family=family, size=11, weight="bold")
    small = tkfont.Font(family=family, size=10)

    logo = _logo(icon)
    tk.Label(root, image=logo, bg=SURFACE).pack()
    tk.Label(root, text="Tellbird", font=big, fg=INK, bg=SURFACE).pack(pady=(8, 14))
    title = tk.Label(root, font=medium, fg=INK, bg=SURFACE)
    title.pack()
    bar = tk.Canvas(root, width=WIDTH, height=6, bg=FIELD, highlightthickness=0)
    bar.pack(pady=(12, 8))
    fill = bar.create_rectangle(0, 0, 0, 6, fill=BRAND, width=0)
    detail = tk.Label(root, font=small, fg=MUTED, bg=SURFACE, wraplength=WIDTH, justify="center")
    detail.pack()

    buttons = tk.Frame(root, bg=SURFACE)

    def button(text, command, primary):
        bg = BRAND if primary else SURFACE
        return tk.Button(buttons, text=text, command=command, font=small, fg=INK, bg=bg,
                         activebackground=BRAND_HOVER if primary else LINE,
                         activeforeground=INK, relief="flat", bd=0, padx=14, pady=5,
                         highlightthickness=1, highlightbackground=LINE, cursor="hand2")

    button("Retry", poller.retry, True).pack(side="left", padx=4)
    button("Close", root.destroy, False).pack(side="left", padx=4)

    # Centre on screen once the size is known.
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.lift()
    if sys.platform == "darwin":               # a freshly started app opens behind others there
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))

    phase = 0.0
    connecting_since = None
    closing = False

    def tick():
        nonlocal phase, connecting_since, closing
        if poller.gone():
            root.destroy()
            return
        status = poller.status
        view = describe(status)
        title.config(text=view["title"])
        detail.config(text=view["detail"], fg=RED if view.get("failed") else MUTED)
        progress = view["progress"]
        if progress is None:
            bar.pack_forget()
        else:
            if not bar.winfo_ismapped():
                bar.pack(pady=(12, 8), before=detail)
            if progress == "busy":             # a segment sweeping across
                phase = (phase + 0.012) % 1.3
                x = (phase - 0.3) * WIDTH
                bar.coords(fill, max(0, x), 0, min(WIDTH, x + 0.3 * WIDTH), 6)
            else:
                bar.coords(fill, 0, 0, progress * WIDTH, 6)
        if view.get("failed"):
            if not buttons.winfo_ismapped():
                buttons.pack(pady=(16, 0))
        elif buttons.winfo_ismapped():
            buttons.pack_forget()
        if status and status.get("stage") == "connecting" and status.get("speech_ready"):
            connecting_since = connecting_since or time.monotonic()
        else:
            connecting_since = None
        done = view.get("ready") or (
            connecting_since and time.monotonic() - connecting_since > CONNECTING_GIVE_UP)
        if done and not closing:
            closing = True
            root.after(READY_LINGER_MS, root.destroy)
        root.after(30, tick)

    tick()
    root.mainloop()


def main(url):
    try:
        _window(url)
    except Exception:
        # A splash is never worth an error dialog; from source, show why.
        if not FROZEN:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv[1])
