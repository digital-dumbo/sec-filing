import threading
import time

import k10fetcher.rate_limit as rate_limit_module
from k10fetcher.rate_limit import RateLimiter


def test_rate_limiter_rejects_invalid_capacity() -> None:
    try:
        RateLimiter(0)
    except ValueError as exc:
        assert "max_calls" in str(exc)
    else:
        raise AssertionError("RateLimiter accepted invalid capacity")


def test_rate_limiter_sleeps_after_capacity_without_real_wait(monkeypatch):
    now = 100.0
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now + sum(sleeps)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(rate_limit_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rate_limit_module.time, "sleep", fake_sleep)

    limiter = RateLimiter(max_calls=1, period_seconds=1.0)
    limiter.acquire()
    limiter.acquire()

    assert sleeps == [1.0]


def test_rate_limiter_is_shared_across_threads() -> None:
    limiter = RateLimiter(max_calls=1, period_seconds=0.05)
    started = threading.Event()
    acquired_at: list[float] = []
    lock = threading.Lock()

    def acquire() -> None:
        started.wait()
        limiter.acquire()
        with lock:
            acquired_at.append(time.monotonic())

    threads = [threading.Thread(target=acquire) for _ in range(3)]
    for thread in threads:
        thread.start()
    started.set()
    for thread in threads:
        thread.join()

    acquired_at.sort()
    assert len(acquired_at) == 3
    assert acquired_at[-1] - acquired_at[0] >= 0.08
