#!/usr/bin/env python3
"""
================================================================================
 Tellbird  --  config-driven, runs as a system-tray app
================================================================================
 All settings live in config.json (next to this file). Change them on the
 control panel's Settings tab (http://127.0.0.1:5050/), which applies them
 right away, or edit the file by hand and use "Reload" on the panel or tray.
 Synthesis: Kyutai Pocket TTS (built-in voice, fully local after first download).
 Output:    via 'sounddevice' to a named device (Mac: BlackHole, OBS captures it).

 config.json keys:
   mode                "twitch" (read chat directly) or "http" (Firebot, Mix It
                       Up, or anything else POSTs/GETs text to us)
   channel             your Twitch channel, lowercase, no '#' (twitch mode)
   voice               built-in voice name, e.g. alba, anna, michael, vera, jane
   language            "english"
   output_device       device name substring, or null for the system default
   read_username       true/false - speak the sender's name (twitch mode only)
   username_format     template, e.g. "{user} says: {text}" (twitch mode only)
   min_chars/max_chars length limits
   cooldown_seconds    minimum seconds between messages per user (0 = off)
   tail_padding_seconds trailing silence so endings aren't clipped
   ignored_users       bot accounts to skip
   ignore_prefixes     message prefixes treated as commands and skipped
   filters             optional anti-abuse filters (repeated words/chars, word
                       spam, over-long words, copypasta) - each switchable
   firebot_actions     optional: run a Firebot preset effect list when a filter
                       fires (maps each filter to a preset list ID)
   http_host/http_port local HTTP server address (http mode)
   show_tray_icon      true/false - run as a system-tray app (default true);
                       falls back to a plain console loop if the tray backend
                       isn't available (e.g. headless Linux)

 Run:
   python3 tellbird.py

 Tray menu (when show_tray_icon is on): open the control panel, reload the
 config, open the config folder, open the log folder, skip the current
 message, clear the queue, quit.

 Control endpoints (a small local server runs in BOTH modes):
   http://127.0.0.1:5050/        control panel: test speech + edit settings
   http://127.0.0.1:5050/skip    abort the current message, play the next
   http://127.0.0.1:5050/clear   drop the whole queue and abort the current
   http://127.0.0.1:5050/tts     speak text (used in "http" mode)

   /tts accepts:
     GET  ?user=<name>&text=<message>
     POST application/json       {"user": "...", "text": "..."}
     POST form-urlencoded        user=...&text=...
     POST anything else          the raw body is spoken as-is, username (if
                                  any) via ?user= in the URL - use this from
                                  tools that can't escape quotes/newlines into
                                  JSON or a form body, e.g. Mix It Up's HTTP
                                  Custom Request action: POST the raw $message
                                  as the body with ?user=$username in the URL.

   Settings API (used by the panel, same-origin only):
     GET  /api/config      schema, saved values, abbreviations, emotes
     POST /api/config      {"values": {"dotted.key": value}, "abbreviations":
                           {...}, "emotes": [...]} - validate, save, apply
     POST /api/reload      re-read the files from disk and apply them
================================================================================
"""

import asyncio
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from pocket_tts import TTSModel


def _user_config_base():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _user_config_dir():
    return _user_config_base() / "tellbird"


def _migrate_legacy_config_dir(new_dir):
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


class _Tee:
    """Writes to every given stream, skipping any that are None (e.g. a
    windowed Windows build has no real sys.stdout) or that error out."""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


LOG_FILE = None
if getattr(sys, "frozen", False):
    # Installed/frozen app: keep editable config + JSON in a per-user folder,
    # seeded once from the files bundled inside the app.
    _RES_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = _user_config_dir()
    _migrated_from = _migrate_legacy_config_dir(APP_DIR)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # Always keep a log file, on every OS - not just when sys.stdout is None
    # (that only happens on windowed Windows builds; on Linux/macOS a
    # console=False build can still have a real, just-not-visible stdout, so
    # a crash there would otherwise vanish with nothing to inspect). Tee so a
    # terminal launch still shows live output too.
    LOG_FILE = APP_DIR / "tellbird.log"
    _log_fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, _log_fh)
    sys.stderr = _Tee(sys.stderr, _log_fh)
    for _dst_fn, _src_fn in (("config.json", "config.example.json"),
                              ("abbreviations.json", "abbreviations.json"),
                              ("emotes.json", "emotes.json")):
        _dst = APP_DIR / _dst_fn
        if not _dst.exists() and (_RES_DIR / _src_fn).exists():
            shutil.copy(_RES_DIR / _src_fn, _dst)
    if _migrated_from:
        print(f"Moved settings from old folder {_migrated_from}")
    print(f"Config folder: {APP_DIR}")
    PANEL_HTML_PATH = _RES_DIR / "panel.html"
else:
    APP_DIR = Path(__file__).parent
    PANEL_HTML_PATH = APP_DIR / "panel.html"
    if not (APP_DIR / "config.json").exists() and (APP_DIR / "config.example.json").exists():
        shutil.copy(APP_DIR / "config.example.json", APP_DIR / "config.json")

CONFIG_FILE = APP_DIR / "config.json"
ABBR_FILE = APP_DIR / "abbreviations.json"
EMOTE_FILE = APP_DIR / "emotes.json"

class ConfigError(Exception):
    pass


def _strip_comments(raw):
    # Drop whole-line // comments so config.json can be annotated.
    return "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("//"))


def _load_json(path, default, allow_comments=False):
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return default
    if allow_comments:
        raw = _strip_comments(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name} is not valid JSON: {e}")


# ------------------------------ Config schema --------------------------------
# Every key config.json understands, with its type, default and help text.
# This drives loading/validation AND the settings page of the web panel.
# "restart": the running bot only picks the value up after a restart; every
# other key is applied live by a reload (web panel or tray menu).
KNOWN_VOICES = ["alba", "anna", "michael", "vera", "jane", "eve", "george",
                "mary", "paul", "charles"]
FIREBOT_FILTERS = ["word_spam", "long_word", "repeated_message", "copypasta_flood",
                   "char_run", "word_repeat", "phrase_repeat"]


