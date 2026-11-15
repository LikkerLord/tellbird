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


WINDOWS_APIS = [{"name": "MME", "default_output_device": 0},
                {"name": "Windows DirectSound", "default_output_device": 3},
                {"name": "Windows WASAPI", "default_output_device": 6},
                {"name": "Windows WDM-KS", "default_output_device": 8}]
WINDOWS_DEVICES = [
    device("Microsoft Sound Mapper - Output", 0),
    device("Speakers (Realtek(R) Audio)", 0),
    device("CABLE Input (VB-Audio Virtual C", 0),          # MME cuts names off
    device("Primary Sound Driver", 1),                     # = the system default
    device("Speakers (Realtek(R) Audio)", 1),
    device("CABLE Input (VB-Audio Virtual Cable)", 1),
    device("Speakers (Realtek(R) Audio)", 2),
    device("CABLE Input (VB-Audio Virtual Cable)", 2),
    device("Speakers (Realtek HD Audio output)", 3),
    device("Primary Sound Capture Driver", 1, outputs=0),
]


def test_windows_lists_each_device_once_through_directsound(monkeypatch):
    audio = load_audio(monkeypatch, WINDOWS_DEVICES, WINDOWS_APIS)
    assert audio.output_device_names() == [
        "CABLE Input (VB-Audio Virtual Cable), Windows DirectSound",
        "Speakers (Realtek(R) Audio), Windows DirectSound",
    ]


def test_output_device_names_survive_portaudio_errors(monkeypatch):
    audio = load_audio(monkeypatch, [], [])

    def broken():
        raise OSError("PortAudio not initialized")

    audio.sd.query_devices = broken
    assert audio.output_device_names() == []


LINUX_APIS = [{"name": "ALSA"}, {"name": "OSS"}, {"name": "JACK Audio Connection Kit"}]


def test_linux_lists_sound_server_devices_only(monkeypatch):
    audio = load_audio(monkeypatch, [
        device("HDA ATI HDMI: 0 (hw:1,3)", 0), device("dmix", 0), device("default", 0),
        device("USB Audio Speakers", 2), device("WH-1000XM4", 2),
        device("Bluetooth internal capture stream for WH-1000XM4", 2, outputs=1),
    ], LINUX_APIS)
    assert audio.output_device_names() == ["USB Audio Speakers", "WH-1000XM4"]


def test_linux_without_sound_server_lists_alsa_hardware(monkeypatch):
    audio = load_audio(monkeypatch, [
        device("HDA ATI HDMI: 0 (hw:1,3)", 0), device("surround51", 0), device("pipewire", 0),
    ], LINUX_APIS)
    assert audio.output_device_names() == ["HDA ATI HDMI: 0 (hw:1,3)"]


def test_jack_elsewhere_doesnt_hide_other_devices(monkeypatch):
    audio = load_audio(monkeypatch, [device("Speakers", 0), device("system", 1)],
                       [{"name": "Windows WASAPI"}, {"name": "JACK Audio Connection Kit"}])
    assert audio.output_device_names() == ["Speakers", "system"]
