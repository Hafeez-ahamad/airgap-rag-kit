"""Per-user rate and concurrency limits (spec F15), in-process.

Correct for the native profile (one API process). With several API replicas each process
enforces its own limits, so the effective limit multiplies; a shared Redis-backed limiter is
listed as a gap in docs/STATUS.md.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass


class LimitExceeded(RuntimeError):
    def __init__(self, message: str, retry_after_s: int) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass
class _Bucket:
    tokens: float
    updated: float
    active: int = 0


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        max_concurrent: int,
        *,
        global_concurrent: int | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = per_minute / 60.0
        self._capacity = float(per_minute)
        self._max_concurrent = max_concurrent
        self._global_max = global_concurrent
        self._global_active = 0
        self._now = now
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @contextmanager
    def acquire(self, key: str) -> Iterator[None]:
        release = self.take(key)
        try:
            yield
        finally:
            release()

    def take(self, key: str) -> Callable[[], None]:
        """Reserve a slot now; returns an idempotent release function."""
        with self._lock:
            now = self._now()
            b = self._buckets.setdefault(key, _Bucket(self._capacity, now))
            b.tokens = min(self._capacity, b.tokens + (now - b.updated) * self._rate)
            b.updated = now
            if b.active >= self._max_concurrent:
                raise LimitExceeded("too many requests in progress; wait for one to finish", 2)
            if self._global_max is not None and self._global_active >= self._global_max:
                raise LimitExceeded("the assistant is busy; try again shortly", 5)
            if b.tokens < 1.0:
                wait = int((1.0 - b.tokens) / self._rate) + 1
                raise LimitExceeded("request rate limit reached", wait)
            b.tokens -= 1.0
            b.active += 1
            self._global_active += 1
        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                b.active -= 1
                self._global_active -= 1

        return release
