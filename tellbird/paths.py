"""Where Tellbird's editable files live.

Running from source: next to the code, in the repo root.
Installed (frozen) app: a per-user folder, seeded once from the defaults
bundled inside the app, so settings survive updates.
"""

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent


@dataclass
class Paths:
    app_dir: Path
    log_file: Path | None = None
    # Things worth logging once logging is set up (it isn't yet when this runs).
    notes: list[str] = field(default_factory=list)

    @property
    def config(self):
        return self.app_dir / "config.json"

    @property
    def abbreviations(self):
        return self.app_dir / "abbreviations.json"

    @property
    def emotes(self):
        return self.app_dir / "emotes.json"

    @property
    def panel_html(self):
        return PACKAGE_DIR / "panel.html"


def _user_config_base():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def user_config_dir():
    return _user_config_base() / "tellbird"


def migrate_legacy_config_dir(new_dir):
    """One-time move of the per-user folder from the pre-rename "Twitch TTS
    Bot" days, so existing users keep their config, word lists and log.
    Returns the old path if it moved something, else None."""
    old_dir = _user_config_base() / "twitch-tts-bot"
    if new_dir.exists() or not old_dir.is_dir():
        return None
    try:
        shutil.move(str(old_dir), str(new_dir))
    except OSError:
        return None
    old_log = new_dir / "tts_bot.log"
    if old_log.exists():
        try:
            old_log.rename(new_dir / "tellbird.log")
        except OSError:
            pass
    return old_dir


def _seed(app_dir, res_dir):
    """Copy the default files into app_dir unless the user already has them.
    config.example.json is the template for config.json."""
    for dst_name, src_name in (("config.json", "config.example.json"),
                               ("abbreviations.json", "abbreviations.json"),
                               ("emotes.json", "emotes.json")):
        dst, src = app_dir / dst_name, res_dir / src_name
        if not dst.exists() and src.exists():
            shutil.copy(src, dst)


def prepare():
    """Resolve (and if needed create, migrate and seed) the config folder."""
    if not FROZEN:
        paths = Paths(SOURCE_ROOT)
        if not paths.config.exists() and (SOURCE_ROOT / "config.example.json").exists():
            shutil.copy(SOURCE_ROOT / "config.example.json", paths.config)
        return paths

    app_dir = user_config_dir()
    paths = Paths(app_dir, log_file=app_dir / "tellbird.log")
    migrated_from = migrate_legacy_config_dir(app_dir)
    if migrated_from:
        paths.notes.append(f"Moved settings from old folder {migrated_from}")
    app_dir.mkdir(parents=True, exist_ok=True)
    _seed(app_dir, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    paths.notes.append(f"Config folder: {app_dir}")
    return paths
