from datetime import UTC, datetime

import httpx
import pytest
from tenacity import RetryCallState, Retrying, wait_fixed

from jobwatch.ratelimit import RateLimiter
from jobwatch.retry import (
    RetryableResponseError,
    WaitRetryAfter,
    build_retrying,
    parse_retry_after,
)

from .conftest import FakeTime

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", 2.0),
        ("1.5", 1.5),
        ("-3", 0.0),
        ("Wed, 25 Nov 2026 12:00:30 GMT", 30.0),
        ("Wed, 25 Nov 2026 11:59:00 GMT", 0.0),
        ("demain", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value, now=NOW) == expected


def test_parse_retry_after_http_date_uses_current_time() -> None:
    assert parse_retry_after("Wed, 01 Jan 2020 00:00:00 GMT") == 0.0


def test_retryable_error_from_response() -> None:
    error = RetryableResponseError.from_response(httpx.Response(429, headers={"Retry-After": "7"}))
    assert error.status_code == 429
    assert error.retry_after == 7.0
    assert str(error) == "HTTP 429"


def _state_with(exc: BaseException) -> RetryCallState:
    state = RetryCallState(Retrying(), fn=None, args=(), kwargs={})
    state.set_exception((type(exc), exc, None))
    return state


def test_wait_uses_retry_after_capped() -> None:
    wait = WaitRetryAfter(wait_fixed(9), cap=60)
    assert wait(_state_with(RetryableResponseError(429, 3))) == 3
    assert wait(_state_with(RetryableResponseError(429, 600))) == 60
    assert wait(_state_with(RetryableResponseError(503, None))) == 9
    assert wait(_state_with(httpx.ConnectError("x"))) == 9


def test_build_retrying_retries_then_reraises(fake_time: FakeTime) -> None:
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        raise RetryableResponseError(503)

    with pytest.raises(RetryableResponseError):
        build_retrying(target="t", max_attempts=3, sleep=fake_time.sleep)(flaky)
    assert len(calls) == 3
    assert fake_time.sleeps == [1, 2]  # backoff exponentiel


def test_build_retrying_does_not_retry_other_errors(fake_time: FakeTime) -> None:
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise KeyError("x")

    with pytest.raises(KeyError):
        build_retrying(target="t", sleep=fake_time.sleep)(broken)
    assert len(calls) == 1


def test_rate_limiter_spaces_calls(fake_time: FakeTime) -> None:
    limiter = RateLimiter(4, clock=fake_time.clock, sleep=fake_time.sleep)
    for _ in range(3):
        limiter.acquire()
    assert fake_time.sleeps == [0.25, 0.25]
    fake_time.advance(10)
    limiter.acquire()
    assert fake_time.sleeps == [0.25, 0.25]


def test_rate_limiter_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError, match="positif"):
        RateLimiter(0)
