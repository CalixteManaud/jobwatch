"""Throttling côté client : on reste sous la limite de l'API (10 appels/s) avec de la marge."""

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Garantit au moins `1 / rate_per_second` secondes entre deux appels à `acquire()`."""

    def __init__(
        self,
        rate_per_second: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second doit être strictement positif")
        self._interval = 1.0 / rate_per_second
        # résolus à l'exécution (et non à l'import) : remplaçables dans les tests
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._next_slot: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if self._next_slot is not None and now < self._next_slot:
                self._sleep(self._next_slot - now)
                now = self._next_slot
            self._next_slot = now + self._interval
