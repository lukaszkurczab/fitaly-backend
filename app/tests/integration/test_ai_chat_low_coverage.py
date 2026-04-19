from __future__ import annotations

import json

from app.schemas.ai_chat.request import ChatRunRequestDto
from app.tests.integration._ai_chat_v2_fixtures import (
    build_orchestrator_harness,
    generation_result,
    planner_result_payload,
)


async def test_ai_chat_v2_low_coverage_uses_trimmed_grounding() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[
            {"name": "resolve_time_scope", "priority": 1, "args": {"label": "rolling_7d"}},
            {
                "name": "get_nutrition_period_summary",
                "priority": 2,
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
                "type": "rolling_7d",
                "startDate": "2026-04-13",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            },
            "get_nutrition_period_summary": {
                "period": {
                    "type": "rolling_7d",
                    "startDate": "2026-04-13",
                    "endDate": "2026-04-19",
                    "timezone": "Europe/Warsaw",
                    "isPartial": True,
                },
                "loggingCoverage": {
                    "daysInPeriod": 7,
                    "daysWithEntries": 1,
                    "mealCount": 2,
                    "coverageLevel": "low",
                },
                "totals": {"kcal": 900.0, "proteinG": 45.0, "fatG": 25.0, "carbsG": 95.0},
                "dailyBreakdown": [
                    {"date": "2026-04-13", "mealCount": 0, "kcal": 0, "proteinG": 0, "fatG": 0, "carbsG": 0}
                ],
                "signals": ["logging_sparse"],
                "reliability": {
                    "summaryConfidence": "low",
                    "reason": "insufficient_logged_days",
                },
            },
        },
        generator_script=[
            generation_result(text="Masz niskie pokrycie logowania, wiec wnioski sa ograniczone.")
        ],
    )

    response = await harness.orchestrator.run(
        user_id="user-low",
        request=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-low",
                "clientMessageId": "client-low-1",
                "message": "Oceń moje 7 dni jedzenia.",
                "language": "pl",
            }
        ),
    )

    assert response.context_stats.tools_used == [
        "resolve_time_scope",
        "get_nutrition_period_summary",
    ]
    assert response.reply.startswith("Masz niskie pokrycie")

    sent_messages = harness.generator.calls[0]
    developer_payload = json.loads(
        next(message["content"] for message in sent_messages if message["role"] == "developer")
    )
    nutrition = developer_payload["grounding"]["nutritionSummary"]
    assert nutrition["loggingCoverage"]["coverageLevel"] == "low"
    assert "dailyBreakdown" not in nutrition
