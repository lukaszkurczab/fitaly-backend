from __future__ import annotations

import asyncio

import pytest

from app.domain.chat.retry_policy import RetryPolicy


class _RetryableError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("retryable")
        self.status_code = status_code


async def _no_sleep(_: float) -> None:
    return None


async def test_retry_policy_retries_retryable_error_then_succeeds() -> None:
    attempts = 0

    async def _flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _RetryableError(status_code=503)
        return "ok"

    policy = RetryPolicy(
        max_attempts=3,
        timeout_seconds=1.0,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
        sleep_fn=_no_sleep,
    )
    result = await policy.run_with_retry(_flaky)
    assert result == "ok"
    assert attempts == 2


async def test_retry_policy_does_not_retry_non_retryable_error() -> None:
    attempts = 0

    async def _boom() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("bad input")

    policy = RetryPolicy(
        max_attempts=3,
        timeout_seconds=1.0,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
        sleep_fn=_no_sleep,
    )
    with pytest.raises(ValueError):
        await policy.run_with_retry(_boom)
    assert attempts == 1


async def test_retry_policy_handles_timeout_as_retryable() -> None:
    attempts = 0

    async def _slow_then_fast() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.sleep(0.03)
        return "done"

    policy = RetryPolicy(
        max_attempts=2,
        timeout_seconds=0.01,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
        sleep_fn=_no_sleep,
    )
    result = await policy.run_with_retry(_slow_then_fast)
    assert result == "done"
    assert attempts == 2
