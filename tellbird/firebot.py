"""Firebot actions: when a filter fires, optionally run a Firebot preset
effect list so you can react (timeout, warn in chat, log, ...). Configured
under "firebot_actions" in config.json; each filter maps to a preset list ID
(empty = no action)."""

import asyncio
import logging

log = logging.getLogger("tellbird")


async def _post(settings, preset_id, user, message, filter_name):
    from aiohttp import ClientSession, ClientTimeout
    url = f"{settings.firebot_api_base}/api/v1/effects/preset/{preset_id}"
    if settings["firebot_actions.async"]:
        url += "/run"          # async: Firebot doesn't wait for effects to finish
    payload = {"username": user or "unknown",
               "args": {"filter": filter_name, "message": message}}
    try:
        async with ClientSession(timeout=ClientTimeout(total=4)) as s:
            async with s.post(url, json=payload) as r:
                if r.status >= 400:
                    log.warning(f"[firebot] {filter_name}: HTTP {r.status}")
    except Exception as e:
        log.warning(f"[firebot] {filter_name}: {e}")


def fire(settings, filter_name, user, message):
    """Fire-and-forget: trigger the Firebot preset mapped to this filter."""
    if not settings["firebot_actions.enabled"]:
        return
    preset = settings.firebot_presets.get(filter_name)
    if not preset:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_post(settings, preset, user, message, filter_name))
    log.info(f"[firebot] triggered '{filter_name}' for {user or '(unknown)'}")
