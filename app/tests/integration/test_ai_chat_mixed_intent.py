from __future__ import annotations

from app.schemas.ai_chat.request import ChatRunRequestDto
from app.tests.integration._ai_chat_v2_fixtures import (
    build_orchestrator_harness,
    generation_result,
    planner_result_payload,
)


async def test_ai_chat_v2_mixed_intent_executes_multiple_capabilities() -> None:
    planner_result = planner_result_payload(
        task_type="mixed_capability_answer",
        mixed_request=True,
        topics=["meal_history", "goal_progress"],
        capabilities=[
            {"name": "resolve_time_scope", "priority": 1, "args": {"label": "this_week"}},
            {"name": "get_goal_context", "priority": 2, "args": {}},
            {
                "name": "get_nutrition_period_summary",
                "priority": 3,
                "args": {
                    "type": "$tool.resolve_time_scope.type",
                    "startDate": "$tool.resolve_time_scope.startDate",
                    "endDate": "$tool.resolve_time_scope.endDate",
                    "timezone": "$tool.resolve_time_scope.timezone",
                    "isPartial": "$tool.resolve_time_scope.isPartial",
                },
            },
        ],
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "calendar_week",
                "startDate": "2026-04-13",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            },
            "get_goal_context": {
                "goal": "maintain",
                "calorieTarget": 2200,
                "proteinStrategy": "balanced_protein_intake",
            },
            "get_nutrition_period_summary": {
                "period": {
                    "type": "calendar_week",
                    "startDate": "2026-04-13",
                    "endDate": "2026-04-19",
                    "timezone": "Europe/Warsaw",
                    "isPartial": True,
                },
                "loggingCoverage": {
                    "daysInPeriod": 7,
                    "daysWithEntries": 6,
                    "mealCount": 19,
                    "coverageLevel": "high",
                },
                "totals": {"kcal": 13900.0, "proteinG": 910.0, "fatG": 470.0, "carbsG": 1510.0},
                "dailyBreakdown": [],
                "signals": ["logging_consistent"],
                "reliability": {
                    "summaryConfidence": "high",
                    "reason": "sufficient_logging_coverage",
                },
            },
        },
        generator_script=[
            generation_result(text="Postep celu jest stabilny, a tydzien ma wysokie pokrycie danych.")
        ],
    )

    response = await harness.orchestrator.run(
        user_id="user-7",
        request=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-mixed",
                "clientMessageId": "client-mixed-1",
                "message": "Podsumuj tydzien i ocen postep celu.",
                "language": "pl",
            }
        ),
    )

    assert response.client_message_id == "client-mixed-1"
    assert response.context_stats.scope_decision == "ALLOW_NUTRITION"
    assert response.persistence == "backend_owned"
    assert response.credits is None
    assert response.reply.startswith("Postep celu")

    assert len(harness.tools["resolve_time_scope"].calls) == 1
    assert len(harness.tools["get_goal_context"].calls) == 1
    assert len(harness.tools["get_nutrition_period_summary"].calls) == 1
