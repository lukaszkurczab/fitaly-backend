"""Rule-based gateway for deciding whether a request should reach OpenAI."""

import logging
from functools import lru_cache
from pathlib import Path
import re
from typing import Literal, TypedDict

from app.core.config import settings
from app.services.ai_classifier import AiClassifier
from app.services.content_guard_service import is_off_topic

Decision = Literal["FORWARD", "REJECT", "LOCAL_ANSWER"]

logger = logging.getLogger(__name__)
LOCAL_ANSWER_PATTERNS = (
    re.compile(r"^ile kalorii ma\s+.+\??$", re.IGNORECASE),
    re.compile(r"^how many calories (are in|in)\s+.+\??$", re.IGNORECASE),
)


class GatewayResult(TypedDict):
    decision: Decision
    reason: str
    score: float
    credit_cost: float


def _predict_on_topic_probability(message: str) -> float | None:
    if not settings.AI_GATEWAY_ML_ENABLED:
        return None

    try:
        classifier = _get_ml_classifier()
        if classifier is None:
            return None
        return classifier.predict(message)
    except Exception:
        logger.exception("Failed to evaluate AI gateway ML classifier.")
        return None


@lru_cache(maxsize=1)
def _get_ml_classifier() -> AiClassifier | None:
    model_path = Path(settings.AI_GATEWAY_ML_MODEL_PATH)
    if not model_path.exists():
        return None

    classifier = AiClassifier()
    classifier.load_model(model_path)
    return classifier


def _can_answer_locally(message: str) -> bool:
    return any(pattern.match(message) for pattern in LOCAL_ANSWER_PATTERNS)


def evaluate_request(
    user_id: str,
    action_type: str,
    message: str,
    *,
    language: str = "pl",
) -> GatewayResult:
    """Evaluate whether a request should be forwarded to OpenAI."""
    del user_id, action_type

    credit_full = 1.0
    reject_cost = float(settings.AI_REJECT_COST)
    local_cost = float(settings.AI_LOCAL_COST)
    normalized_message = message.strip()

    if not settings.AI_GATEWAY_ENABLED:
        return {
            "decision": "FORWARD",
            "reason": "GATEWAY_DISABLED",
            "score": 1.0,
            "credit_cost": credit_full,
        }

    if len(normalized_message) < 5:
        return {
            "decision": "REJECT",
            "reason": "TOO_SHORT",
            "score": -1.0,
            "credit_cost": reject_cost,
        }

    if is_off_topic(normalized_message, language):
        return {
            "decision": "REJECT",
            "reason": "OFF_TOPIC",
            "score": -0.8,
            "credit_cost": reject_cost,
        }

    if _can_answer_locally(normalized_message):
        return {
            "decision": "LOCAL_ANSWER",
            "reason": "LOCAL_SIMPLE_QUERY",
            "score": 0.7,
            "credit_cost": local_cost,
        }

    ml_probability = _predict_on_topic_probability(normalized_message)
    if (
        ml_probability is not None
        and ml_probability < settings.AI_GATEWAY_ML_THRESHOLD_OFF_TOPIC
    ):
        return {
            "decision": "REJECT",
            "reason": "ML_OFF_TOPIC",
            "score": ml_probability,
            "credit_cost": reject_cost,
        }

    return {
        "decision": "FORWARD",
        "reason": "OK",
        "score": 1.0,
        "credit_cost": credit_full,
    }
