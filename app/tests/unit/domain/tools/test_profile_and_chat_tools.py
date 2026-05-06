from typing import Any, cast

import pytest

from app.core.errors import ConsentRequiredError
from app.domain.chat_memory.models.memory_summary import MemorySummary
from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.tools.get_app_help_context import GetAppHelpContextTool
from app.domain.tools.get_goal_context import GetGoalContextTool
from app.domain.tools.get_profile_summary import GetProfileSummaryTool
from app.domain.tools.get_recent_chat_summary import GetRecentChatSummaryTool
from app.domain.users.models.user_profile import UserProfile
from app.domain.users.services.consent_service import ConsentService
from app.domain.users.services.user_profile_service import UserProfileService


class _FakeUserProfileService:
    def __init__(
        self, profile_summary: dict[str, Any], goal_context: dict[str, Any]
    ) -> None:
        self._profile_summary = profile_summary
        self._goal_context = goal_context

    async def get_profile_summary(self, *, user_id: str) -> dict[str, Any]:
        del user_id
        return self._profile_summary

    async def get_goal_context(self, *, user_id: str) -> dict[str, Any]:
        del user_id
        return self._goal_context


class _FakeSummaryService:
    def __init__(self, summary: MemorySummary | None) -> None:
        self.summary = summary

    async def get_current_summary(self, *, user_id: str, thread_id: str) -> MemorySummary | None:
        del user_id, thread_id
        return self.summary


class _FakeMessageService:
    def __init__(self, turns: list[dict[str, str]]) -> None:
        self.turns = turns
        self.called = False

    async def get_recent_turns(self, *, user_id: str, thread_id: str, limit: int) -> list[dict[str, str]]:
        del user_id, thread_id, limit
        self.called = True
        return self.turns


class _FakeConsentProfileService:
    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile

    async def get_profile(self, *, user_id: str) -> UserProfile | None:
        del user_id
        return self._profile


async def test_profile_and_goal_tools_return_structured_payloads() -> None:
    profile_tool = GetProfileSummaryTool(
        _FakeUserProfileService(
            profile_summary={
                "goal": "maintain",
                "activityLevel": "moderate",
                "preferences": ["high_protein"],
                "allergies": ["nuts"],
                "language": "pl",
                "aiPersona": "cheerful_companion",
                "styleProfile": {
                    "id": "cheerful_companion",
                    "label": "Cheerful Companion",
                },
            },
            goal_context={},
        )  # type: ignore[arg-type]
    )
    goal_tool = GetGoalContextTool(
        _FakeUserProfileService(
            profile_summary={},
            goal_context={
                "goal": "maintain",
                "calorieTarget": 2200,
                "proteinStrategy": "balanced_protein_intake",
            },
        )  # type: ignore[arg-type]
    )

    profile = await profile_tool.execute(user_id="user-1", args={})
    goal = await goal_tool.execute(user_id="user-1", args={})

    assert profile["activityLevel"] == "moderate"
    assert profile["preferences"] == ["high_protein"]
    assert profile["aiPersona"] == "cheerful_companion"
    assert profile["styleProfile"]["label"] == "Cheerful Companion"
    assert goal["calorieTarget"] == 2200


async def test_get_recent_chat_summary_prefers_summary_then_falls_back_to_turns() -> None:
    summary = MemorySummary(
        user_id="user-1",
        thread_id="thread-1",
        summary="Uzytkownik chce podsumowanie bialka.",
        resolved_facts=["fakt-a"],
        covered_until_message_id="msg-1",
        version=1,
        summary_model="gpt-4o-mini",
        created_at=1,
        updated_at=2,
    )

    fallback_turns = [{"role": "user", "content": "hej"}]

    summary_service = _FakeSummaryService(summary=summary)
    message_service = _FakeMessageService(turns=fallback_turns)
    tool = GetRecentChatSummaryTool(
        cast(SummaryService, summary_service),
        cast(MessageService, message_service),
    )
    result = await tool.execute(
        user_id="user-1",
        args={"threadId": "thread-1"},
    )
    assert result["hasSummary"] is True
    assert result["source"] == "memory_summary"
    assert message_service.called is False

    fallback_tool = GetRecentChatSummaryTool(
        cast(SummaryService, _FakeSummaryService(summary=None)),
        cast(MessageService, message_service),
    )
    fallback = await fallback_tool.execute(
        user_id="user-1",
        args={"threadId": "thread-1", "fallbackTurnsLimit": 4},
    )
    assert fallback["hasSummary"] is False
    assert fallback["source"] == "recent_turns_fallback"
    assert fallback["lastTurns"] == fallback_turns


