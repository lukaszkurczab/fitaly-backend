from dataclasses import dataclass
from typing import Literal

MessageRole = Literal["user", "assistant", "system"]
MessageStatus = Literal["accepted", "completed", "failed"]


@dataclass(slots=True)
class ChatMessage:
    id: str
    user_id: str
    thread_id: str
    role: MessageRole
    content: str
    created_at: int
    updated_at: int
    status: MessageStatus
    run_id: str | None = None
    client_message_id: str | None = None
    language: Literal["pl", "en"] | None = None
    deleted: bool = False
    last_synced_at: int | None = None
