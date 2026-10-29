"""Turning chat messages into speakable text: emote/URL stripping,
abbreviations, the optional anti-abuse filters, and who gets skipped."""

import re
import time
from collections import Counter, deque

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


# --------------------------- Abbreviations / emotes --------------------------
class WordLists:
    """abbreviations.json (shorthand -> spoken words) and emotes.json
    (third-party emote names to drop), normalized and compiled."""

    def __init__(self, abbreviations=None, emotes=None):
        self.abbreviations = {str(k).lower(): str(v) for k, v in (abbreviations or {}).items()}
        self.emotes = [str(e) for e in (emotes or [])]
        self._abbr_re = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in
                              sorted(self.abbreviations, key=len, reverse=True)) + r")\b",
            re.IGNORECASE,
        ) if self.abbreviations else None
        self._emote_re = re.compile(
            r"\b(" + "|".join(re.escape(e) for e in
                              sorted(self.emotes, key=len, reverse=True)) + r")\b"
        ) if self.emotes else None

    def __eq__(self, other):
        return (isinstance(other, WordLists) and self.abbreviations == other.abbreviations
                and self.emotes == other.emotes)

    def expand_abbreviations(self, text):
        text = re.sub(r"\blo+l\b", "lol", text, flags=re.IGNORECASE)
        text = re.sub(r"\blma+o+\b", "lmao", text, flags=re.IGNORECASE)
        if self._abbr_re:
            text = self._abbr_re.sub(lambda m: self.abbreviations[m.group(0).lower()], text)
        return text

    def strip_emotes(self, text):
        if self._emote_re:
            text = self._emote_re.sub("", text)
            text = re.sub(r"\s{2,}", " ", text).strip()
        return text


def strip_native_emotes(message, emotes_tag):
    """Remove Twitch's own emotes, located by the IRC "emotes" tag
    (id:start-end,start-end/id:...)."""
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


# ------------------------------ Abuse filters --------------------------------
def collapse_char_runs(text, max_run):
    # "aaaaaa" -> max_run a's, "!!!!!" -> capped too
    return re.sub(r"(.)\1{%d,}" % max_run, lambda m: m.group(1) * max_run, text)


def collapse_word_repeats(text, max_repeats):
    # "lol lol lol lol" -> keep at most max_repeats in a row
    out, prev, run = [], None, 0
    for w in text.split():
        key = w.lower()
        run = run + 1 if key == prev else 1
        prev = key
        if run <= max_repeats:
            out.append(w)
    return " ".join(out)


