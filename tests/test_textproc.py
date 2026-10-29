import pytest

from tellbird.config import Settings
from tellbird.textproc import (ChatFilter, WordLists, collapse_char_runs,
                               collapse_phrase_repeats, collapse_word_repeats, is_word_spam,
                               strip_native_emotes)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_filter(word_lists=None, **overrides):
    fired = []
    clock = Clock()
    f = ChatFilter(Settings.defaults(**overrides), word_lists,
                   on_filter=lambda *a: fired.append(a), clock=clock)
    return f, fired, clock


@pytest.mark.parametrize("text, expected", [
    ("aaaaaaa", "aaa"),
    ("!!!!!!", "!!!"),
    ("aaa", "aaa"),
    ("hello", "hello"),
])
def test_collapse_char_runs(text, expected):
    assert collapse_char_runs(text, 3) == expected


def test_collapse_word_repeats_is_case_insensitive_and_keeps_case():
    assert collapse_word_repeats("lol LOL lol Lol lol hi", 3) == "lol LOL lol hi"


@pytest.mark.parametrize("text, expected", [
    ("go team go team go team go team", "go team go team go team"),
    ("go team go team", "go team go team"),
    # The real unit is 2 words, not 4: must collapse to 3 x "a b".
    ("a b a b a b a b a b a b a b a b", "a b a b a b"),
    ("no repeats here at all", "no repeats here at all"),
])
def test_collapse_phrase_repeats(text, expected):
    assert collapse_phrase_repeats(text, 4, 3) == expected


def test_is_word_spam():
    assert is_word_spam("buy buy buy buy buy now", 6, 0.6)
    assert not is_word_spam("buy buy buy", 6, 0.6)           # too short to judge
    assert not is_word_spam("one two three four five six", 6, 0.6)


def test_strip_native_emotes():
    # "Kappa" at 0-4 and 12-16
    assert strip_native_emotes("Kappa hello Kappa", "25:0-4,12-16") == "hello"
    assert strip_native_emotes("hello", "") == "hello"
    assert strip_native_emotes("hello", "garbage") == "hello"


def test_word_lists():
    wl = WordLists({"BRB": "be right back", "gg": "good game"}, ["catJAM"])
    assert wl.expand_abbreviations("brb, gg") == "be right back, good game"
    assert wl.expand_abbreviations("loooool lmaaooo") == "lol lmao"
    assert wl.strip_emotes("nice catJAM song") == "nice song"
    assert wl == WordLists({"brb": "be right back", "gg": "good game"}, ["catJAM"])


def test_skip_ignored_users_and_prefixes():
    f, _, _ = make_filter()
    assert f.should_skip("nightbot", "hello")
    assert f.should_skip("someone", "!command")
    assert not f.should_skip("someone", "hello")


def test_cooldown():
    f, _, clock = make_filter(cooldown_seconds=10)
    assert not f.should_skip("alice", "one")
    clock.now = 5
    assert f.should_skip("alice", "two")
    assert not f.should_skip("bob", "two")
    clock.now = 11
    assert not f.should_skip("alice", "three")


def test_repeated_message_fires_filter():
    f, fired, _ = make_filter(filters__block_repeated_messages=True)
    assert not f.should_skip("alice", "Hello")
    assert f.should_skip("alice", "hello ")
    assert fired == [("repeated_message", "alice", "hello ")]


def test_copypasta_flood_window():
    f, fired, clock = make_filter(filters__block_copypasta_flood=True,
                                  filters__copypasta_flood_min_users=3,
                                  filters__copypasta_flood_window_seconds=20)
    assert f.apply_filters("pasta", "a") == "pasta"
    assert f.apply_filters("pasta", "b") == "pasta"
    assert f.apply_filters("pasta", "c") is None
    assert fired[-1][0] == "copypasta_flood"
    clock.now = 100                                   # window expired
    assert f.apply_filters("pasta", "d") == "pasta"


def test_word_spam_and_long_word_skip():
    f, fired, _ = make_filter(filters__skip_word_spam=True, filters__max_word_length=10)
    assert f.apply_filters("spam spam spam spam spam ok", "u") is None
    assert f.apply_filters("a" * 11, "u") is None
    assert [x[0] for x in fired] == ["word_spam", "long_word"]


def test_collapse_filters_fire():
    f, fired, _ = make_filter()
    assert f.apply_filters("nooooooo", "u") == "nooo"
    assert fired == [("char_run", "u", "nooooooo")]


def test_clean_text():
    f, _, _ = make_filter(WordLists({"gg": "good game"}), max_chars=40)
    assert f.clean_text("gg check https://example.com/x") == "good game check link."
    assert f.clean_text("I love pocketTTS!") == "I love pocket TTS!"
    assert f.clean_text("   ") is None
    long = f.clean_text(" ".join(f"word{i}" for i in range(20)))
    assert long.endswith(" ...") and len(long) <= 44


def test_build_spoken():
    f, _, _ = make_filter()
    assert f.build_spoken("Alice", "hi.") == "Alice says: hi."
    assert f.build_spoken("", "hi.") == "hi."
    f.settings = Settings.defaults(read_username=False)
    assert f.build_spoken("Alice", "hi.") == "hi."
