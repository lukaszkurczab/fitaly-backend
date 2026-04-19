from dataclasses import dataclass


@dataclass(slots=True)
class ChatThread:
    id: str
    user_id: str
    title: str
    created_at: int
    updated_at: int
    last_message: str | None = None
    last_message_at: int | None = None
    status: str = "active"
