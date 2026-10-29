# PyInstaller spec for the Tellbird.
# Run from the repo root:  pyinstaller packaging/tellbird.spec --noconfirm
import os
import sys
from PyInstaller.utils.hooks import collect_all

# SPECPATH (injected by PyInstaller) is this file's own directory (packaging/),
# not the CWD you ran pyinstaller from - resolve repo-root files against it so
# the build works no matter where `pyinstaller` is invoked from. abspath()
# first because SPECPATH itself can be relative (e.g. just "packaging").
ROOT = os.path.dirname(os.path.abspath(SPECPATH))


def root(*parts):
    return os.path.join(ROOT, *parts)


datas, binaries, hiddenimports = [], [], []
# Pull in everything these packages need (torch is the big one).
for pkg in ("torch", "pocket_tts", "sentencepiece", "sounddevice", "aiohttp", "certifi",
            "pystray", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

if sys.platform.startswith("linux"):
    # pystray's AppIndicator backend (real StatusNotifierItem tray icon,
    # works on KDE/GNOME under both X11 and Wayland) needs these gi.repository
    # modules; PyInstaller's built-in gi hooks bundle their typelibs/.so's
    # once they're referenced here. Harmless if AppIndicator wasn't installed
    # at build time (PyInstaller's hook just no-ops).
    hiddenimports += [
        "gi.repository.Gtk",
        "gi.repository.GLib",
        "gi.repository.GObject",
        "gi.repository.AyatanaAppIndicator3",
        "gi.repository.AppIndicator3",
    ]

# Editable defaults shipped inside the app (seeded to a user folder on first
# run - see tellbird/paths.py, which renames
# config.example.json -> config.json when it copies it out).
datas += [
    (root("config.example.json"), "."),
    (root("abbreviations.json"), "."),
    (root("emotes.json"), "."),
    (root("tellbird", "panel.html"), "tellbird"),
    (root("README.md"), "."),
]

a = Analysis(
    [root("tellbird", "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="tellbird",
    console=False,              # tray app: no console window, tray icon instead
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="tellbird")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tellbird.app",
        bundle_identifier="org.tellbird.tellbird",
        info_plist={"CFBundleName": "Tellbird"},
    )
