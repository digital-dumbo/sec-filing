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