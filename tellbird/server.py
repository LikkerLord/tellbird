"""The local HTTP server: control panel, /tts /skip /clear, and the settings
API the panel uses. Runs in both modes so the skip URL always works."""

import json
import logging
import re

from aiohttp import web

from .config import ConfigError

log = logging.getLogger("tellbird")


async def _read_tts_request(request):
    """-> (user, text) from any of the request shapes /tts accepts (see README)."""
    user = request.query.get("user", "")
    if request.method == "GET":
        return user, request.query.get("text", "")
    if request.content_type == "application/json":
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            # Bots like Mix It Up paste chat text into a JSON template with
            # no escaping, so a stray quote/backslash in the message breaks
            # the JSON. Fall back to the raw body as plain text.
            return user, await request.text()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text='expected a JSON object like {"text": "..."}')
        return data.get("user", user), data.get("text", "")
    if request.content_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
        data = await request.post()
        return data.get("user", user), data.get("text", "")
    # No structured encoding (e.g. Mix It Up's HTTP Custom Request, which
    # can't escape the message for JSON/form bodies): treat the whole POST
    # body as the literal text to speak, username via ?user= instead.
    return user, await request.text()


def _local_api_request(request):
    # The settings API can rewrite config.json (e.g. the Firebot URL), so
    # unlike /tts it must not be callable by other websites: no CORS, the
    # Origin (if any) must be this server, and the Host must be an IP or
    # "localhost" so a DNS-rebinding domain can't pose as same-origin.
    host = request.url.host or ""
    if host != "localhost" and not re.fullmatch(r"[\d.]+|\[?[0-9a-fA-F:]+\]?", host):
        return False
    origin = request.headers.get("Origin")
    return origin is None or origin == f"{request.scheme}://{request.host}"


@web.middleware
async def _cors(request, handler):
    if request.path.startswith("/api/"):
        if not _local_api_request(request):
            return web.json_response({"error": "forbidden"}, status=403)
        return await handler(request)
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def create_app(bot):
    """The aiohttp application for a running app.Bot."""

    async def handle_tts(request):
        user, text = await _read_tts_request(request)
        user = str(user).strip().lower()
        text = str(text).strip()
        if not text or bot.chat_filter.should_skip(user, text):
            return web.Response(status=204)
        cleaned = bot.chat_filter.clean_text(text, user)
        if not cleaned:
            return web.Response(status=204)
        log.info(f"[http] {cleaned}")
        await bot.enqueue(cleaned)      # speak the text as-is, no name prefix
        return web.Response(text="ok")

    async def handle_skip(request):
        # Abort the message currently being spoken; the worker advances to next.
        bot.skip()
        log.info("[skip] current message aborted")
        return web.Response(text="skipped")

    async def handle_clear(request):
        dropped = await bot.clear()
        log.info(f"[clear] dropped {dropped} queued, aborted current")
        return web.Response(text=f"cleared {dropped}")

    async def handle_health(request):
        return web.Response(text="ok")

    async def handle_panel(request):
        return web.FileResponse(bot.paths.panel_html)

    async def handle_config_get(request):
        return web.json_response(bot.config_snapshot())

    async def handle_config_post(request):
        if request.content_type != "application/json":
            return web.json_response({"error": "expected application/json"}, status=415)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)
        try:
            result = await bot.save_config(body.get("values") or {},
                                           body.get("abbreviations"), body.get("emotes"))
        except ValueError as e:
            return web.json_response({"errors": e.args[0]}, status=400)
        except (ConfigError, OSError) as e:
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response(result)

    async def handle_reload(request):
        try:
            return web.json_response(await bot.reload_config())
        except ConfigError as e:
            return web.json_response({"error": str(e)}, status=400)

    app = web.Application(middlewares=[_cors])
    app.router.add_route("*", "/tts", handle_tts)
    app.router.add_route("*", "/skip", handle_skip)
    app.router.add_route("*", "/clear", handle_clear)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_panel)
    app.router.add_get("/api/config", handle_config_get)
    app.router.add_post("/api/config", handle_config_post)
    app.router.add_post("/api/reload", handle_reload)
    return app


async def serve(bot, host, port):
    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info(f"Control panel: http://{host}:{port}/  (endpoints: /tts /skip /clear)")
