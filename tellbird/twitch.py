"""Reading Twitch chat over the anonymous IRC-over-WebSocket interface."""

import asyncio
import logging
import random
import re
import ssl

log = logging.getLogger("tellbird")

TWITCH_WS = "wss://irc-ws.chat.twitch.tv:443"
PRIVMSG_RE = re.compile(r":(\w+)!\w+@[\w.]+ PRIVMSG #\w+ :(.*)")


def _ssl_context():
    # Build an SSL context from certifi so wss:// works even when macOS Python
    # has no local CA certificates (the common "CERTIFICATE_VERIFY_FAILED").
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_privmsg(line):
    """IRC line -> (login, display name, message, emotes tag), or None if
    it isn't a chat message."""
    tags = {}
    if line.startswith("@"):
        tag_str, _, line = line[1:].partition(" ")
        for kv in tag_str.split(";"):
            key, _, val = kv.partition("=")
            tags[key] = val
    m = PRIVMSG_RE.match(line)
    if not m:
        return None
    login = m.group(1).lower()
    message = m.group(2).rstrip("\r\n").rstrip(" \u0001")
    display = tags.get("display-name") or m.group(1)
    return login, display, message, tags.get("emotes", "")


class TwitchChat:
    """Stays connected to the configured channel (reconnecting on errors)
    and hands every chat message to on_message(login, display, message,
    emotes_tag)."""

    def __init__(self, get_channel, on_message):
        self._get_channel = get_channel
        self._on_message = on_message
        self._ws = None

    async def rejoin(self):
        """Drop the connection; run() reconnects to the (new) channel."""
        if self._ws is not None:
            await self._ws.close()

    async def run(self):
        import websockets
        ssl_context = _ssl_context()
        while True:
            try:
                async with websockets.connect(TWITCH_WS, ssl=ssl_context) as ws:
                    self._ws = ws
                    channel = self._get_channel()
                    await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                    await ws.send(f"JOIN #{channel}")
                    log.info(f"Connected to #{channel}. Waiting for messages ...")
                    async for raw in ws:
                        for line in raw.split("\r\n"):
                            if line.startswith("PING"):
                                await ws.send("PONG :tmi.twitch.tv")
                            elif "PRIVMSG" in line:
                                parsed = parse_privmsg(line)
                                if parsed:
                                    await self._on_message(*parsed)
            except Exception as e:
                log.warning(f"[Connection lost] {e} - retrying in 5 s ...")
                await asyncio.sleep(5)
