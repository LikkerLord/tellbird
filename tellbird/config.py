"""Settings: the schema, loading/validation, and the comment-preserving
config.json writer."""

import json
import os
import re
from functools import cached_property


class ConfigError(Exception):
    pass


def strip_comments(raw):
    # Drop whole-line // comments so config.json can be annotated.
    return "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("//"))


def load_json(path, default, allow_comments=False):
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return default
    if allow_comments:
        raw = strip_comments(raw)
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


def get_path(cfg, key):
    for part in key.split("."):
        if not isinstance(cfg, dict) or part not in cfg:
            raise KeyError(key)
        cfg = cfg[part]
    return cfg


def coerce(field, value, strict):
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


def resolve_config(cfg):
    """config.json contents -> {key: value} for every schema key, using the
    default for missing/invalid values. Returns (values, warnings)."""
    values, warnings = {}, []
    for f in CONFIG_SCHEMA:
        try:
            raw = get_path(cfg, f["key"])
        except KeyError:
            values[f["key"]] = f["default"]
            continue
        try:
            values[f["key"]] = coerce(f, raw, strict=False)
        except ValueError as e:
            warnings.append(f"{f['key']}: {e} - using default {f['default']!r}")
            values[f["key"]] = f["default"]
    return values, warnings


def read_config_file(path):
    cfg = load_json(path, {}, allow_comments=True)
    if not isinstance(cfg, dict):
        raise ConfigError("config.json must contain a JSON object")
    return resolve_config(cfg)


def read_word_lists(abbreviations_path, emotes_path):
    abbreviations = load_json(abbreviations_path, {}) or {}
    emotes = load_json(emotes_path, []) or []
    if not isinstance(abbreviations, dict):
        raise ConfigError("abbreviations.json must contain a JSON object")
    if not isinstance(emotes, list):
        raise ConfigError("emotes.json must contain a JSON list")
    return abbreviations, emotes


class Settings:
    """One complete, validated configuration: settings["filters.max_char_run"]
    for any schema key, plus a few normalized views. Never changes once made;
    a reload builds a new one."""

    def __init__(self, values):
        missing = SCHEMA_BY_KEY.keys() - values.keys()
        if missing:
            raise KeyError(f"settings missing: {', '.join(sorted(missing))}")
        self._values = dict(values)

    @classmethod
    def defaults(cls, **overrides):
        """Schema defaults; overrides use "__" for ".", e.g. filters__max_char_run=2."""
        values, _ = resolve_config({})
        values.update({k.replace("__", "."): v for k, v in overrides.items()})
        return cls(values)

    def __getitem__(self, key):
        return self._values[key]

    def as_dict(self):
        return dict(self._values)

    @cached_property
    def channel(self):
        return self["channel"].lower().lstrip("#")

    @cached_property
    def ignored_users(self):
        return {u.lower() for u in self["ignored_users"]}

    @cached_property
    def ignore_prefixes(self):
        return tuple(self["ignore_prefixes"])

    @cached_property
    def firebot_api_base(self):
        return self["firebot_actions.api_base"].rstrip("/")

    @cached_property
    def firebot_presets(self):
        return {name: self[f"firebot_actions.presets.{name}"] for name in FIREBOT_FILTERS
                if self[f"firebot_actions.presets.{name}"]}


class RunningConfig:
    """Tracks which values are in effect. Restart-only keys ("restart" in the
    schema) keep the value the bot started with; everything else is applied
    live by a reload."""

    def __init__(self):
        self.startup = {}
        self.active = {}

    def apply(self, values):
        """Make `values` (from resolve_config) the running config.
        Returns (Settings, set of changed keys)."""
        if not self.startup:
            self.startup = {k: v for k, v in values.items() if SCHEMA_BY_KEY[k].get("restart")}
        merged = {**values, **self.startup}
        changed = {k for k in merged if self.active.get(k, object()) != merged[k]}
        self.active = merged
        return Settings(merged), changed

    def restart_pending(self, values):
        """Restart-only keys whose saved value differs from what's running."""
        return [k for k, val in self.startup.items() if values.get(k) != val]


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


def update_config_text(raw, updates):
    """Return config.json text `raw` with {dotted key: value} written into it,
    replacing just those values in place so the user's comments and layout
    survive. Keys missing from the text are appended to their (possibly new)
    parent object."""
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
    check = json.loads(strip_comments(raw))
    for key, val in updates.items():
        if get_path(check, key) != val:
            raise ConfigError(f"couldn't write {key} into config.json")
    return raw


def write_text_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_config(path, updates):
    raw = path.read_text(encoding="utf-8") if path.exists() else "{\n}\n"
    write_text_atomic(path, update_config_text(raw, updates))


def write_json(path, data):
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

