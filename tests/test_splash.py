import subprocess

from tellbird import splash


def test_describe_each_stage():
    assert splash.describe(None)["progress"] == "busy"
    loading = splash.describe({"stage": "loading", "message": "Loading the voice model…"})
    assert loading["title"] == "Loading the voice model…" and loading["progress"] == "busy"
    down = splash.describe({"stage": "downloading", "message": "Downloading…",
                            "done": 50_000_000, "total": 200_000_000})
    assert down["progress"] == 0.25 and down["detail"].startswith("50 of 200 MB")
    failed = splash.describe({"stage": "failed", "error": "no voice"})
    assert failed["failed"] and failed["detail"] == "no voice" and failed["progress"] is None
    assert splash.describe({"stage": "ready", "message": "Ready"})["ready"]


def test_launch_runs_the_module_from_source(monkeypatch):
    started = []

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            started.append(cmd)

        def wait(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    monkeypatch.setattr(splash, "FROZEN", False)
    splash.launch("http://127.0.0.1:5050/")
    monkeypatch.setattr(splash, "FROZEN", True)
    splash.launch("http://127.0.0.1:5050/")
    assert started[0][1:] == ["-m", "tellbird.splash", "http://127.0.0.1:5050/"]
    assert started[1][1:] == ["--splash", "http://127.0.0.1:5050/"]


def test_launch_never_raises(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "Popen", broken)
    assert splash.launch("http://127.0.0.1:5050/") is None
