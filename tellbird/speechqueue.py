"""The queue of messages waiting to be spoken. Unlike asyncio.Queue it can be
listed and have single items removed, for the panel's queue monitor."""

import asyncio
import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field

_ids = itertools.count(1)


@dataclass
class QueueItem:
    spoken: str              # exactly what gets read aloud
    message: str = ""        # the cleaned chat message, for display
    user: str = ""           # display name, if known
    source: str = ""         # "chat" or "http"
    id: int = field(default_factory=lambda: next(_ids))
    queued_at: float = field(default_factory=time.time)
    # Set to skip this message, from any thread, whether it's still being
    # generated or already playing.
    cancel: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "spoken", "message", "user", "source", "queued_at")}


class SpeechQueue:
    def __init__(self):
        self._items: deque[QueueItem] = deque()
        self._wakeup = asyncio.Event()

    def __len__(self):
        return len(self._items)

    def items(self):
        return list(self._items)

    def put(self, item):
        self._items.append(item)
        self._wakeup.set()

    async def get(self):
        """Wait for and remove the next item."""
        while not self._items:
            self._wakeup.clear()
            await self._wakeup.wait()
        return self._items.popleft()

    def remove(self, item_id):
        """Drop one waiting item; False if it isn't (or is no longer) queued."""
        for item in self._items:
            if item.id == item_id:
                self._items.remove(item)
                return True
        return False

    def clear(self):
        """Drop every waiting item; returns how many there were."""
        n = len(self._items)
        self._items.clear()
        return n
