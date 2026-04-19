from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any


class RetryPolicy:
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        timeout_seconds: float = 30.0,
        base_delay_seconds: float = 0.5,
        jitter_seconds: float = 0.2,
        max_delay_seconds: float = 4.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.timeout_seconds = max(0.001, timeout_seconds)
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self.max_delay_seconds = max(self.base_delay_seconds, max_delay_seconds)
        self.sleep_fn = sleep_fn

    async def run_with_retry(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await asyncio.wait_for(fn(), timeout=self.timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not self._is_retryable(exc) or attempt >= self.max_attempts:
                    raise

                delay = min(
                    self.max_delay_seconds,
                    self.base_delay_seconds * (2 ** (attempt - 1)),
                )
                if self.jitter_seconds > 0:
                    delay += random.uniform(0.0, self.jitter_seconds)
                await self.sleep_fn(delay)

        if last_error is not None:
            raise last_error
        raise RuntimeError("RetryPolicy reached unexpected terminal state.")

    def is_retryable(self, exc: Exception) -> bool:
        return self._is_retryable(exc)

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, asyncio.TimeoutError | TimeoutError):
            return True

        for candidate in (exc, exc.__cause__, exc.__context__):
            if candidate is None:
                continue
            status_code = getattr(candidate, "status_code", None)
            if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
                return True
            status = getattr(candidate, "status", None)
            if isinstance(status, int) and (status == 429 or status >= 500):
                return True
            name = candidate.__class__.__name__.lower()
            if any(
                marker in name
                for marker in (
                    "ratelimit",
                    "timeout",
                    "apiconnection",
                    "serviceunavailable",
                )
            ):
                return True

        return False
