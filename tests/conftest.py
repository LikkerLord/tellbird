import asyncio
import shutil
from pathlib import Path

import pytest

from tellbird.paths import Paths

REPO = Path(__file__).resolve().parent.parent


class FakeSpeaker:
    """Stands in for audio.Speaker: no model, no sound card."""

    def __init__(self):
        self.voice = None
        self.output_device = None
        self.loaded = []
        self.spoken = []

    def load_voice(self, name):
        # Like the real Speaker: unknown names fail, known ones are cached.
        if name == "nosuchvoice":
            raise ValueError(f"unknown voice {name!r}")
        if name not in self.loaded:
            self.loaded.append(name)
        return object()

    def set_voice(self, name):
        self.load_voice(name)
        self.voice = name

    def set_output_device(self, device):
        self.output_device = device

    def speak_blocking(self, text, tail_padding_seconds, cancel):
        self.spoken.append(text)


class FakeAudio:
    @staticmethod
    def check_output_device(device):
        if device == "missing":
            raise ValueError("No output device matching 'missing'")

    @staticmethod
    def output_device_names():
        return ["Speakers"]


@pytest.fixture
def paths(tmp_path):
    """A config folder seeded from the real example files."""
    shutil.copy(REPO / "config.example.json", tmp_path / "config.json")
    shutil.copy(REPO / "abbreviations.json", tmp_path / "abbreviations.json")
    shutil.copy(REPO / "emotes.json", tmp_path / "emotes.json")
    return Paths(tmp_path)


@pytest.fixture
def make_bot(paths):
    """make_bot() -> a Bot with fake audio. Call it inside a running event loop."""
    from tellbird.app import Bot

    def make():
        return Bot(paths, FakeSpeaker(), FakeAudio)
    return make


def run(coro):
    return asyncio.run(coro)