def _field(section, key, type_, default, label, help="", **extra):
    return {"section": section, "key": key, "type": type_, "default": default,
            "label": label, "help": help, **extra}


CONFIG_SCHEMA = [
    _field("General", "mode", "choice", "twitch", "Mode",
           '"twitch" reads your Twitch chat directly. "http" only speaks text that '
           "Firebot, Mix It Up, etc. send to /tts.",
           choices=["twitch", "http"], restart=True),
    _field("General", "channel", "str", "yourchannelname", "Twitch channel",
           "Lowercase, no '#'. Twitch mode only."),
    _field("General", "voice", "str", "alba", "Voice",
           "Built-in Pocket TTS voice name.", suggest="voices"),
    _field("General", "language", "str", "english", "Model language",
           'Keep "english" unless you deliberately use another model.', restart=True),
    _field("General", "output_device", "nullable_str", None, "Output device",
           "Device name (substring match). Empty = system default. macOS: "
           '"BlackHole 2ch", Windows: "CABLE Input".', suggest="devices"),
    _field("General", "show_tray_icon", "bool", True, "Show tray icon",
           "Run as a system-tray app instead of a plain console window.",
           restart=True),

    _field("Speech", "read_username", "bool", True, "Read username",
           "Speak the sender's name before the message (twitch mode only)."),
    _field("Speech", "username_format", "str", "{user} says: {text}", "Username format",
           "How name and message are combined. Placeholders: {user} and {text}."),
    _field("Speech", "min_chars", "int", 1, "Minimum characters",
           "Skip messages shorter than this.", min=0),
    _field("Speech", "max_chars", "int", 300, "Maximum characters",
           "Shorten messages longer than this.", min=1),
    _field("Speech", "cooldown_seconds", "float", 0, "Per-user cooldown (s)",
           "Minimum seconds between spoken messages per user. 0 = off.", min=0),
    _field("Speech", "tail_padding_seconds", "float", 0.4, "Tail padding (s)",
           "Silence added after each message so the last word isn't clipped.",
           min=0),
    _field("Speech", "ignored_users", "list",
           ["nightbot", "streamelements", "streamlabs", "moobot"], "Ignored users",
           "Accounts whose messages are never read (one per line)."),
    _field("Speech", "ignore_prefixes", "list", ["!", "/"], "Ignored prefixes",
           "Messages starting with any of these are treated as commands and "
           "skipped (one per line)."),

    _field("Filters", "filters.collapse_char_runs", "bool", True, "Collapse character runs",
           '"aaaaaa" -> "aaa", "!!!!!" -> "!!!".'),
    _field("Filters", "filters.max_char_run", "int", 3, "Max character run", min=1),
    _field("Filters", "filters.collapse_word_repeats", "bool", True, "Collapse repeated words",
           '"lol lol lol lol lol" -> "lol lol lol".'),
    _field("Filters", "filters.max_word_repeats", "int", 3, "Max word repeats", min=1),
    _field("Filters", "filters.collapse_phrase_repeats", "bool", True,
           "Collapse repeated phrases",
           '"go team go team go team go team" -> "go team go team go team".'),
    _field("Filters", "filters.max_phrase_words", "int", 4, "Max phrase length (words)",
           min=2),
    _field("Filters", "filters.max_phrase_repeats", "int", 3, "Max phrase repeats", min=1),
    _field("Filters", "filters.skip_word_spam", "bool", False, "Skip word spam",
           "Skip a message when one word makes up most of it."),
    _field("Filters", "filters.spam_min_words", "int", 6, "Word spam: minimum words",
           min=1),
    _field("Filters", "filters.spam_ratio", "float", 0.6, "Word spam: ratio",
           "0.6 = one word is at least 60% of all words.", min=0, max=1),
    _field("Filters", "filters.max_word_length", "int", 0, "Max word length",
           "Skip messages containing a longer word. 0 = off.", min=0),
    _field("Filters", "filters.block_repeated_messages", "bool", False,
           "Block repeated messages",
           "Skip a message identical to that user's previous one."),
    _field("Filters", "filters.block_copypasta_flood", "bool", False,
           "Block copypasta floods",
           "Skip a message once several chatters sent the exact same text "
           "within the window."),
    _field("Filters", "filters.copypasta_flood_window_seconds", "float", 20,
           "Copypasta window (s)", min=0),
    _field("Filters", "filters.copypasta_flood_min_users", "int", 3,
           "Copypasta: minimum chatters", min=2),

    _field("Firebot actions", "firebot_actions.enabled", "bool", False, "Enabled",
           "Run a Firebot Preset Effect List when a filter fires."),
    _field("Firebot actions", "firebot_actions.api_base", "str", "http://localhost:7472",
           "Firebot API base"),
    _field("Firebot actions", "firebot_actions.async", "bool", True, "Don't wait for effects",
           "Fire and forget (recommended)."),
    *[_field("Firebot actions", f"firebot_actions.presets.{name}", "str", "",
             f"Preset: {name}", "Preset Effect List ID. Empty = no action.")
      for name in FIREBOT_FILTERS],

    _field("Server", "http_host", "str", "127.0.0.1", "HTTP host",
           "Address the local server (this panel, /tts, /skip, /clear) listens on.",
           restart=True),
    _field("Server", "http_port", "int", 5050, "HTTP port", min=1, max=65535,
           restart=True),
]
SCHEMA_BY_KEY = {f["key"]: f for f in CONFIG_SCHEMA}


def _get_path(cfg, key):
    for part in key.split("."):
        if not isinstance(cfg, dict) or part not in cfg:
            raise KeyError(key)
        cfg = cfg[part]
    return cfg


