from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MessageTokenStat:
    role: str
    tokens: int


@dataclass(frozen=True)
class TokenStats:
    total_tokens: int
    per_message: list[MessageTokenStat]


class TokenCounter:
    """Lightweight token estimator for prompt budgeting.

    We intentionally use an approximation to avoid optional heavy dependencies.
    """

    @staticmethod
    def count_text_tokens(text: str) -> int:
        normalized = text.strip()
        if not normalized:
            return 1
        return max(1, math.ceil(len(normalized) / 4))

    def count_message_tokens(self, message: dict[str, Any]) -> int:
        role = str(message.get("role") or "user").strip() or "user"
        content = str(message.get("content") or "")
        role_tokens = self.count_text_tokens(role)
        content_tokens = self.count_text_tokens(content)
        # Chat message envelope overhead.
        return role_tokens + content_tokens + 4

    def measure_messages(self, messages: list[dict[str, Any]]) -> TokenStats:
        per_message: list[MessageTokenStat] = []
        total = 0
        for message in messages:
            role = str(message.get("role") or "user")
            tokens = self.count_message_tokens(message)
            per_message.append(MessageTokenStat(role=role, tokens=tokens))
            total += tokens
        # Assistant priming overhead.
        total += 2
        return TokenStats(total_tokens=total, per_message=per_message)
