from __future__ import annotations

import json
from typing import Any

from app.domain.chat.planner import ChatPlanner


class _FakeOpenAIClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def responses_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.payload


def _base_query_understanding(*, mixed_request: bool = False) -> dict[str, Any]:
    return {
        "requiresUserData": True,
        "requestedScopeLabel": None,
        "mixedRequest": mixed_request,
        "topics": ["nutrition"],
    }


async def test_planner_mixed_intent_meal_history_and_goal_progress() -> None:
    client = _FakeOpenAIClient(
        {
            "taskType": "mixed_capability_answer",
            "queryUnderstanding": _base_query_understanding(mixed_request=True),
            "capabilities": [
                {"name": "resolve_time_scope", "priority": 4, "args": {"label": "this_week"}},
                {"name": "get_goal_context", "priority": 1, "args": {}},
                {"name": "get_profile_summary", "priority": 2, "args": {}},
                {
                    "name": "get_nutrition_period_summary",
                    "priority": 3,
                    "args": {"type": "calendar_week"},
                },
                {"name": "get_meal_logging_quality", "priority": 5, "args": {}},
            ],
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "followUpQuestion": None,
        }
    )
    planner = ChatPlanner(client)

    result = await planner.plan(
        user_id="user-1",
        user_message="Podsumuj ten tydzien i ocen postep celu.",
        recent_turns=[{"role": "user", "content": "Wczoraj jadlem malo bialka"}],
        memory_summary=None,
        language="pl",
    )

    assert result.task_type == "mixed_capability_answer"
    assert [cap.name for cap in result.capabilities] == [
        "get_goal_context",
        "get_profile_summary",
        "get_nutrition_period_summary",
        "resolve_time_scope",
        "get_meal_logging_quality",
    ]
    assert [cap.priority for cap in result.capabilities] == [1, 2, 3, 4, 5]


async def test_planner_mixed_app_help_and_nutrition() -> None:
    client = _FakeOpenAIClient(
        {
            "taskType": "mixed_capability_answer",
            "queryUnderstanding": {
                "requiresUserData": True,
                "requestedScopeLabel": "today",
                "mixedRequest": True,
                "topics": ["app_help", "nutrition"],
            },
            "capabilities": [
                {"name": "get_app_help_context", "priority": 1, "args": {"topic": "meal_logging"}},
                {"name": "resolve_time_scope", "priority": 2, "args": {"label": "today"}},
                {"name": "get_nutrition_period_summary", "priority": 3, "args": {"type": "today"}},
            ],
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "followUpQuestion": None,
        }
    )
    planner = ChatPlanner(client)

    result = await planner.plan(
        user_id="user-1",
        user_message="Jak dodac posilek i ile kalorii mam dzis?",
        recent_turns=[],
        memory_summary=None,
        language="pl",
    )

    assert result.task_type == "mixed_capability_answer"
    assert [cap.name for cap in result.capabilities] == [
        "get_app_help_context",
        "resolve_time_scope",
        "get_nutrition_period_summary",
    ]
    assert result.needs_follow_up is False


async def test_planner_out_of_scope_forces_refusal_without_capabilities() -> None:
    client = _FakeOpenAIClient(
        {
            "taskType": "out_of_scope_refusal",
            "queryUnderstanding": {
                "requiresUserData": False,
                "requestedScopeLabel": None,
                "mixedRequest": False,
                "topics": ["stocks"],
            },
            "capabilities": [
                {"name": "unknown_capability", "priority": 1, "args": {}},
                {"name": "get_nutrition_period_summary", "priority": 2, "args": {}},
            ],
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": True,
            "followUpQuestion": "Dopytam o cos",
        }
    )
    planner = ChatPlanner(client)

    result = await planner.plan(
        user_id="user-1",
        user_message="Jaki ETF kupic w tym kwartale?",
        recent_turns=[],
        memory_summary=None,
        language="pl",
    )

    assert result.task_type == "out_of_scope_refusal"
    assert result.capabilities == []
    assert result.response_mode == "refusal_redirect"
    assert result.needs_follow_up is False
    assert result.follow_up_question is None


async def test_planner_follow_up_required_edge_case_adds_default_question() -> None:
    client = _FakeOpenAIClient(
        {
            "taskType": "follow_up_required",
            "queryUnderstanding": {
                "requiresUserData": True,
                "requestedScopeLabel": None,
                "mixedRequest": False,
                "topics": ["nutrition"],
            },
            "capabilities": [{"name": "resolve_time_scope", "priority": 10, "args": {}}],
            "responseMode": "concise_answer",
            "needsFollowUp": True,
            "followUpQuestion": "",
        }
    )
    planner = ChatPlanner(client)

    result = await planner.plan(
        user_id="user-1",
        user_message="Podsumuj mi wyniki.",
        recent_turns=[],
        memory_summary=None,
        language="pl",
    )

    assert result.task_type == "follow_up_required"
    assert result.needs_follow_up is True
    assert result.follow_up_question == "Jaki dokladnie zakres czasu mam przeanalizowac?"
    assert [cap.name for cap in result.capabilities] == ["resolve_time_scope"]


async def test_planner_context_and_prompt_are_planning_only() -> None:
    client = _FakeOpenAIClient(
        {
            "taskType": "data_grounded_answer",
            "queryUnderstanding": _base_query_understanding(mixed_request=False),
            "capabilities": [{"name": "get_nutrition_period_summary", "priority": 1, "args": {}}],
            "responseMode": "concise_answer",
            "needsFollowUp": False,
            "followUpQuestion": None,
        }
    )
    planner = ChatPlanner(client)

    await planner.plan(
        user_id="user-1",
        user_message="Ile bialka mialem dzis?",
        recent_turns=[{"role": "assistant", "content": "Wczoraj bylo malo danych."}],
        memory_summary=None,
        language="pl",
    )

    call = client.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["temperature"] == 0.0
    assert call["schema"].__name__ == "PlannerResultDto"

    system_prompt = call["messages"][0]["content"]
    assert "Never answer the user directly." in system_prompt
    assert "Never execute tools." in system_prompt

    developer_context = json.loads(call["messages"][1]["content"])
    assert developer_context["userMessage"] == "Ile bialka mialem dzis?"
    assert developer_context["language"] == "pl"
    assert "get_nutrition_period_summary" in developer_context["allowedCapabilities"]
