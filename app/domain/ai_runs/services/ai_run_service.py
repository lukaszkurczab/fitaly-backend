"""Canonical AI Chat v2 run persistence service."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.domain.ai_runs.models.ai_run import AiRun, RunStatus
from app.infra.firestore.mappers.ai_run_mapper import run_from_document, run_to_document
from app.infra.firestore.repositories.ai_run_repository import AiRunRepository


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class AiRunService:
    def __init__(self, run_repository: AiRunRepository) -> None:
        self._run_repository = run_repository

    def new_run_id(self) -> str:
        return f"run_{uuid4().hex}"

    async def get_run(self, *, run_id: str) -> AiRun | None:
        payload = await self._run_repository.get(run_id=run_id)
        if payload is None:
            return None
        return run_from_document(run_id=run_id, data=payload)

    async def create_run(
        self,
        *,
        run_id: str,
        user_id: str,
        thread_id: str,
        status: RunStatus = "started",
        metadata: dict[str, Any] | None = None,
    ) -> AiRun:
        now = _utc_now_ms()
        run = AiRun(
            id=run_id,
            user_id=user_id,
            thread_id=thread_id,
            status=status,
            outcome=None,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        await self._run_repository.upsert(run_id=run_id, payload=run_to_document(run))
        return run

    async def update_run(
        self,
        *,
        run_id: str,
        status: RunStatus,
        outcome: RunStatus | None = None,
        failure_reason: str | None = None,
        planner_used: bool | None = None,
        tools_used: list[str] | None = None,
        tool_metrics: list[dict[str, Any]] | None = None,
        summary_used: bool | None = None,
        truncated: bool | None = None,
        retry_count: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        total_latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "updatedAt": _utc_now_ms(),
        }
        if outcome is not None:
            payload["outcome"] = outcome
        if failure_reason is not None:
            payload["failureReason"] = failure_reason
        if planner_used is not None:
            payload["plannerUsed"] = planner_used
        if tools_used is not None:
            payload["toolsUsed"] = tools_used
        if tool_metrics is not None:
            payload["toolMetrics"] = tool_metrics
        if summary_used is not None:
            payload["summaryUsed"] = summary_used
        if truncated is not None:
            payload["truncated"] = truncated
        if retry_count is not None:
            payload["retryCount"] = retry_count
        if prompt_tokens is not None:
            payload["promptTokens"] = prompt_tokens
        if completion_tokens is not None:
            payload["completionTokens"] = completion_tokens
        if total_tokens is not None:
            payload["totalTokens"] = total_tokens
        if total_latency_ms is not None:
            payload["totalLatencyMs"] = total_latency_ms
        if metadata is not None:
            payload["metadata"] = metadata
        await self._run_repository.upsert(run_id=run_id, payload=payload)
