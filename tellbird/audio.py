"""Speech synthesis (Kyutai Pocket TTS) and playback (sounddevice).

This is the only module that pulls in torch and PortAudio; everything else
(and the tests) can be imported without them."""

import logging

import numpy as np
import sounddevice as sd

log = logging.getLogger("tellbird")


def check_output_device(device):
    """Raise ValueError if `device` (name substring, or None for the system
    default) isn't a usable output device."""
    if device is None:
        return
    try:
        sd.query_devices(device, "output")
    except Exception as e:
        raise ValueError(str(e))


def output_device_names():
    try:
        return sorted({d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0})
    except Exception:
        return []


def resample_linear(data, orig_rate, target_rate):
    if orig_rate == target_rate or len(data) == 0:
        return data
    duration = len(data) / orig_rate
    n_target = max(1, int(round(duration * target_rate)))
    x_orig = np.linspace(0, duration, num=len(data), endpoint=False)
    x_target = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_target, x_orig, data).astype("float32")


class Speaker:
    """The loaded TTS model plus the current voice and output device."""

    def __init__(self, language):
        from pocket_tts import TTSModel
        log.info("Loading Pocket TTS model ...")
        self._model = TTSModel.load_model(language=language)
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
                from .config import KNOWN_VOICES
                log.warning(f"[config] voice {name!r} failed to load: {e}")
                raise ValueError(f"unknown voice {name!r} (built-in voices include: "
                                 f"{', '.join(KNOWN_VOICES)})")
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

    def speak_blocking(self, text, tail_padding_seconds):
        audio = self._model.generate_audio(self._voice_state, text)
        data = np.clip(audio.numpy(), -1.0, 1.0).astype("float32")
        pad = np.zeros(int(self.sample_rate * tail_padding_seconds), dtype="float32")
        data = np.concatenate([data, pad])
        rate = self._playback_rate
        if rate != self.sample_rate:
            data = resample_linear(data, self.sample_rate, rate)
        sd.play(data, samplerate=rate, device=self.output_device)
        sd.wait()

    def stop(self):
        """Abort whatever is playing right now."""
        sd.stop()
