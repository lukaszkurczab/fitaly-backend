from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class ToolExecutionMetricDto(BaseModel):
    name: str
    duration_ms: int = Field(alias="durationMs")
    success: bool

class AiRunTelemetryDto(BaseModel):
    run_id: str = Field(alias="runId")
    user_id: str = Field(alias="userId")
    thread_id: str = Field(alias="threadId")
    planner_used: bool = Field(alias="plannerUsed")
    tools_used: List[str] = Field(alias="toolsUsed")
    tool_metrics: List[ToolExecutionMetricDto] = Field(alias="toolMetrics")
    summary_used: bool = Field(alias="summaryUsed")
    truncated: bool
    retry_count: int = Field(alias="retryCount")
    outcome: Literal["completed", "failed", "rejected"] 
    failure_reason: Optional[str] = Field(default=None, alias="failureReason")
    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    total_tokens: int = Field(alias="totalTokens")
    total_latency_ms: int = Field(alias="totalLatencyMs")