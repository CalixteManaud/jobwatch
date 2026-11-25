"""Politique de retry HTTP commune (France Travail et Discord), basée sur tenacity.

Retry sur 429 / 5xx et erreurs de transport ; backoff exponentiel, sauf si le serveur impose un
délai via `Retry-After`, qui est alors respecté (plafonné).
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from jobwatch.log import get_logger

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0

log = get_logger(__name__)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """`Retry-After` en secondes (entier, décimal ou date HTTP), `None` si absent ou illisible."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    reference = now if now is not None else datetime.now(UTC)
    return max((when - reference).total_seconds(), 0.0)


class RetryableResponseError(Exception):
    """Réponse HTTP qui justifie un nouvel essai (429 / 5xx)."""

    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after

    @classmethod
    def from_response(cls, response: httpx.Response) -> "RetryableResponseError":
        return cls(response.status_code, parse_retry_after(response.headers.get("Retry-After")))


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableResponseError | httpx.TransportError)


class WaitRetryAfter(wait_base):
    """Attend `Retry-After` quand le serveur le fournit, sinon délègue au backoff exponentiel."""

    def __init__(self, fallback: wait_base, cap: float = MAX_RETRY_AFTER) -> None:
        self._fallback = fallback
        self._cap = cap

    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
            if isinstance(exc, RetryableResponseError) and exc.retry_after is not None:
                return min(exc.retry_after, self._cap)
        return self._fallback(retry_state)


def _log_before_sleep(target: str) -> Callable[[RetryCallState], None]:
    def _log(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        log.warning(
            "http.retry",
            target=target,
            attempt=retry_state.attempt_number,
            wait_s=round(retry_state.upcoming_sleep, 2),
            error=type(exc).__name__ if exc else None,
            detail=str(exc) if isinstance(exc, RetryableResponseError) else None,
        )

    return _log


def build_retrying(
    *,
    target: str,
    max_attempts: int = 5,
    sleep: Callable[[float], None] | None = None,
) -> Retrying:
    """Politique de retry ; `sleep` est injectable pour des tests sans attente réelle."""
    return Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=WaitRetryAfter(wait_exponential(multiplier=1, min=1, max=30)),
        retry=retry_if_exception(_is_retryable),
        before_sleep=_log_before_sleep(target),
        reraise=True,
        sleep=sleep if sleep is not None else time.sleep,
    )
