from __future__ import annotations

import json
from types import SimpleNamespace

from app.core.token_counter import TokenCounter
from app.domain.chat.context_builder import ContextBuilder
from app.domain.chat.prompt_composer import PromptComposer


def _planner_result() -> SimpleNamespace:
    return SimpleNamespace(
        task_type="data_grounded_answer",
        response_mode="assessment_plus_guidance",
        needs_follow_up=False,
        follow_up_question=None,
        capabilities=[SimpleNamespace(name="resolve_time_scope")],
        query_understanding=SimpleNamespace(topics=["nutrition"]),
    )


def _memory_summary() -> SimpleNamespace:
    return SimpleNamespace(
        summary="Uzytkownik pyta o bialko i kalorie.",
        resolved_facts=["cel:maintain"],
    )


def test_context_builder_resolves_nested_tool_args() -> None:
    builder = ContextBuilder()
    resolved = builder.resolve_tool_args(
        raw_args={
            "startDate": "$tool.resolve_time_scope.startDate",
            "scopeType": "$tool.resolve_time_scope.type",
            "plain": "today",
        },
        tool_outputs={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
            }
        },
    )
    assert resolved == {
        "startDate": "2026-04-19",
        "scopeType": "today",
        "plain": "today",
    }


def test_context_builder_low_coverage_grounding_trims_daily_breakdown() -> None:
    builder = ContextBuilder(max_recent_turns=6)
    grounding = builder.build_grounding(
        planner_result=_planner_result(),
        tool_outputs={
            "get_nutrition_period_summary": {
                "period": {"type": "rolling_7d"},
                "loggingCoverage": {"coverageLevel": "low"},
                "dailyBreakdown": [{"date": "2026-04-19", "kcal": 1200}],
            },
            "get_app_help_context": {"topic": "meal_logging", "answerFacts": ["a", "b", "c", "d", "e", "f"]},
        },
        recent_turns=[
            {"role": "user", "content": "A" * 500},
            {"role": "assistant", "content": "ok"},
        ],
        memory_summary=_memory_summary(),
    )
    assert "dailyBreakdown" not in grounding["nutritionSummary"]
    assert len(grounding["appHelpContext"]["answerFacts"]) == 5
    assert len(grounding["threadMemory"]["lastTurns"]) == 2
    assert grounding["threadMemory"]["lastTurns"][0]["content"].endswith("…")


def test_context_builder_enforces_token_budget_preferring_summary() -> None:
    token_counter = TokenCounter()
    builder = ContextBuilder(
        token_counter=token_counter,
        max_recent_turns=10,
        soft_token_limit=120,
        hard_token_limit=180,
    )
    composer = PromptComposer()

    recent_turns = [
        {"role": "user", "content": f"turn-{index} " + ("x" * 120)}
        for index in range(10)
    ]
    grounding = builder.build_grounding(
        planner_result=_planner_result(),
        tool_outputs={
            "get_nutrition_period_summary": {
                "period": {"type": "rolling_7d"},
                "loggingCoverage": {"coverageLevel": "high"},
                "dailyBreakdown": [{"date": "2026-04-19", "kcal": 1800}] * 10,
            }
        },
        recent_turns=recent_turns,
        memory_summary=_memory_summary(),
    )
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=grounding,
        user_message="Podsumuj tydzien i daj kroki.",
    )
    messages = composer.compose_messages(prompt_input)
    token_stats = token_counter.measure_messages(messages)
    assert token_stats.total_tokens > 120

    budgeted_messages, budget = builder.enforce_token_budget(
        messages=messages,
        token_stats=token_stats,
        memory_summary=_memory_summary(),
    )

    assert budget.truncated is True
    assert budget.used_summary is True
    assert budget.history_turns < len(recent_turns)

    developer_payload = json.loads(
        next(item["content"] for item in budgeted_messages if item["role"] == "developer")
    )
    trimmed_turns = developer_payload["grounding"]["threadMemory"]["lastTurns"]
    assert len(trimmed_turns) <= 3
