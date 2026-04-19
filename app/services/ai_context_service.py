"""Legacy v1 AI context assembly for `/api/v1/ai/ask`.

Canonical AI Chat v2 context flow is implemented in `app/domain/chat/*`.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.core.exceptions import FirestoreServiceError
from app.services import chat_thread_service, conversation_memory_service, meal_service, user_account_service


class ChatContext(TypedDict):
    profile: dict[str, Any]
    meals: list[dict[str, Any]]
    history_messages: list[dict[str, Any]]
    memory_summary: str | None
    warnings: list[str]


async def build_chat_context(user_id: str, thread_id: str) -> ChatContext:
    warnings: list[str] = []

    profile: dict[str, Any] = {}
    meals: list[dict[str, Any]] = []
    history_messages: list[dict[str, Any]] = []
    memory_summary: str | None = None

    try:
        profile_data = await user_account_service.get_user_profile_data(user_id)
        profile = profile_data or {}
    except (FirestoreServiceError, Exception):
        warnings.append("PROFILE_UNAVAILABLE")

    try:
        meals_items, _ = await meal_service.list_history(user_id, limit_count=5)
        meals = meals_items
    except (FirestoreServiceError, Exception):
        warnings.append("MEALS_UNAVAILABLE")

    try:
        history_desc, _ = await chat_thread_service.list_messages(
            user_id,
            thread_id,
            limit_count=24,
        )
        history_messages = list(reversed(history_desc))
    except (FirestoreServiceError, Exception):
        warnings.append("HISTORY_UNAVAILABLE")

    try:
        memory_summary = await conversation_memory_service.get_thread_summary(user_id, thread_id)
    except (FirestoreServiceError, Exception):
        warnings.append("MEMORY_UNAVAILABLE")

    return {
        "profile": profile,
        "meals": meals,
        "history_messages": history_messages,
        "memory_summary": memory_summary,
        "warnings": warnings,
    }


def resolve_language(explicit_language: str | None, profile: dict[str, Any]) -> str:
    candidate = (explicit_language or "").strip().lower()
    if candidate.startswith("pl"):
        return "pl"
    if candidate.startswith("en"):
        return "en"

    profile_language = str(profile.get("language") or "").strip().lower()
    if profile_language.startswith("pl"):
        return "pl"
    if profile_language.startswith("en"):
        return "en"

    return "pl"


def has_ai_health_data_consent(profile: dict[str, Any]) -> bool:
    consent_at = profile.get("aiHealthDataConsentAt")
    if isinstance(consent_at, str) and consent_at.strip():
        return True

    # Transitional fallback for prelaunch profiles completed before explicit consent field existed.
    return bool(profile.get("surveyComplited"))
