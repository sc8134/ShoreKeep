from backend.ratelimit import SlidingWindowLimiter


def test_blocks_after_limit():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=3600)

    assert limiter.allow("1.2.3.4")[0] is True
    assert limiter.allow("1.2.3.4")[0] is True

    allowed, retry_after = limiter.allow("1.2.3.4")
    assert allowed is False
    assert retry_after > 0
    assert limiter.allow("5.6.7.8")[0] is True


def test_zero_limit_disables_limiter():
    limiter = SlidingWindowLimiter(limit=0, window_seconds=60)
    for _ in range(50):
        assert limiter.allow("1.2.3.4")[0] is True