def _coerce(field, value, strict):
    """Convert a raw value to the field's type. strict=True (values coming
    from the panel) rejects out-of-range numbers; strict=False (values read
    from config.json) clamps them, like the bot always has."""
    t = field["type"]
    if t == "bool":
        if isinstance(value, str) and not strict:
            return value.strip().lower() in ("true", "1", "yes", "on")
        if not isinstance(value, (bool, int)):
            raise ValueError("must be true or false")
        return bool(value)
    if t in ("int", "float"):
        if isinstance(value, bool):
            raise ValueError("must be a number")
        try:
            num = int(value) if t == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"must be {'a whole number' if t == 'int' else 'a number'}")
        lo, hi = field.get("min"), field.get("max")
        if strict and ((lo is not None and num < lo) or (hi is not None and num > hi)):
            raise ValueError(f"must be between {lo} and {hi}" if hi is not None
                             else f"must be at least {lo}")
        if lo is not None:
            num = max(lo, num)
        if hi is not None:
            num = min(hi, num)
        return num
    if t == "list":
        if not isinstance(value, list):
            raise ValueError("must be a list")
        return [str(v) for v in value if str(v).strip()]
    if t == "nullable_str":
        return (str(value).strip() or None) if value is not None else None
    value = str(value) if value is not None else ""
    if t == "choice" and value not in field["choices"]:
        raise ValueError(f"must be one of: {', '.join(field['choices'])}")
    if field["key"] == "username_format":
        try:
            value.format(user="u", text="t")
        except (KeyError, IndexError, ValueError):
            raise ValueError("only the placeholders {user} and {text} are allowed")
    return value


def _resolve_config(cfg):
    """config.json contents -> {key: value} for every schema key, using the
    default for missing/invalid values. Returns (values, warnings)."""
    values, warnings = {}, []
    for f in CONFIG_SCHEMA:
        try:
            raw = _get_path(cfg, f["key"])
        except KeyError:
            values[f["key"]] = f["default"]
            continue
        try:
            values[f["key"]] = _coerce(f, raw, strict=False)
        except ValueError as e:
            warnings.append(f"{f['key']}: {e} - using default {f['default']!r}")
            values[f["key"]] = f["default"]
    return values, warnings


def _read_config_file():
    cfg = _load_json(CONFIG_FILE, {}, allow_comments=True)
    if not isinstance(cfg, dict):
        raise ConfigError("config.json must contain a JSON object")
    return _resolve_config(cfg)


_active: dict = {}    # key -> value currently in effect
_startup: dict = {}   # values of the restart-only keys the bot started with


def _apply_config(values):
    """Make `values` (from _resolve_config) the running config. Restart-only
    keys are kept at their startup value. Returns the set of changed keys."""
    global MODE, CHANNEL, VOICE, LANGUAGE, OUTPUT_DEVICE, READ_USERNAME
    global USERNAME_FORMAT, MIN_CHARS, MAX_CHARS, COOLDOWN_SECONDS
    global TAIL_PADDING_SECONDS, IGNORED_USERS, IGNORE_PREFIXES, HTTP_HOST
    global HTTP_PORT, SHOW_TRAY_ICON
    global F_COLLAPSE_CHARS, F_MAX_CHAR_RUN, F_COLLAPSE_WORDS, F_MAX_WORD_REPEATS
    global F_COLLAPSE_PHRASES, F_MAX_PHRASE_WORDS, F_MAX_PHRASE_REPEATS
    global F_SKIP_SPAM, F_SPAM_MIN_WORDS, F_SPAM_RATIO, F_MAX_WORD_LENGTH
    global F_BLOCK_REPEATS, F_COPYPASTA_FLOOD, F_COPYPASTA_WINDOW, F_COPYPASTA_MIN_USERS
    global FB_ENABLED, FB_API_BASE, FB_ASYNC, FB_PRESETS

    if not _startup:
        _startup.update({k: v for k, v in values.items() if SCHEMA_BY_KEY[k].get("restart")})
    v = {**values, **_startup}
    changed = {k for k in v if _active.get(k, object()) != v[k]}
    _active.clear()
    _active.update(v)

    MODE = v["mode"]
    CHANNEL = v["channel"].lower().lstrip("#")
    VOICE = v["voice"]
    LANGUAGE = v["language"]
    OUTPUT_DEVICE = v["output_device"]
    READ_USERNAME = v["read_username"]
    USERNAME_FORMAT = v["username_format"]
    MIN_CHARS = v["min_chars"]
    MAX_CHARS = v["max_chars"]
    COOLDOWN_SECONDS = v["cooldown_seconds"]
    TAIL_PADDING_SECONDS = v["tail_padding_seconds"]
    IGNORED_USERS = {u.lower() for u in v["ignored_users"]}
    IGNORE_PREFIXES = tuple(v["ignore_prefixes"])
    HTTP_HOST = v["http_host"]
    HTTP_PORT = v["http_port"]
    SHOW_TRAY_ICON = v["show_tray_icon"]

    # Optional anti-abuse filters
    F_COLLAPSE_CHARS = v["filters.collapse_char_runs"]
    F_MAX_CHAR_RUN = v["filters.max_char_run"]
    F_COLLAPSE_WORDS = v["filters.collapse_word_repeats"]
    F_MAX_WORD_REPEATS = v["filters.max_word_repeats"]
    F_COLLAPSE_PHRASES = v["filters.collapse_phrase_repeats"]
    F_MAX_PHRASE_WORDS = v["filters.max_phrase_words"]
    F_MAX_PHRASE_REPEATS = v["filters.max_phrase_repeats"]
    F_SKIP_SPAM = v["filters.skip_word_spam"]
    F_SPAM_MIN_WORDS = v["filters.spam_min_words"]
    F_SPAM_RATIO = v["filters.spam_ratio"]
    F_MAX_WORD_LENGTH = v["filters.max_word_length"]            # 0 = off
    F_BLOCK_REPEATS = v["filters.block_repeated_messages"]
    F_COPYPASTA_FLOOD = v["filters.block_copypasta_flood"]
    F_COPYPASTA_WINDOW = v["filters.copypasta_flood_window_seconds"]
    F_COPYPASTA_MIN_USERS = v["filters.copypasta_flood_min_users"]

    # Firebot actions
    FB_ENABLED = v["firebot_actions.enabled"]
    FB_API_BASE = v["firebot_actions.api_base"].rstrip("/")
    FB_ASYNC = v["firebot_actions.async"]
    FB_PRESETS = {name: v[f"firebot_actions.presets.{name}"] for name in FIREBOT_FILTERS
                  if v[f"firebot_actions.presets.{name}"]}
    return changed


def restart_pending(values):
    """Restart-only keys whose saved value differs from what's running."""
    return [k for k, val in _startup.items() if values.get(k) != val]


