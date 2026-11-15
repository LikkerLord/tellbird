"""Speech synthesis (Kyutai Pocket TTS) and playback (sounddevice).

This is the only module that pulls in torch and PortAudio; everything else
(and the tests) can be imported without them."""

import contextlib
import ctypes
import inspect
import logging
import os
import sys
import threading
import time
from collections import Counter

log = logging.getLogger("tellbird")

# Where distros keep libjack: PipeWire's own first (it shows PipeWire's devices
# by their real names), then the regular one.
SYSTEM_LIBJACK = ("/usr/lib64/pipewire-0.3/jack/libjack.so.0",
                  "/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack/libjack.so.0",
                  "/usr/lib/aarch64-linux-gnu/pipewire-0.3/jack/libjack.so.0",
                  "/usr/lib64/libjack.so.0",
                  "/usr/lib/x86_64-linux-gnu/libjack.so.0",
                  "/usr/lib/aarch64-linux-gnu/libjack.so.0",
                  "/usr/lib/libjack.so.0")


def _prefer_system_libjack():
    """The Linux build bundles the build machine's libjack (PortAudio won't
    load without one), but that one can't talk to PipeWire, so PortAudio then
    lists only raw sound cards. Loading the system's first makes PortAudio
    use it instead; the bundled one stays as the fallback."""
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return
    for path in SYSTEM_LIBJACK:
        if os.path.exists(path):
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                return
            except OSError as e:
                log.info(f"[audio] couldn't load {path}: {e}")


_prefer_system_libjack()

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402


def check_output_device(device):
    """Raise ValueError if `device` (name substring, or None for the system
    default) isn't a usable output device."""
    if device is None:
        return
    try:
        sd.query_devices(device, "output")
    except Exception as e:
        raise ValueError(str(e))


# Linux host APIs that go through the sound server (PipeWire/PulseAudio), where
# devices have the names the system shows everywhere else.
SOUND_SERVER_APIS = ("JACK Audio Connection Kit", "PulseAudio")


WINDOWS_API = "Windows DirectSound"


def _is_real_device(device, api, has_sound_server):
    """False for the entries PortAudio lists on Linux that aren't devices a
    user would recognise or should pick."""
    if api == "ALSA":
        # With a sound server, every raw ALSA device is a duplicate that
        # bypasses it. Without one, keep the hardware ("... (hw:1,3)") and
        # drop ALSA's plugins ("dmix", "surround51", "default", ...).
        return not has_sound_server and "(hw:" in device["name"]
    # PipeWire's Bluetooth loopback node: listed as an output, plays nowhere.
    return not device["name"].startswith("Bluetooth internal capture stream")


def output_device_names():
    """The output devices to offer, each by a name that selects exactly that
    device: just its name, or "name, host API" when several host APIs offer
    a device of that name. A bare name would match all of them, which
    PortAudio rejects."""
    try:
        devices = [(i, d) for i, d in enumerate(sd.query_devices())
                   if d["max_output_channels"] > 0]
        hostapis = sd.query_hostapis()
    except Exception:
        return []
    apis = [h["name"] for h in hostapis]
    qualify_all = False
    if any(apis[d["hostapi"]] == WINDOWS_API for _, d in devices):
        # Windows offers every device through MME, DirectSound, WASAPI and
        # WDM-KS. List each once, through DirectSound: full names (MME cuts
        # them at 31 characters), and it converts rates and mono itself.
        # WASAPI has the same full names, so the API stays in every value.
        primary = next(h["default_output_device"] for h in hostapis if h["name"] == WINDOWS_API)
        devices = [(i, d) for i, d in devices
                   # The default is its "Primary Sound Driver" (in the system's
                   # language): the same as the system default.
                   if apis[d["hostapi"]] == WINDOWS_API and i != primary]
        qualify_all = True
    else:
        has_sound_server = "ALSA" in apis and any(
            apis[d["hostapi"]] in SOUND_SERVER_APIS for _, d in devices)
        devices = [(i, d) for i, d in devices
                   if _is_real_device(d, apis[d["hostapi"]], has_sound_server)]
    counts = Counter(d["name"] for _, d in devices)
    return sorted({d["name"] if counts[d["name"]] == 1 and not qualify_all
                   else f"{d['name']}, {apis[d['hostapi']]}" for _, d in devices},
                  key=str.lower)


def voice_names():
    """Every built-in voice of the installed Pocket TTS. The catalog is a
    private name there, so a future version may move it."""
    try:
        from pocket_tts.utils.utils import _ORIGINS_OF_PREDEFINED_VOICES as catalog
    except ImportError:
        from .config import KNOWN_VOICES
        return KNOWN_VOICES
    return sorted(catalog)


def resample_linear(data, orig_rate, target_rate):
    if orig_rate == target_rate or len(data) == 0:
        return data
    duration = len(data) / orig_rate
    n_target = max(1, int(round(duration * target_rate)))
    x_orig = np.linspace(0, duration, num=len(data), endpoint=False)
    x_target = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_target, x_orig, data).astype("float32")


