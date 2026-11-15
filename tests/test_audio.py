import importlib.util
import sys
import types


def load_audio(monkeypatch, devices, hostapis):
    """tellbird.audio with numpy/sounddevice stubbed out (the tests don't have them)."""
    sd = types.SimpleNamespace(query_devices=lambda: devices, query_hostapis=lambda: hostapis)
    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    # Loaded fresh and not registered, so no other test gets this stubbed copy.
    spec = importlib.util.find_spec("tellbird.audio")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def device(name, hostapi, outputs=2):
    return {"name": name, "hostapi": hostapi, "max_output_channels": outputs}


def test_output_device_names_are_unambiguous(monkeypatch):
    hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    audio = load_audio(monkeypatch, [
        device("Speakers", 0), device("Speakers", 1),
        device("CABLE Input", 1),
        device("Microphone", 0, outputs=0),
    ], hostapis)
    assert audio.output_device_names() == [
        "CABLE Input", "Speakers, MME", "Speakers, Windows WASAPI"]


def test_output_device_names_survive_portaudio_errors(monkeypatch):
    audio = load_audio(monkeypatch, [], [])

    def broken():
        raise OSError("PortAudio not initialized")

    audio.sd.query_devices = broken
    assert audio.output_device_names() == []

