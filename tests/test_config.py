import json

import pytest

from conftest import REPO
from tellbird.config import (CONFIG_SCHEMA, SCHEMA_BY_KEY, ConfigError, RunningConfig, Settings,
                             coerce, read_config_file, resolve_config, strip_comments,
                             update_config_text)


def test_example_config_is_valid():
    values, warnings = read_config_file(REPO / "config.example.json")
    assert warnings == []
    assert values.keys() == SCHEMA_BY_KEY.keys()


def test_example_config_mentions_every_setting():
    # A new schema key should also be documented in config.example.json.
    cfg = json.loads(strip_comments((REPO / "config.example.json").read_text()))
    from tellbird.config import get_path
    missing = []
    for f in CONFIG_SCHEMA:
        try:
            get_path(cfg, f["key"])
        except KeyError:
            missing.append(f["key"])
    assert missing == []


@pytest.mark.parametrize("key, raw, strict, expected", [
    ("read_username", "yes", False, True),
    ("read_username", 0, True, False),
    ("max_chars", "50", True, 50),
    ("max_chars", -5, False, 1),               # clamped when read from the file
    ("spam_ratio", 2, False, 1.0),
    ("output_device", "  ", True, None),
    ("ignored_users", ["a", " ", "b"], True, ["a", "b"]),
])
def test_coerce(key, raw, strict, expected):
    field = SCHEMA_BY_KEY.get(key) or SCHEMA_BY_KEY[f"filters.{key}"]
    assert coerce(field, raw, strict) == expected


@pytest.mark.parametrize("key, raw", [
    ("max_chars", -5),                         # rejected from the panel
    ("max_chars", True),
    ("mode", "irc"),
    ("username_format", "{user} {oops}"),
    ("ignored_users", "nightbot"),
])
def test_coerce_rejects(key, raw):
    with pytest.raises(ValueError):
        coerce(SCHEMA_BY_KEY[key], raw, strict=True)


def test_resolve_config_falls_back_to_defaults_with_warning():
    values, warnings = resolve_config({"mode": "irc", "max_chars": 99})
    assert values["mode"] == "twitch"
    assert values["max_chars"] == 99
    assert values["voice"] == "alba"
    assert len(warnings) == 1 and warnings[0].startswith("mode:")


def test_read_config_file_errors(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[]")
    with pytest.raises(ConfigError):
        read_config_file(p)
    p.write_text("{ nope")
    with pytest.raises(ConfigError):
        read_config_file(p)


def test_running_config_keeps_restart_keys():
    rc = RunningConfig()
    values, _ = resolve_config({})
    settings, _ = rc.apply(values)
    new = {**values, "http_port": 6000, "voice": "anna"}
    settings, changed = rc.apply(new)
    assert settings["http_port"] == 5050           # restart-only: unchanged
    assert settings["voice"] == "anna"
    assert changed == {"voice"}
    assert rc.restart_pending(new) == ["http_port"]


def test_settings_views():
    s = Settings.defaults(channel="#MyChannel", firebot_actions__presets__word_spam="abc")
    assert s.channel == "mychannel"
    assert s.firebot_presets == {"word_spam": "abc"}
    with pytest.raises(KeyError):
        Settings({"mode": "twitch"})


# --------------------------- comment-preserving writer ------------------------
RAW = """{
  // the mode
  "mode": "twitch",
  "voice": "alba",
  "filters": {
    // a filter
    "max_char_run": 3
  },
  "empty": {}
}
"""


def test_update_replaces_values_in_place_and_keeps_comments():
    out = update_config_text(RAW, {"voice": "anna", "filters.max_char_run": 5})
    assert out == RAW.replace('"alba"', '"anna"').replace('"max_char_run": 3', '"max_char_run": 5')


def test_update_inserts_missing_keys():
    out = update_config_text(RAW, {"channel": "me", "filters.spam_ratio": 0.5,
                                   "firebot_actions.presets.word_spam": "id",
                                   "empty.x": 1})
    cfg = json.loads(strip_comments(out))
    assert cfg["channel"] == "me"
    assert cfg["filters"] == {"max_char_run": 3, "spam_ratio": 0.5}
    assert cfg["firebot_actions"] == {"presets": {"word_spam": "id"}}
    assert cfg["empty"] == {"x": 1}
    assert "// the mode" in out and "// a filter" in out


def test_update_handles_strings_with_tricky_characters():
    raw = '{\n  "username_format": "{user} says: \\"{text}\\" // not a comment"\n}\n'
    out = update_config_text(raw, {"username_format": "{user}: {text}", "voice": "ünïcode"})
    cfg = json.loads(strip_comments(out))
    assert cfg == {"username_format": "{user}: {text}", "voice": "ünïcode"}


def test_every_setting_round_trips_through_the_example_config():
    raw = (REPO / "config.example.json").read_text()
    values, _ = resolve_config({})
    out = update_config_text(raw, values)
    reread, warnings = resolve_config(json.loads(strip_comments(out)))
    assert warnings == [] and reread == values


def test_depends_points_to_an_earlier_switch():
    # The panel nests a dependent field under its switch, so the switch must
    # be a bool field in the same section that comes before it.
    for i, f in enumerate(CONFIG_SCHEMA):
        if "depends" in f:
            parent = SCHEMA_BY_KEY[f["depends"]]
            assert parent["type"] == "bool", f["key"]
            assert parent["section"] == f["section"], f["key"]
            assert CONFIG_SCHEMA.index(parent) < i, f["key"]
