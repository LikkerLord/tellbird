import sys

import pytest

from tellbird import paths as paths_mod
from tellbird.twitch import parse_privmsg


def test_parse_privmsg_with_tags():
    line = ("@badge-info=;display-name=Alice_W;emotes=25:0-4 "
            ":alice_w!alice_w@alice_w.tmi.twitch.tv PRIVMSG #chan :Kappa hello")
    assert parse_privmsg(line) == ("alice_w", "Alice_W", "Kappa hello", "25:0-4")


def test_parse_privmsg_action_and_no_tags():
    line = ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :\u0001ACTION waves\u0001"
    login, display, message, emotes = parse_privmsg(line)
    assert (login, display, emotes) == ("bob", "bob", "")
    assert message == "\u0001ACTION waves"


def test_parse_privmsg_ignores_other_lines():
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!") is None


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="uses XDG_CONFIG_HOME")
def test_legacy_config_dir_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / "twitch-tts-bot"
    old.mkdir()
    (old / "config.json").write_text("{}")
    (old / "tts_bot.log").write_text("log")
    new = paths_mod.user_config_dir()
    assert paths_mod.migrate_legacy_config_dir(new) == old
    assert sorted(p.name for p in new.iterdir()) == ["config.json", "tellbird.log"]
    assert not old.exists()
    assert paths_mod.migrate_legacy_config_dir(new) is None    # only once


def test_migration_never_overwrites_new_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    (tmp_path / "twitch-tts-bot").mkdir()
    (tmp_path / "tellbird").mkdir()
    assert paths_mod.migrate_legacy_config_dir(tmp_path / "tellbird") is None
    assert (tmp_path / "twitch-tts-bot").exists()
