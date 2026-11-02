from tellbird import startup
from tellbird.startup import Startup


def test_download_moves_in_and_out_of_its_stage():
    s = Startup()
    s.set(startup.VOICE)
    s.download_progress(0, 100)
    assert s.stage == startup.DOWNLOADING
    s.download_progress(50, 100)
    assert s.snapshot()["done"] == 50
    s.download_progress(100, 100)
    assert s.stage == startup.VOICE


def test_normal_start_stays_quiet():
    s = Startup()
    s.set(startup.LOADING)
    s.set(startup.READY)
    assert s.announcement() is None


def test_ready_is_announced_after_download_or_failure():
    s = Startup()
    s.download_progress(0, 100)
    assert "only happens once" in s.announcement()
    s.download_progress(100, 100)
    s.set(startup.READY)
    assert "ready" in s.announcement()

    s = Startup()
    s.fail("no internet")
    assert s.announcement() == "no internet"
    s.set(startup.READY)
    assert "ready" in s.announcement()


def test_ready_is_announced_after_a_slow_start():
    s = Startup()
    s._started -= startup.SLOW_START_SECONDS + 1
    s.set(startup.READY)
    assert "ready" in s.announcement()


def test_listeners_see_changes_once_and_the_current_stage_on_subscribe():
    s = Startup()
    s.set(startup.LOADING)
    seen = []
    s.subscribe(lambda status: seen.append(status.stage))
    s.set(startup.LOADING)
    s.set(startup.READY)
    assert seen == [startup.LOADING, startup.READY]


def test_menu_text():
    s = Startup()
    s.set(startup.DOWNLOADING)
    assert s.menu_text("twitch") == "Tellbird: Downloading the voice model…"
    s.set(startup.READY)
    assert s.menu_text("twitch") == "Tellbird (twitch mode)"
