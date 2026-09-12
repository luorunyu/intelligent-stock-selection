"""线程安全的滑动窗口限流器，保护 Tushare 接口不被高频调用触发限制。"""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Callable


class RateLimiter:
    """按滑动时间窗预留 API 调用额度；支持对较重调用设置更高成本。"""

    def __init__(
        self,
        max_calls: int = 90,
        window_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """初始化限流上限，并允许测试时注入时钟和休眠函数。"""
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
        """预留调用额度；窗口已满时在锁外等待，避免阻塞其他线程清理过期记录。"""

        if cost < 1:
            raise ValueError("cost must be at least 1")
        if cost > self.max_calls:
            raise ValueError("cost cannot exceed max_calls")

        while True:
            with self._lock:
                now = self._clock()
                self._discard_expired(now)

                # 将一次调用按 cost 记入时间窗；pro_bar 等较重调用可占用更多额度。
                if len(self._calls) + cost <= self.max_calls:
                    for _ in range(cost):
                        self._calls.append(now)
                    return

                wait_seconds = self._calls[0] + self.window_seconds - now

            self._sleeper(max(wait_seconds, 0.0))

    def remaining(self) -> int:
        """返回当前滑动窗口中的剩余额度。"""

        with self._lock:
            self._discard_expired(self._clock())
            return self.max_calls - len(self._calls)

    def _discard_expired(self, now: float) -> None:
        """移除窗口左侧已过期的调用时间戳。"""
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
