import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: float = 1.0) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.period_seconds - (now - self._calls[0])
            time.sleep(max(sleep_for, 0.0))