# ---------------------- Comment-preserving config writer ---------------------
def _scan_members(raw):
    """Locate object members in raw config text (which may contain // line
    comments). Returns ({key: (start, end) of its value text},
    {object key: position just after its last member, or after '{' if empty}).
    Keys are dotted paths, "" is the top-level object."""
    spans, ends = {}, {}
    n, i = len(raw), 0

    def skip_ws():
        nonlocal i
        while i < n:
            if raw[i] in " \t\r\n":
                i += 1
            elif raw.startswith("//", i):
                j = raw.find("\n", i)
                i = n if j < 0 else j
            else:
                break

    def string_end(j):
        j += 1
        while raw[j] != '"':
            j += 2 if raw[j] == "\\" else 1
        return j + 1

    def value(path):
        nonlocal i
        skip_ws()
        start = i
        if raw[i] == "{":
            i += 1
            ends[path] = i
            skip_ws()
            if raw[i] == "}":
                i += 1
            else:
                while True:
                    skip_ws()
                    k_end = string_end(i)
                    key = json.loads(raw[i:k_end])
                    i = k_end
                    skip_ws()
                    i += 1                                  # ':'
                    value(f"{path}.{key}" if path else key)
                    ends[path] = i
                    skip_ws()
                    i += 1                                  # ',' or '}'
                    if raw[i - 1] == "}":
                        break
        elif raw[i] == "[":
            i += 1
            skip_ws()
            if raw[i] == "]":
                i += 1
            else:
                while True:
                    value(None)
                    skip_ws()
                    i += 1                                  # ',' or ']'
                    if raw[i - 1] == "]":
                        break
        elif raw[i] == '"':
            i = string_end(i)
        else:
            i = re.compile(r"[-+\w.]+").match(raw, i).end()
        if path is not None:
            spans[path] = (start, i)

    value("")
    return spans, ends


