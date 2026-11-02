"""The system-tray icon and its menu (via pystray)."""

import asyncio
import logging
import os
import subprocess
import sys

from . import startup
from .config import ConfigError
from .paths import ICON_PATH

log = logging.getLogger("tellbird")


def _subprocess_env():
    """Environment for spawning external (non-bundled) programs. A frozen
    PyInstaller app overrides LD_LIBRARY_PATH/DYLD_LIBRARY_PATH so its own
    bundled shared libs (often older than the system's) take priority for
    itself - but that leaks into any subprocess we spawn too, breaking system
    tools like xdg-open/kde-open that need the SYSTEM's newer libs (e.g.
    libstdc++). PyInstaller's bootloader stashes the pre-override value in
    *_ORIG for exactly this case."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            orig = env.pop(f"{var}_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
    return env


def open_path(path):
    """Open a file/folder/URL with the OS's default handler."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], env=_subprocess_env())
        else:
            subprocess.Popen(["xdg-open", str(path)], env=_subprocess_env())
    except Exception as e:
        log.warning(f"[tray] couldn't open {path}: {e}")


def _icon_image():
    from PIL import Image
    return Image.open(ICON_PATH)


def panel_url(host, port):
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _patch_pystray_gi():
    # Work around a pystray bug (its notify_dbus helper calls
    # gi.require_version('DBus', '1.0') for a namespace that doesn't exist on
    # any distro and is never actually used afterwards - only GLib/Gio are -
    # which otherwise kills the whole AppIndicator backend with "Namespace
    # DBus not available").
    try:
        import gi
    except Exception:
        return
    orig_require_version = gi.require_version

    def require_version(namespace, version):
        if namespace == "DBus":
            return
        return orig_require_version(namespace, version)

    gi.require_version = require_version


def available():
    """True if a tray backend loads; otherwise log why and return False."""
    if sys.platform not in ("win32", "darwin"):
        _patch_pystray_gi()
    try:
        import pystray  # noqa: F401
        return True
    except Exception:
        log.exception("[tray] pystray not available, running as a plain console app:")
        return False


def _notify(icon, message):
    # Not on macOS: pystray goes through osascript there, which shows up as
    # Script Editor rather than Tellbird. The menu bar text covers it instead.
    if sys.platform == "darwin" or not icon.HAS_NOTIFICATION:
        return
    try:
        # Windows rejects anything over 256 characters.
        icon.notify(message[:200], "Tellbird")
    except Exception as e:
        log.warning(f"[tray] couldn't show a notification: {e}")


def _set_menu_bar_text(icon, text):
    """Text beside the icon, on the macOS main thread like all AppKit calls.
    Reaches into pystray for the status item, which it has no API for."""
    from PyObjCTools import AppHelper

    def apply():
        try:
            icon._status_item.button().setTitle_(text)
        except Exception as e:
            log.warning(f"[tray] couldn't set the menu bar text: {e}")

    AppHelper.callAfter(apply)


def _startup_listener(icon):
    """-> a Startup listener keeping the icon up to date with the stage."""
    shown = set()

    def on_change(status):
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper
            AppHelper.callAfter(icon.update_menu)
            _set_menu_bar_text(icon, "Downloading…" if status.stage == startup.DOWNLOADING else "")
        else:
            icon.update_menu()
        message = status.announcement()
        if message and message not in shown:
            shown.add(message)
            _notify(icon, message)

    return on_change


def run_failed(paths, error):
    """Blocking: a tray icon that only reports why Tellbird can't run."""
    import pystray

    def setup(icon):
        icon.visible = True
        _notify(icon, error)

    menu = pystray.Menu(
        pystray.MenuItem("Tellbird couldn't start", None, enabled=False),
        pystray.MenuItem("Open config folder", lambda icon, item: open_path(paths.app_dir)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: (icon.stop(), os._exit(0))),
    )
    pystray.Icon("tellbird", _icon_image(), "Tellbird", menu).run(setup=setup)


def run(bot):
    """Blocking: runs the tray icon on the calling (main) thread while
    bot.run() runs on bot.loop in another thread."""
    import pystray

    def submit(coro):
        if bot.loop:
            return asyncio.run_coroutine_threadsafe(coro, bot.loop)
        coro.close()

    async def reload():
        try:
            await bot.reload_config()
        except ConfigError as e:
            log.error(f"[config] reload failed: {e}")

    def on_skip(icon, item):
        if bot.loop:
            bot.loop.call_soon_threadsafe(bot.skip)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    s = bot.settings
    items = [
        pystray.MenuItem(lambda item: bot.startup.menu_text(s["mode"]), None, enabled=False),
        pystray.MenuItem("Open Tellbird panel",
                         lambda icon, item: open_path(panel_url(s["http_host"], s["http_port"]))),
        pystray.MenuItem("Reload config", lambda icon, item: submit(reload()),
                         enabled=lambda item: bot.speech_ready),
        pystray.MenuItem("Open config folder", lambda icon, item: open_path(bot.paths.app_dir)),
        pystray.MenuItem("Open log folder", lambda icon, item: open_path(bot.paths.app_dir)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Skip current", on_skip),
        pystray.MenuItem("Clear queue", lambda icon, item: submit(bot.clear())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    ]

    def setup(icon):
        icon.visible = True
        bot.startup.subscribe(_startup_listener(icon))

    pystray.Icon("tellbird", _icon_image(), "Tellbird", pystray.Menu(*items)).run(setup=setup)
