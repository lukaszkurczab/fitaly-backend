from dataclasses import dataclass, field
from typing import Any, Literal

RunStatus = Literal["started", "completed", "failed", "rejected"]


@dataclass(slots=True)
class AiRun:
    id: str
    user_id: str
    thread_id: str
    status: RunStatus
    created_at: int
    updated_at: int
    outcome: RunStatus | None = None
    failure_reason: str | None = None
    planner_used: bool = False
    tools_used: list[str] = field(default_factory=list)
    tool_metrics: list[dict[str, Any]] = field(default_factory=list)
    summary_used: bool = False
    truncated: bool = False
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
