from dataclasses import dataclass, field


@dataclass(slots=True)
class MemorySummary:
    user_id: str
    thread_id: str
    summary: str
    version: int
    created_at: int
    updated_at: int
    resolved_facts: list[str] = field(default_factory=list)
    covered_until_message_id: str | None = None
    summary_model: str | None = None
