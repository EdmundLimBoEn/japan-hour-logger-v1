from __future__ import annotations

import time
from collections import deque

from radio_logger.models import RawDecode


class NetworkDedupe:
    """Suppress only identical network fingerprints inside a short window.

    This is not callsign dedupe. Twenty decodes of the same station remain
    twenty rows unless SNR/DT/DF/time/message all match within the window.
    """

    def __init__(self, window_seconds: float = 2.0):
        self.window_seconds = window_seconds
        self._seen: deque[tuple[float, str]] = deque()
        self._keys: set[str] = set()

    def is_duplicate(self, decode: RawDecode, now: float | None = None) -> bool:
        key = decode.fingerprint()
        ts = now if now is not None else time.monotonic()
        self._expire(ts)
        if key in self._keys:
            return True
        self._seen.append((ts, key))
        self._keys.add(key)
        return False

    def _expire(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._seen and self._seen[0][0] < cutoff:
            _, key = self._seen.popleft()
            self._keys.discard(key)