def play_blocking(data, rate, device, cancel):
    """Play mono float32 `data` and return when it's done, or soon after
    `cancel` (a threading.Event) is set from any thread.

    Deliberately not sd.play()/sd.stop()/sd.wait(): those share global state,
    and a stop() from another thread that races a stream starting up can lose
    the "finished" signal, leaving sd.wait() blocked forever (the queue then
    hangs on that message). Here only this thread touches the stream; skipping
    just sets `cancel`, which the audio callback and the wait loop both check,
    and the wait is capped so nothing can block indefinitely."""
    if cancel.is_set() or len(data) == 0:
        return
    pos = 0
    done = threading.Event()

    def callback(outdata, frames, time_info, status):
        nonlocal pos
        if cancel.is_set():
            outdata.fill(0)
            raise sd.CallbackAbort
        chunk = data[pos:pos + frames]
        outdata[:len(chunk), 0] = chunk
        outdata[len(chunk):] = 0
        pos += len(chunk)
        if pos >= len(data):
            raise sd.CallbackStop

    deadline = time.monotonic() + len(data) / rate + 5
    with sd.OutputStream(samplerate=rate, device=device, channels=1, dtype="float32",
                         callback=callback, finished_callback=done.set) as stream:
        while not done.wait(0.1):
            if cancel.is_set() or time.monotonic() > deadline:
                stream.abort()
                break


@contextlib.contextmanager
def report_downloads(on_progress):
    """Calls on_progress(done_bytes, total_bytes) for model files downloaded
    inside the `with`. Pocket TTS offers no hook for this, so its Hugging Face
    download call gets our own progress bar. Best effort: if either library
    changes shape, the download still works, just without progress."""
    try:
        from huggingface_hub.utils import tqdm as hf_tqdm
        from pocket_tts.utils import utils as pocket_utils
        original = pocket_utils.hf_hub_download
        supported = "tqdm_class" in inspect.signature(original).parameters
    except Exception as e:
        log.info(f"[audio] can't report download progress: {e}")
        supported = False
    if not supported:
        yield
        return

    files = []

    def report():
        on_progress(sum(bar.n for bar in files), sum(bar.total or 0 for bar in files))

    class ProgressBar(hf_tqdm):
        def __init__(self, *args, **kwargs):
            # Xet downloads get a second bar for network transfer on top of
            # the one for bytes written; only count the latter.
            self._counted = not (kwargs.get("name") or "").endswith(".transfer")
            super().__init__(*args, **{**kwargs, "disable": True})
            if self._counted:
                files.append(self)
                report()

        def update(self, n=1):
            self.n += n
            if self._counted:
                report()

    def download(*args, **kwargs):
        return original(*args, **{**kwargs, "tqdm_class": ProgressBar})

    pocket_utils.hf_hub_download = download
    try:
        yield
    finally:
        pocket_utils.hf_hub_download = original


class Speaker:
    """The loaded TTS model plus the current voice and output device."""

    def __init__(self, model):
        from pocket_tts import TTSModel
        log.info(f"Loading Pocket TTS model {model} ...")
        self._model = TTSModel.load_model(language=model)
        self.sample_rate = self._model.sample_rate
        self._voice_states = {}       # name -> loaded voice, so switching back is instant
        self.voice = None
        self._voice_state = None
        self.output_device = None
        self._playback_rate = self.sample_rate

    def load_voice(self, name):
        """Load (or fetch the cached) voice; ValueError if Pocket TTS doesn't
        know it. Doesn't switch to it."""
        if name not in self._voice_states:
            try:
                self._voice_states[name] = self._model.get_state_for_audio_prompt(name)
            except Exception as e:
                # Pocket TTS treats an unknown name as an audio file path, so
                # the raw error is about reading audio files - not helpful
                # for a typo.
                log.warning(f"[config] voice {name!r} failed to load: {e}")
                raise ValueError(f"unknown voice {name!r} (built-in voices include: "
                                 f"{', '.join(voice_names())})")
        return self._voice_states[name]

    def set_voice(self, name):
        if name != self.voice:
            log.info(f"Loading voice: {name}")
            self._voice_state = self.load_voice(name)
            self.voice = name

    def set_output_device(self, device):
        self.output_device = device
        self._playback_rate = self._pick_playback_rate()

    def _pick_playback_rate(self):
        """Some output devices (typically a raw ALSA device, not going through
        PipeWire/PulseAudio's automatic resampling) reject the TTS model's
        native sample rate outright (PortAudio error -9997 "Invalid sample
        rate"). Detect that when the device is set and resample instead of
        failing every line."""
        try:
            sd.check_output_settings(device=self.output_device, samplerate=self.sample_rate)
            return self.sample_rate
        except Exception:
            pass
        try:
            rate = int(sd.query_devices(self.output_device, "output")["default_samplerate"])
            log.info(f"[audio] output device doesn't support {self.sample_rate} Hz, "
                     f"resampling to {rate} Hz")
            return rate
        except Exception:
            return self.sample_rate

    def speak_blocking(self, text, tail_padding_seconds, cancel):
        """Generate and play `text`; returns early once `cancel` is set
        (also when that happens while the audio is still being generated)."""
        audio = self._model.generate_audio(self._voice_state, text)
        if cancel.is_set():
            return
        data = np.clip(audio.numpy(), -1.0, 1.0).astype("float32")
        pad = np.zeros(int(self.sample_rate * tail_padding_seconds), dtype="float32")
        data = np.concatenate([data, pad])
        rate = self._playback_rate
        if rate != self.sample_rate:
            data = resample_linear(data, self.sample_rate, rate)
        play_blocking(data, rate, self.output_device, cancel)
