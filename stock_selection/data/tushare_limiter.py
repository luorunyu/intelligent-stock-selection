from __future__ import annotations

from collections import deque
import threading
import time
from typing import Callable


class RateLimiter:
    """Sliding-window limiter for outbound API calls."""

    def __init__(
        self,
        max_calls: int = 90,
        window_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self.max_calls = max_calls
        self.window_seconds = float(window_seconds)
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, cost: int = 1) -> None:
        """Reserve call capacity, sleeping until enough room is available."""

        if cost < 1:
            raise ValueError("cost must be at least 1")
        if cost > self.max_calls:
            raise ValueError("cost cannot exceed max_calls")

        while True:
            with self._lock:
                now = self._clock()
                self._discard_expired(now)

                if len(self._calls) + cost <= self.max_calls:
                    for _ in range(cost):
                        self._calls.append(now)
                    return

                wait_seconds = self._calls[0] + self.window_seconds - now

            self._sleeper(max(wait_seconds, 0.0))

    def remaining(self) -> int:
        """Return currently available capacity in the active window."""

        with self._lock:
            self._discard_expired(self._clock())
            return self.max_calls - len(self._calls)

    def _discard_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

