"""Gateway decision helper for /ai/ask chat traffic."""

from typing import Literal, TypedDict

from app.core.config import settings

Decision = Literal["FORWARD", "REJECT"]


class GatewayResult(TypedDict):
    decision: Decision
    reason: str
    score: float
    credit_cost: float


def evaluate_request(
    user_id: str,
    action_type: str,
    message: str,
    *,
    language: str = "pl",
) -> GatewayResult:
    """Evaluate whether a request should be forwarded to OpenAI."""
    del user_id, action_type, message, language

    if not settings.AI_GATEWAY_ENABLED:
        return {
            "decision": "FORWARD",
            "reason": "GATEWAY_DISABLED",
            "score": 1.0,
            "credit_cost": 1.0,
        }

    return {
        "decision": "FORWARD",
        "reason": "PASS_THROUGH",
        "score": 1.0,
        "credit_cost": 1.0,
    }