def write_config(updates):
    """Write {dotted key: value} into config.json, replacing just those values
    in place so the user's comments and layout survive. Keys missing from the
    file are appended to their (possibly new) parent object."""
    raw = CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else "{\n}\n"
    spans, ends = _scan_members(raw)
    edits = []                    # (start, end, replacement)
    inserts = {}                  # existing parent -> {missing key: value}
    for key, val in updates.items():
        if key in spans:
            edits.append((*spans[key], json.dumps(val, ensure_ascii=False)))
            continue
        parts = key.split(".")
        depth = len(parts) - 1
        while ".".join(parts[:depth]) not in ends:
            depth -= 1
        node = inserts.setdefault(".".join(parts[:depth]), {})
        for part in parts[depth:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = val
    for parent, members in inserts.items():
        pos = ends[parent]
        indent = "  " * (parent.count(".") + 2 if parent else 1)
        text = ("" if raw[pos - 1] == "{" else ",") + "".join(
            f"{',' if idx else ''}\n{indent}{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
            for idx, (k, v) in enumerate(members.items()))
        if raw[pos - 1] == "{":
            text += "\n" + indent[:-2]
        edits.append((pos, pos, text))
    for start, end, text in sorted(edits, key=lambda e: e[0], reverse=True):
        raw = raw[:start] + text + raw[end:]
    # Sanity check before touching the real file.
    check = json.loads(_strip_comments(raw))
    for key, val in updates.items():
        if _get_path(check, key) != val:
            raise ConfigError(f"couldn't write {key} into config.json")
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(raw, encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


# --------------------------- Load configuration ------------------------------
try:
    _values, _warnings = _read_config_file()
except ConfigError as e:
    raise SystemExit(str(e))
for _w in _warnings:
    print(f"[config] {_w}")
_apply_config(_values)
# -----------------------------------------------------------------------------

TWITCH_WS = "wss://irc-ws.chat.twitch.tv:443"

# Build an SSL context from certifi so wss:// works even when macOS Python has
# no local CA certificates (the common "CERTIFICATE_VERIFY_FAILED" on Mac).
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CONTEXT = ssl.create_default_context()

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
PRIVMSG_RE = re.compile(r":(\w+)!\w+@[\w.]+ PRIVMSG #\w+ :(.*)")
_last_spoken: dict[str, float] = {}


# --------------------------- Abbreviations / emotes --------------------------
ABBREVIATIONS: dict = {}
EMOTES: list = []
_ABBR_PATTERN = None
_EMOTE_PATTERN = None


def _apply_word_lists(abbreviations, emotes):
    global ABBREVIATIONS, EMOTES, _ABBR_PATTERN, _EMOTE_PATTERN
    ABBREVIATIONS = {str(k).lower(): str(v) for k, v in abbreviations.items()}
    EMOTES = [str(e) for e in emotes]
    _ABBR_PATTERN = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted(ABBREVIATIONS, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    ) if ABBREVIATIONS else None
    _EMOTE_PATTERN = re.compile(
        r"\b(" + "|".join(re.escape(e) for e in sorted(EMOTES, key=len, reverse=True)) + r")\b"
    ) if EMOTES else None


def _read_word_lists():
    abbreviations = _load_json(ABBR_FILE, {}) or {}
    emotes = _load_json(EMOTE_FILE, []) or []
    if not isinstance(abbreviations, dict):
        raise ConfigError("abbreviations.json must contain a JSON object")
    if not isinstance(emotes, list):
        raise ConfigError("emotes.json must contain a JSON list")
    return abbreviations, emotes


try:
    _apply_word_lists(*_read_word_lists())
except ConfigError as e:
    raise SystemExit(str(e))


def expand_abbreviations(text):
    text = re.sub(r"\blo+l\b", "lol", text, flags=re.IGNORECASE)
    text = re.sub(r"\blma+o+\b", "lmao", text, flags=re.IGNORECASE)
    if _ABBR_PATTERN:
        text = _ABBR_PATTERN.sub(lambda m: ABBREVIATIONS[m.group(0).lower()], text)
    return text


def strip_custom_emotes(text):
    if _EMOTE_PATTERN:
        text = _EMOTE_PATTERN.sub("", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def strip_native_emotes(message, emotes_tag):
    if not emotes_tag:
        return message
    ranges = []
    for chunk in emotes_tag.split("/"):
        _id, _, positions = chunk.partition(":")
        for pos in positions.split(","):
            a, _, b = pos.partition("-")
            try:
                ranges.append((int(a), int(b)))
            except ValueError:
                pass
    if not ranges:
        return message
    chars = list(message)
    for a, b in ranges:
        for i in range(a, min(b, len(chars) - 1) + 1):
            chars[i] = None
    result = "".join(c for c in chars if c is not None)
    return re.sub(r"\s{2,}", " ", result).strip()
# -----------------------------------------------------------------------------


# ------------------------------ Abuse filters --------------------------------
# All optional, each switchable via the "filters" block in config.json
# (F_* values are set by _apply_config()).

_last_message: dict[str, str] = {}
_copypasta_history: deque = deque()   # (timestamp, normalized_text, user)


def _collapse_char_runs(text):
    # "aaaaaa" -> F_MAX_CHAR_RUN a's, "!!!!!" -> capped too
    return re.sub(r"(.)\1{%d,}" % F_MAX_CHAR_RUN,
                  lambda m: m.group(1) * F_MAX_CHAR_RUN, text)


def _collapse_word_repeats(text):
    # "lol lol lol lol" -> keep at most F_MAX_WORD_REPEATS in a row
    out, prev, run = [], None, 0
    for w in text.split():
        key = w.lower()
        run = run + 1 if key == prev else 1
        prev = key
        if run <= F_MAX_WORD_REPEATS:
            out.append(w)
    return " ".join(out)


def _collapse_phrase_repeats(text):
    # "go team go team go team go team" -> keep at most F_MAX_PHRASE_REPEATS
    # repeats of a 2..F_MAX_PHRASE_WORDS-word phrase (single-word repeats are
    # handled separately by _collapse_word_repeats).
    words = text.split()
    n = len(words)
    out, i = [], 0
    while i < n:
        matched = False
        longest = min(F_MAX_PHRASE_WORDS, (n - i) // 2)
        # shortest period first: a longer length that's just a multiple of a
        # shorter one (e.g. 4 words = two 2-word units) would "match" too but
        # isn't the real repeating unit, so it wouldn't get collapsed enough.
        for length in range(2, longest + 1):
            phrase = [w.lower() for w in words[i:i + length]]
            repeats, j = 1, i + length
            while j + length <= n and [w.lower() for w in words[j:j + length]] == phrase:
                repeats += 1
                j += length
            if repeats >= 2:
                keep = min(repeats, F_MAX_PHRASE_REPEATS)
                out.extend(words[i:i + length] * keep)
                i = j
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _is_word_spam(text):
    # True if one word makes up >= spam_ratio of a long-enough message
    words = [w.lower() for w in text.split()]
    if len(words) < F_SPAM_MIN_WORDS:
        return False
    return max(Counter(words).values()) / len(words) >= F_SPAM_RATIO


def _is_copypasta_flood(text, user):
    # True once F_COPYPASTA_MIN_USERS distinct chatters have sent the exact
    # same message within the trailing window (raid/copypasta spam) - keeps
    # the bot from reading the same line over and over during a flood.
    now = time.monotonic()
    while _copypasta_history and now - _copypasta_history[0][0] > F_COPYPASTA_WINDOW:
        _copypasta_history.popleft()
    norm = text.strip().lower()
    if not norm:
        return False
    users = {u for ts, t, u in _copypasta_history if t == norm}
    users.add(user)
    _copypasta_history.append((now, norm, user))
    return len(users) >= F_COPYPASTA_MIN_USERS


# --------------------------- Firebot actions ---------------------------------
# When a filter fires, optionally trigger a Firebot preset effect list so you
# can react (timeout, warn in chat, log, ...). Configured in config.json under
# "firebot_actions"; each filter maps to a preset list ID (empty = no action).
# Values (FB_ENABLED, FB_PRESETS, ...) are set by _apply_config().


async def _post_firebot(preset_id, user, message, filter_name):
    from aiohttp import ClientSession, ClientTimeout
    url = f"{FB_API_BASE}/api/v1/effects/preset/{preset_id}"
    if FB_ASYNC:
        url += "/run"          # async: Firebot doesn't wait for effects to finish
    payload = {"username": user or "unknown",
               "args": {"filter": filter_name, "message": message}}
    try:
        async with ClientSession(timeout=ClientTimeout(total=4)) as s:
            async with s.post(url, json=payload) as r:
                if r.status >= 400:
                    print(f"[firebot] {filter_name}: HTTP {r.status}")
    except Exception as e:
        print(f"[firebot] {filter_name}: {e}")


def _fire_firebot(filter_name, user, message):
    """Fire-and-forget: trigger the Firebot preset mapped to this filter."""
    if not FB_ENABLED:
        return
    preset = FB_PRESETS.get(filter_name)
    if not preset:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_post_firebot(preset, user, message, filter_name))
    print(f"[firebot] triggered '{filter_name}' for {user or '(unknown)'}")
# -----------------------------------------------------------------------------


def apply_filters(text, user=""):
    """Normalize spammy text; return None to skip. Fires Firebot actions."""
    if F_SKIP_SPAM and _is_word_spam(text):
        _fire_firebot("word_spam", user, text)
        return None
    if F_MAX_WORD_LENGTH > 0 and any(len(w) > F_MAX_WORD_LENGTH for w in text.split()):
        _fire_firebot("long_word", user, text)
        return None
    if F_COPYPASTA_FLOOD and _is_copypasta_flood(text, user):
        _fire_firebot("copypasta_flood", user, text)
        return None
    if F_COLLAPSE_CHARS:
        collapsed = _collapse_char_runs(text)
        if collapsed != text:
            _fire_firebot("char_run", user, text)
            text = collapsed
    if F_COLLAPSE_PHRASES:
        collapsed = _collapse_phrase_repeats(text)
        if collapsed != text:
            _fire_firebot("phrase_repeat", user, text)
            text = collapsed
    if F_COLLAPSE_WORDS:
        collapsed = _collapse_word_repeats(text)
        if collapsed != text:
            _fire_firebot("word_repeat", user, text)
            text = collapsed
    return text.strip() or None
# -----------------------------------------------------------------------------


def should_skip(user, text):
    if user in IGNORED_USERS:
        return True
    if text.startswith(IGNORE_PREFIXES):
        return True
    if COOLDOWN_SECONDS > 0 and user:
        now = time.monotonic()
        if now - _last_spoken.get(user, 0.0) < COOLDOWN_SECONDS:
            return True
        _last_spoken[user] = now
    if F_BLOCK_REPEATS and user:
        norm = text.strip().lower()
        if norm and _last_message.get(user) == norm:
            _fire_firebot("repeated_message", user, text)
            return True
        _last_message[user] = norm
    return False


def clean_text(text, user=""):
    # Split acronym-suffixed run-together words: "pocketTTS" -> "pocket TTS".
    # Requires 2+ uppercase letters after the boundary so mixed-case chat
    # noise like "loL"/"LoL" isn't shredded into fragments (which would
    # otherwise dodge the lol/repeat-word filters below).
    text = re.sub(r"(?<=[a-z])(?=[A-Z]{2})", " ", text)
    text = strip_custom_emotes(text)
    text = URL_RE.sub("link", text)
    text = text.strip()
    if len(text) < MIN_CHARS:
        return None
    text = apply_filters(text, user)    # optional anti-abuse filters (may skip)
    if text is None:
        return None
    text = expand_abbreviations(text)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + " ..."
    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def build_spoken(user, cleaned):
    if READ_USERNAME and user:
        return USERNAME_FORMAT.format(user=user, text=cleaned)
    return cleaned


# ------------------------------ Pocket TTS -----------------------------------
print("Loading Pocket TTS model ...")
tts_model = TTSModel.load_model(language=LANGUAGE)
print(f"Loading voice: {VOICE}")
voice_state = tts_model.get_state_for_audio_prompt(VOICE)
SAMPLE_RATE = tts_model.sample_rate


def _pick_playback_rate():
    """Some output devices (typically a raw ALSA device, not going through
    PipeWire/PulseAudio's automatic resampling) reject the TTS model's native
    sample rate outright (PortAudio error -9997 "Invalid sample rate").
    Detect that at startup (and when the device changes) and resample instead
    of failing every line."""
    try:
        sd.check_output_settings(device=OUTPUT_DEVICE, samplerate=SAMPLE_RATE)
        return SAMPLE_RATE
    except Exception:
        pass
    try:
        rate = int(sd.query_devices(OUTPUT_DEVICE, "output")["default_samplerate"])
        print(f"[audio] output device doesn't support {SAMPLE_RATE} Hz, resampling to {rate} Hz")
        return rate
    except Exception:
        return SAMPLE_RATE


PLAYBACK_SAMPLE_RATE = _pick_playback_rate()
print(f"Pocket TTS ready. Mode: {MODE} | Output: {OUTPUT_DEVICE or 'default'}")

speech_queue: asyncio.Queue = asyncio.Queue()
_loop: asyncio.AbstractEventLoop | None = None  # set once main() starts; used by the tray


async def _drain_queue():
    """Drop everything queued and abort the current message. Returns count dropped."""
    dropped = 0
    while not speech_queue.empty():
        try:
            speech_queue.get_nowait()
            speech_queue.task_done()
            dropped += 1
        except asyncio.QueueEmpty:
            break
    sd.stop()
    return dropped


def _resample_linear(data, orig_rate, target_rate):
    if orig_rate == target_rate or len(data) == 0:
        return data
    duration = len(data) / orig_rate
    n_target = max(1, int(round(duration * target_rate)))
    x_orig = np.linspace(0, duration, num=len(data), endpoint=False)
    x_target = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_target, x_orig, data).astype("float32")


def speak_blocking(text):
    audio = tts_model.generate_audio(voice_state, text)
    data = np.clip(audio.numpy(), -1.0, 1.0).astype("float32")
    pad = np.zeros(int(SAMPLE_RATE * TAIL_PADDING_SECONDS), dtype="float32")
    data = np.concatenate([data, pad])
    if PLAYBACK_SAMPLE_RATE != SAMPLE_RATE:
        data = _resample_linear(data, SAMPLE_RATE, PLAYBACK_SAMPLE_RATE)
    sd.play(data, samplerate=PLAYBACK_SAMPLE_RATE, device=OUTPUT_DEVICE)
    sd.wait()


async def tts_worker():
    loop = asyncio.get_running_loop()
    while True:
        spoken = await speech_queue.get()
        try:
            await loop.run_in_executor(None, speak_blocking, spoken)
        except Exception as e:
            print(f"[TTS error] {e}")
        finally:
            speech_queue.task_done()


# ------------------------------ Mode: Twitch ---------------------------------
def parse_privmsg(line):
    tags = {}
    if line.startswith("@"):
        tag_str, _, line = line[1:].partition(" ")
        for kv in tag_str.split(";"):
            key, _, val = kv.partition("=")
            tags[key] = val
    m = PRIVMSG_RE.match(line)
    if not m:
        return None
    login = m.group(1).lower()
    message = m.group(2).rstrip("\r\n").rstrip(" \u0001")
    display = tags.get("display-name") or m.group(1)
    return login, display, message, tags.get("emotes", "")


_chat_ws = None   # current Twitch connection; closed to rejoin after a channel change


async def chat_listener():
    global _chat_ws
    import websockets
    while True:
        try:
            async with websockets.connect(TWITCH_WS, ssl=_SSL_CONTEXT) as ws:
                _chat_ws = ws
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send(f"JOIN #{CHANNEL}")
                print(f"Connected to #{CHANNEL}. Waiting for messages ...")
                async for raw in ws:
                    for line in raw.split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        if "PRIVMSG" not in line:
                            continue
                        parsed = parse_privmsg(line)
                        if not parsed:
                            continue
                        login, display, message, emotes_tag = parsed
                        if should_skip(login, message):
                            continue
                        message = strip_native_emotes(message, emotes_tag)
                        cleaned = clean_text(message, login)
                        if not cleaned:
                            continue
                        print(f"{display}: {cleaned}")
                        await speech_queue.put(build_spoken(display, cleaned))
        except Exception as e:
            print(f"[Connection lost] {e} - retrying in 5 s ...")
            await asyncio.sleep(5)


# ------------------------------ Config reload --------------------------------
_reload_lock = asyncio.Lock()


def _check_voice(name):
    """Load a voice's state; raises ValueError if Pocket TTS doesn't know it."""
    try:
        return tts_model.get_state_for_audio_prompt(name)
    except Exception as e:
        # Pocket TTS treats an unknown name as an audio file path, so the raw
        # error is about reading audio files - not helpful for a typo.
        print(f"[config] voice {name!r} failed to load: {e}")
        raise ValueError(f"unknown voice {name!r} (built-in voices include: "
                         f"{', '.join(KNOWN_VOICES)})")


def _check_output_device(device):
    if device is None:
        return
    try:
        sd.query_devices(device, "output")
    except Exception as e:
        raise ValueError(str(e))


async def reload_config():
    """Re-read config.json, abbreviations.json and emotes.json and apply them
    to the running bot. Returns a summary dict for the panel / log."""
    global voice_state, PLAYBACK_SAMPLE_RATE
    loop = asyncio.get_running_loop()
    async with _reload_lock:
        values, warnings = _read_config_file()
        word_lists = _read_word_lists()
        old_voice_state = voice_state
        if values["voice"] != VOICE:
            try:
                voice_state = await loop.run_in_executor(None, _check_voice, values["voice"])
            except ValueError as e:
                warnings.append(f"voice: {e} - keeping {VOICE!r}")
                values["voice"] = VOICE
        changed = _apply_config(values)
        old_lists = (ABBREVIATIONS, EMOTES)
        _apply_word_lists(*word_lists)
        if ABBREVIATIONS != old_lists[0]:
            changed.add("abbreviations")
        if EMOTES != old_lists[1]:
            changed.add("emotes")
        if voice_state is not old_voice_state:
            print(f"[config] voice switched to {VOICE}")
        if "output_device" in changed:
            PLAYBACK_SAMPLE_RATE = _pick_playback_rate()
            print(f"[config] output device: {OUTPUT_DEVICE or 'default'}")
        if "channel" in changed and _chat_ws is not None:
            await _chat_ws.close()      # chat_listener reconnects to the new channel
        pending = restart_pending(values)
        for w in warnings:
            print(f"[config] {w}")
        print(f"[config] reloaded ({len(changed)} setting(s) changed)"
              + (f", restart needed for: {', '.join(pending)}" if pending else ""))
        return {"changed": sorted(changed), "warnings": warnings, "restart_pending": pending}


async def save_config(updates, abbreviations=None, emotes=None):
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
            clean[key] = _coerce(field, value, strict=True)
        except ValueError as e:
            errors[key] = str(e)
    if "voice" in clean and clean["voice"] != VOICE:
        try:
            await loop.run_in_executor(None, _check_voice, clean["voice"])
        except ValueError as e:
            errors["voice"] = str(e)
    if "output_device" in clean:
        try:
            _check_output_device(clean["output_device"])
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
    async with _reload_lock:
        if clean:
            write_config(clean)
        for path, data in ((ABBR_FILE, abbreviations), (EMOTE_FILE, emotes)):
            if data is not None:
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
                os.replace(tmp, path)
    return await reload_config()


def _output_device_names():
    try:
        return sorted({d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0})
    except Exception:
        return []


def config_snapshot():
    """Everything the settings page needs: schema, saved values, word lists,
    and whether the saved files differ from what's running."""
    snap = {"schema": CONFIG_SCHEMA, "running": _active,
            "suggest": {"voices": KNOWN_VOICES, "devices": _output_device_names()},
            "config_path": str(CONFIG_FILE)}
    try:
        values, warnings = _read_config_file()
        abbreviations, emotes = _read_word_lists()
    except ConfigError as e:
        return {**snap, "error": str(e), "values": _active,
                "abbreviations": ABBREVIATIONS, "emotes": EMOTES}
    # Saved but not yet running, e.g. config.json was edited by hand.
    unapplied = sorted(k for k, v in values.items()
                       if not SCHEMA_BY_KEY[k].get("restart") and _active.get(k) != v)
    if {str(k).lower(): str(v) for k, v in abbreviations.items()} != ABBREVIATIONS:
        unapplied.append("abbreviations")
    if [str(e) for e in emotes] != EMOTES:
        unapplied.append("emotes")
    return {**snap, "values": values, "warnings": warnings,
            "abbreviations": abbreviations, "emotes": emotes,
            "restart_pending": restart_pending(values), "unapplied": unapplied}


# --------------------------- Control + HTTP server ---------------------------
async def run_http():
    from aiohttp import web

    async def handle_tts(request):
        user = request.query.get("user", "")
        if request.method == "GET":
            text = request.query.get("text", "")
        elif request.content_type == "application/json":
            try:
                data = await request.json()
            except (json.JSONDecodeError, ValueError):
                # Bots like Mix It Up paste chat text into a JSON template
                # with no escaping, so a stray quote/backslash in the message
                # breaks the JSON. Fall back to the raw body as plain text.
                data = {}
                text = await request.text()
            else:
                user = data.get("user", user)
                text = data.get("text", "")
        elif request.content_type in (
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        ):
            data = await request.post()
            user = data.get("user", user)
            text = data.get("text", "")
        else:
            # No structured encoding (e.g. Mix It Up's HTTP Custom Request,
            # which can't escape the message for JSON/form bodies): treat the
            # whole POST body as the literal text to speak, username via
            # ?user= instead.
            text = await request.text()
        user = str(user).strip().lower()
        text = str(text).strip()
        if not text or should_skip(user, text):
            return web.Response(status=204)
        cleaned = clean_text(text, user)
        if not cleaned:
            return web.Response(status=204)
        print(f"[http] {cleaned}")
        await speech_queue.put(cleaned)   # speak the text as-is, no name prefix
        return web.Response(text="ok")

    async def handle_skip(request):
        # Abort the message currently being spoken; the worker advances to next.
        sd.stop()
        print("[skip] current message aborted")
        return web.Response(text="skipped")

    async def handle_clear(request):
        dropped = await _drain_queue()
        print(f"[clear] dropped {dropped} queued, aborted current")
        return web.Response(text=f"cleared {dropped}")

    async def handle_panel(request):
        return web.FileResponse(PANEL_HTML_PATH)

    async def handle_config_get(request):
        return web.json_response(config_snapshot())

    async def handle_config_post(request):
        if request.content_type != "application/json":
            return web.json_response({"error": "expected application/json"}, status=415)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid JSON"}, status=400)
        try:
            result = await save_config(body.get("values") or {},
                                       body.get("abbreviations"), body.get("emotes"))
        except ValueError as e:
            return web.json_response({"errors": e.args[0]}, status=400)
        except (ConfigError, OSError) as e:
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response(result)

    async def handle_reload(request):
        try:
            return web.json_response(await reload_config())
        except ConfigError as e:
            return web.json_response({"error": str(e)}, status=400)

    def _local_api_request(request):
        # The settings API can rewrite config.json (e.g. the Firebot URL), so
        # unlike /tts it must not be callable by other websites: no CORS, the
        # Origin (if any) must be this server, and the Host must be an IP or
        # "localhost" so a DNS-rebinding domain can't pose as same-origin.
        host = request.url.host or ""
        if host != "localhost" and not re.fullmatch(r"[\d.]+|\[?[0-9a-fA-F:]+\]?", host):
            return False
        origin = request.headers.get("Origin")
        return origin is None or origin == f"{request.scheme}://{request.host}"

    @web.middleware
    async def _cors(request, handler):
        if request.path.startswith("/api/"):
            if not _local_api_request(request):
                return web.json_response({"error": "forbidden"}, status=403)
            return await handler(request)
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    app = web.Application(middlewares=[_cors])
    app.router.add_route("*", "/tts", handle_tts)
    app.router.add_route("*", "/skip", handle_skip)
    app.router.add_route("*", "/clear", handle_clear)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    app.router.add_get("/", handle_panel)
    app.router.add_get("/api/config", handle_config_get)
    app.router.add_post("/api/config", handle_config_post)
    app.router.add_post("/api/reload", handle_reload)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    base = f"http://{HTTP_HOST}:{HTTP_PORT}"
    print(f"Control panel: {base}/  (endpoints: /tts /skip /clear)")
    await asyncio.Event().wait()


async def main():
    global _loop
    _loop = asyncio.get_running_loop()
    # The control server (/skip, /clear, /tts) runs in BOTH modes so the skip
    # URL always works; twitch mode additionally reads the chat.
    if MODE == "twitch":
        await asyncio.gather(tts_worker(), run_http(), chat_listener())
    elif MODE == "http":
        await asyncio.gather(tts_worker(), run_http())
    else:
        raise SystemExit(f"Unknown mode: {MODE!r} (allowed: 'twitch', 'http')")


# ------------------------------ System tray -----------------------------------
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


def _open_path(path):
    """Open a file/folder/URL with the OS's default handler."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], env=_subprocess_env())
        else:
            subprocess.Popen(["xdg-open", str(path)], env=_subprocess_env())
    except Exception as e:
        print(f"[tray] couldn't open {path}: {e}")


def _tray_icon_image():
    """A small equalizer-bars glyph - reads as "audio" even shrunk to 16px,
    unlike a letter which blurs into mush at tray-icon sizes."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=16, fill=(88, 101, 242, 255))
    for x, top, bottom in ((18, 24, 40), (30, 12, 52), (42, 24, 40)):
        d.rounded_rectangle((x, top, x + 6, bottom), radius=3, fill="white")
    return img


def panel_url():
    host = "127.0.0.1" if HTTP_HOST in ("0.0.0.0", "::", "") else HTTP_HOST
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{HTTP_PORT}/"


async def _reload_from_tray():
    try:
        await reload_config()
    except ConfigError as e:
        print(f"[config] reload failed: {e}")


def run_tray():
    """Blocking: runs the tray icon on the calling (main) thread."""
    import pystray

    def on_open_panel(icon, item):
        _open_path(panel_url())

    def on_reload(icon, item):
        if _loop:
            asyncio.run_coroutine_threadsafe(_reload_from_tray(), _loop)

    def on_open_config(icon, item):
        _open_path(APP_DIR)

    def on_open_log_folder(icon, item):
        _open_path(APP_DIR)

    def on_skip(icon, item):
        if _loop:
            _loop.call_soon_threadsafe(sd.stop)

    def on_clear(icon, item):
        if _loop:
            asyncio.run_coroutine_threadsafe(_drain_queue(), _loop)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    items = [
        pystray.MenuItem(f"Tellbird ({MODE} mode)", None, enabled=False),
        pystray.MenuItem("Open Tellbird panel", on_open_panel),
        pystray.MenuItem("Reload config", on_reload),
        pystray.MenuItem("Open config folder", on_open_config),
        pystray.MenuItem("Open log folder", on_open_log_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Skip current", on_skip),
        pystray.MenuItem("Clear queue", on_clear),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    ]
    icon = pystray.Icon("tellbird", _tray_icon_image(), "Tellbird", pystray.Menu(*items))
    icon.run()
# -----------------------------------------------------------------------------


if __name__ == "__main__":
    tray_available = False
    if SHOW_TRAY_ICON:
        if sys.platform not in ("win32", "darwin"):
            # Work around a pystray bug (its notify_dbus helper calls
            # gi.require_version('DBus', '1.0') for a namespace that doesn't
            # exist on any distro and is never actually used afterwards -
            # only GLib/Gio are - which otherwise kills the whole
            # AppIndicator backend with "Namespace DBus not available").
            try:
                import gi
                _orig_require_version = gi.require_version

                def _require_version(namespace, version):
                    if namespace == "DBus":
                        return
                    return _orig_require_version(namespace, version)

                gi.require_version = _require_version
            except Exception:
                pass
        try:
            import pystray  # noqa: F401
            tray_available = True
        except Exception:
            print("[tray] pystray not available, running as a plain console app:")
            import traceback
            traceback.print_exc()

    if tray_available:
        worker = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
        worker.start()
        try:
            run_tray()
        except KeyboardInterrupt:
            print("\nStopped.")
        except Exception as e:
            print(f"[tray] unavailable ({e}), continuing without it:")
            import traceback
            traceback.print_exc()
            worker.join()
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nStopped.")
