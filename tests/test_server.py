import json

from aiohttp.test_utils import TestClient, TestServer

from conftest import run
from tellbird.server import create_app


def with_client(make_bot, test):
    """Run `await test(client, bot)` against a real server with fake audio."""
    async def go():
        bot = make_bot()
        async with TestClient(TestServer(create_app(bot))) as client:
            await test(client, bot)
    run(go())


def queued(bot):
    items = []
    while not bot.queue.empty():
        items.append(bot.queue.get_nowait())
    return items


def test_tts_request_shapes(make_bot):
    async def test(client, bot):
        assert (await client.get("/tts", params={"text": "hello there"})).status == 200
        assert (await client.post("/tts", json={"user": "a", "text": "json body"})).status == 200
        assert (await client.post("/tts", data={"text": "form body"})).status == 200
        r = await client.post("/tts?user=a", data='raw "quoted" body',
                              headers={"Content-Type": "text/plain"})
        assert r.status == 200
        # Mix It Up-style broken JSON falls back to the raw body.
        r = await client.post("/tts", data='{"text": "he said "hi""}',
                              headers={"Content-Type": "application/json"})
        assert r.status == 200
        assert queued(bot) == ["hello there.", "json body.", "form body.",
                               'raw "quoted" body.', '{"text": "he said "hi""}.']
    with_client(make_bot, test)


def test_tts_rejects_non_object_json(make_bot):
    async def test(client, bot):
        r = await client.post("/tts", json=["hello"])
        assert r.status == 400
        assert queued(bot) == []
    with_client(make_bot, test)


def test_tts_skips_ignored_and_empty(make_bot):
    async def test(client, bot):
        assert (await client.get("/tts", params={"user": "Nightbot", "text": "hi"})).status == 204
        assert (await client.get("/tts", params={"text": "   "})).status == 204
        assert queued(bot) == []
    with_client(make_bot, test)


def test_skip_and_clear(make_bot):
    async def test(client, bot):
        await bot.enqueue("one")
        await bot.enqueue("two")
        assert await (await client.get("/clear")).text() == "cleared 2"
        await client.get("/skip")
        assert bot.speaker.stops == 2
        assert bot.queue.empty()
    with_client(make_bot, test)


def test_cors_open_for_tts_but_not_api(make_bot):
    async def test(client, bot):
        r = await client.get("/health", headers={"Origin": "https://evil.example"})
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        r = await client.get("/api/config", headers={"Origin": "https://evil.example"})
        assert r.status == 403
        r = await client.get("/api/config")
        assert r.status == 200
    with_client(make_bot, test)


def test_config_snapshot(make_bot):
    async def test(client, bot):
        snap = await (await client.get("/api/config")).json()
        assert snap["values"]["voice"] == "alba"
        assert snap["suggest"]["devices"] == ["Speakers"]
        assert snap["unapplied"] == [] and snap["restart_pending"] == []
        assert "panel" not in snap
    with_client(make_bot, test)


def test_save_config_writes_file_and_applies(make_bot, paths):
    async def test(client, bot):
        r = await client.post("/api/config", json={
            "values": {"voice": "anna", "max_chars": 50, "http_port": 6000},
            "abbreviations": {"brb": "be right back"}})
        result = await r.json()
        assert r.status == 200, result
        assert set(result["changed"]) >= {"voice", "max_chars", "abbreviations"}
        assert result["restart_pending"] == ["http_port"]
        assert bot.settings["voice"] == "anna" and bot.speaker.voice == "anna"
        assert bot.settings["http_port"] == 5050
        assert bot.chat_filter.clean_text("brb") == "be right back."
        text = paths.config.read_text()
        assert '"voice": "anna"' in text and "//" in text     # comments kept
        assert json.loads(paths.abbreviations.read_text()) == {"brb": "be right back"}
    with_client(make_bot, test)


def test_save_config_rejects_bad_values(make_bot, paths):
    async def test(client, bot):
        before = paths.config.read_text()
        r = await client.post("/api/config", json={"values": {
            "voice": "nosuchvoice", "output_device": "missing", "max_chars": 0, "bogus": 1}})
        assert r.status == 400
        assert set((await r.json())["errors"]) == {"voice", "output_device", "max_chars", "bogus"}
        assert paths.config.read_text() == before
        r = await client.post("/api/config", json=[1])
        assert r.status == 400
    with_client(make_bot, test)


def test_reload_picks_up_hand_edits(make_bot, paths):
    async def test(client, bot):
        paths.config.write_text(paths.config.read_text().replace('"max_chars": 300',
                                                                 '"max_chars": 120'))
        snap = await (await client.get("/api/config")).json()
        assert snap["unapplied"] == ["max_chars"]
        result = await (await client.post("/api/reload")).json()
        assert result["changed"] == ["max_chars"]
        assert bot.settings["max_chars"] == 120
    with_client(make_bot, test)


def test_chat_message_goes_through_the_filter(make_bot):
    async def test(client, bot):
        await bot.on_chat_message("alice", "Alice", "Kappa hi there", "25:0-4")
        await bot.on_chat_message("nightbot", "Nightbot", "ignored", "")
        assert queued(bot) == ["Alice says: hi there."]
    with_client(make_bot, test)


def test_panel_and_icon(make_bot):
    async def test(client, bot):
        r = await client.get("/")
        assert r.status == 200 and "/icon.png" in await r.text()
        r = await client.get("/icon.png")
        assert r.status == 200 and r.content_type == "image/png"
    with_client(make_bot, test)