def collapse_phrase_repeats(text, max_words, max_repeats):
    # "go team go team go team go team" -> keep at most max_repeats repeats
    # of a 2..max_words-word phrase (single-word repeats are handled
    # separately by collapse_word_repeats).
    words = text.split()
    n = len(words)
    out, i = [], 0
    while i < n:
        matched = False
        longest = min(max_words, (n - i) // 2)
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
                out.extend(words[i:i + length] * min(repeats, max_repeats))
                i = j
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def is_word_spam(text, min_words, ratio):
    # True if one word makes up >= ratio of a long-enough message
    words = [w.lower() for w in text.split()]
    if len(words) < min_words:
        return False
    return max(Counter(words).values()) / len(words) >= ratio


class ChatFilter:
    """Decides what gets spoken and cleans it up. Holds the per-user state
    the filters need (cooldowns, last message, copypasta window).

    on_filter(filter_name, user, text) is called whenever an anti-abuse
    filter fires (used for Firebot actions). `settings` and `word_lists`
    may be swapped out at any time by a config reload."""

    def __init__(self, settings, word_lists=None, on_filter=None, clock=time.monotonic):
        self.settings = settings
        self.word_lists = word_lists or WordLists()
        self.on_filter = on_filter
        self._clock = clock
        self._last_spoken: dict[str, float] = {}
        self._last_message: dict[str, str] = {}
        self._copypasta: deque = deque()   # (timestamp, normalized_text, user)

    def _fire(self, filter_name, user, text):
        if self.on_filter:
            self.on_filter(filter_name, user, text)

    def _is_copypasta_flood(self, text, user):
        # True once copypasta_flood_min_users distinct chatters have sent the
        # exact same message within the trailing window (raid/copypasta spam)
        # - keeps the bot from reading the same line over and over.
        s = self.settings
        now = self._clock()
        window = s["filters.copypasta_flood_window_seconds"]
        while self._copypasta and now - self._copypasta[0][0] > window:
            self._copypasta.popleft()
        norm = text.strip().lower()
        if not norm:
            return False
        users = {u for _ts, t, u in self._copypasta if t == norm}
        users.add(user)
        self._copypasta.append((now, norm, user))
        return len(users) >= s["filters.copypasta_flood_min_users"]

    def apply_filters(self, text, user=""):
        """Normalize spammy text; return None to skip."""
        s = self.settings
        if s["filters.skip_word_spam"] and is_word_spam(
                text, s["filters.spam_min_words"], s["filters.spam_ratio"]):
            self._fire("word_spam", user, text)
            return None
        max_len = s["filters.max_word_length"]            # 0 = off
        if max_len > 0 and any(len(w) > max_len for w in text.split()):
            self._fire("long_word", user, text)
            return None
        if s["filters.block_copypasta_flood"] and self._is_copypasta_flood(text, user):
            self._fire("copypasta_flood", user, text)
            return None
        for enabled, name, collapse in (
            ("filters.collapse_char_runs", "char_run",
             lambda t: collapse_char_runs(t, s["filters.max_char_run"])),
            ("filters.collapse_phrase_repeats", "phrase_repeat",
             lambda t: collapse_phrase_repeats(t, s["filters.max_phrase_words"],
                                               s["filters.max_phrase_repeats"])),
            ("filters.collapse_word_repeats", "word_repeat",
             lambda t: collapse_word_repeats(t, s["filters.max_word_repeats"])),
        ):
            if s[enabled]:
                collapsed = collapse(text)
                if collapsed != text:
                    self._fire(name, user, text)
                    text = collapsed
        return text.strip() or None

    def should_skip(self, user, text):
        s = self.settings
        if user in s.ignored_users:
            return True
        if text.startswith(s.ignore_prefixes):
            return True
        if s["cooldown_seconds"] > 0 and user:
            now = self._clock()
            if now - self._last_spoken.get(user, float("-inf")) < s["cooldown_seconds"]:
                return True
            self._last_spoken[user] = now
        if s["filters.block_repeated_messages"] and user:
            norm = text.strip().lower()
            if norm and self._last_message.get(user) == norm:
                self._fire("repeated_message", user, text)
                return True
            self._last_message[user] = norm
        return False

    def clean_text(self, text, user=""):
        """Make a message speakable; None means don't speak it."""
        s = self.settings
        # Split acronym-suffixed run-together words: "pocketTTS" -> "pocket TTS".
        # Requires 2+ uppercase letters after the boundary so mixed-case chat
        # noise like "loL"/"LoL" isn't shredded into fragments (which would
        # otherwise dodge the lol/repeat-word filters below).
        text = re.sub(r"(?<=[a-z])(?=[A-Z]{2})", " ", text)
        text = self.word_lists.strip_emotes(text)
        text = URL_RE.sub("link", text)
        text = text.strip()
        if len(text) < s["min_chars"]:
            return None
        text = self.apply_filters(text, user)
        if text is None:
            return None
        text = self.word_lists.expand_abbreviations(text)
        if len(text) > s["max_chars"]:
            text = text[:s["max_chars"]].rstrip() + " ..."
        if not text.endswith((".", "!", "?")):
            text += "."
        return text

    def build_spoken(self, user, cleaned):
        s = self.settings
        if s["read_username"] and user:
            return s["username_format"].format(user=user, text=cleaned)
        return cleaned
