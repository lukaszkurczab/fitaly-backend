from typing import Any

from app.core.coercion import coerce_int
from app.domain.ai_runs.models.ai_run import AiRun


def _as_str(value: object, *, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_int(value: object, *, default: int = 0) -> int:
    return coerce_int(value, fallback=default)


def _as_tools_used(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tools: list[str] = []
    for item in value:
        tool = _as_str(item).strip()
        if tool:
            tools.append(tool)
    return tools


def _as_tool_metrics(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def run_to_document(run: AiRun) -> dict[str, Any]:
    return {
        "runId": run.id,
        "userId": run.user_id,
        "threadId": run.thread_id,
        "status": run.status,
        "outcome": run.outcome,
        "failureReason": run.failure_reason,
        "plannerUsed": run.planner_used,
        "toolsUsed": run.tools_used,
        "toolMetrics": run.tool_metrics,
        "summaryUsed": run.summary_used,
        "truncated": run.truncated,
        "retryCount": run.retry_count,
        "promptTokens": run.prompt_tokens,
        "completionTokens": run.completion_tokens,
        "totalTokens": run.total_tokens,
        "totalLatencyMs": run.total_latency_ms,
        "createdAt": run.created_at,
        "updatedAt": run.updated_at,
        "metadata": run.metadata,
    }


def run_from_document(*, run_id: str, data: dict[str, Any]) -> AiRun:
    created_at = _as_int(data.get("createdAt"), default=0)
    updated_at = _as_int(data.get("updatedAt"), default=created_at)
    status = _as_str(data.get("status"), default="started")
    if status not in {"started", "completed", "failed", "rejected"}:
        status = "started"

    outcome = _as_str(data.get("outcome")).strip() or None
    if outcome is not None and outcome not in {"started", "completed", "failed", "rejected"}:
        outcome = None

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return AiRun(
        id=run_id,
        user_id=_as_str(data.get("userId")),
        thread_id=_as_str(data.get("threadId")),
        status=status,
        outcome=outcome,
        failure_reason=_as_str(data.get("failureReason")).strip() or None,
        planner_used=_as_bool(data.get("plannerUsed")),
        tools_used=_as_tools_used(data.get("toolsUsed")),
        tool_metrics=_as_tool_metrics(data.get("toolMetrics")),
        summary_used=_as_bool(data.get("summaryUsed")),
        truncated=_as_bool(data.get("truncated")),
        retry_count=_as_int(data.get("retryCount"), default=0),
        prompt_tokens=_as_int(data.get("promptTokens"), default=0),
        completion_tokens=_as_int(data.get("completionTokens"), default=0),
        total_tokens=_as_int(data.get("totalTokens"), default=0),
        total_latency_ms=_as_int(data.get("totalLatencyMs"), default=0),
        created_at=created_at,
        updated_at=updated_at,
        metadata=metadata,
    )