async def test_get_app_help_context_returns_deterministic_facts() -> None:
    tool = GetAppHelpContextTool()
    result = await tool.execute(user_id="user-1", args={"topic": "meal_logging"})
    assert result["topic"] == "meal_logging"
    assert len(result["answerFacts"]) >= 2
    assert any("Meals" in item or "podsumowania" in item for item in result["answerFacts"])


async def test_get_app_help_context_normalizes_chat_topic() -> None:
    tool = GetAppHelpContextTool()
    result = await tool.execute(user_id="user-1", args={"topic": "chat_v2"})
    assert result["topic"] == "chat"
    assert any("/api/v2/ai/chat/runs" in item for item in result["answerFacts"])


async def test_consent_service_enforces_ai_health_data_consent() -> None:
    service_without_consent = ConsentService(
        _FakeConsentProfileService(
            UserProfile(
                user_id="user-1",
                readiness_status="needs_ai_consent",
            )
        )  # type: ignore[arg-type]
    )
    assert await service_without_consent.has_ai_health_data_consent(user_id="user-1") is False
    with pytest.raises(ConsentRequiredError):
        await service_without_consent.ensure_ai_health_data_consent(user_id="user-1")

    service_with_profile_only = ConsentService(
        _FakeConsentProfileService(
            UserProfile(
                user_id="user-1",
                readiness_status="needs_ai_consent",
                readiness_onboarding_completed_at="2026-04-18T10:00:00Z",
            )
        )  # type: ignore[arg-type]
    )
    assert await service_with_profile_only.has_ai_health_data_consent(user_id="user-1") is False

    service_with_consent = ConsentService(
        _FakeConsentProfileService(
            UserProfile(
                user_id="user-1",
                readiness_status="ready",
                readiness_onboarding_completed_at="2026-04-18T10:00:00Z",
                readiness_ready_at="2026-04-19T10:00:00Z",
                ai_health_data_consent_at="2026-04-19T10:00:00Z",
            )
        )  # type: ignore[arg-type]
    )
    await service_with_consent.ensure_ai_health_data_consent(user_id="user-1")


async def test_user_profile_service_reuses_user_account_profile_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_user_profile_data(user_id: str) -> dict[str, Any]:
        assert user_id == "user-1"
        return {
            "profile": {
                "language": "en-US",
                "nutritionProfile": {
                    "goal": "gain",
                    "activityLevel": "high",
                    "calorieTarget": 2800,
                    "preferences": ["vegan"],
                    "allergies": ["soy"],
                },
                "aiPreferences": {
                    "stylePersona": "focused_coach",
                },
                "consents": {
                    "aiHealthDataConsentAt": "2026-04-10T11:00:00Z",
                },
                "readiness": {
                    "status": "ready",
                    "onboardingCompletedAt": "2026-04-10T10:00:00Z",
                    "readyAt": "2026-04-10T11:00:00Z",
                },
            },
        }

    monkeypatch.setattr(
        "app.domain.users.services.user_profile_service.user_account_service.get_user_profile_data",
        _fake_get_user_profile_data,
    )

    service = UserProfileService()
    profile = await service.get_profile(user_id="user-1")
    assert profile is not None
    assert profile.language == "en"
    assert profile.calorie_target == 2800
    assert profile.ai_persona == "focused_coach"
    assert profile.style_profile == {"id": "focused_coach", "label": "Focused Coach"}
    assert profile.readiness_status == "ready"
    assert profile.readiness_ready_at == "2026-04-10T11:00:00Z"

    goal_context = await service.get_goal_context(user_id="user-1")
    assert goal_context["proteinStrategy"] == "higher_protein_with_calorie_surplus"


async def test_user_profile_service_bounds_future_ai_persona_to_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_user_profile_data(user_id: str) -> dict[str, Any]:
        del user_id
        return {
            "profile": {
                "language": "pl",
                "nutritionProfile": {
                    "goal": "maintain",
                },
                "aiPreferences": {
                    "stylePersona": "Mediterranean Friend",
                },
            },
        }

    monkeypatch.setattr(
        "app.domain.users.services.user_profile_service.user_account_service.get_user_profile_data",
        _fake_get_user_profile_data,
    )

    service = UserProfileService()
    summary = await service.get_profile_summary(user_id="user-1")

    assert summary["aiPersona"] == "mediterranean_friend"
    assert summary["styleProfile"] == {
        "id": "mediterranean_friend",
        "label": "Mediterranean Friend",
    }
