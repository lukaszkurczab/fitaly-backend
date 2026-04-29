from __future__ import annotations

from app.schemas.ai_chat.request import ChatRunRequestDto
from app.tests.integration._ai_chat_v2_fixtures import (
    build_orchestrator_harness,
    generation_result,
    planner_result_payload,
)


async def test_ai_chat_v2_run_e2e_happy_path() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[
            {"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}},
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
            {
                "name": "get_meal_logging_quality",
                "priority": 3,
                "args": {
                    "startDate": "$tool.resolve_time_scope.startDate",
                    "endDate": "$tool.resolve_time_scope.endDate",
                    "timezone": "$tool.resolve_time_scope.timezone",
                },
            },
        ],
        response_mode="assessment_plus_guidance",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            },
            "get_nutrition_period_summary": {
                "period": {
                    "type": "today",
                    "startDate": "2026-04-19",
                    "endDate": "2026-04-19",
                    "timezone": "Europe/Warsaw",
                    "isPartial": True,
                },
                "loggingCoverage": {
                    "daysInPeriod": 1,
                    "daysWithEntries": 1,
                    "mealCount": 3,
                    "coverageLevel": "high",
                },
                "totals": {"kcal": 2100.0, "proteinG": 140.0, "fatG": 70.0, "carbsG": 210.0},
                "dailyBreakdown": [],
                "signals": ["logging_consistent"],
                "reliability": {
                    "summaryConfidence": "high",
                    "reason": "sufficient_logging_coverage",
                },
            },
            "get_meal_logging_quality": {
                "coverageLevel": "high",
                "daysWithEntries": 1,
                "missingDays": 0,
                "canSupportTrendAnalysis": True,
            },
        },
        generator_script=[
            generation_result(
                text="Masz dzisiaj dobre pokrycie logowania i stabilne bialko.",
                prompt_tokens=180,
                completion_tokens=42,
                total_tokens=222,
            )
        ],
    )

    response = await harness.orchestrator.run(
        user_id="user-1",
        request=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "client-1",
                "message": "Podsumuj moje dzisiejsze makro.",
                "language": "pl",
            }
        ),
    )

    assert response.thread_id == "thread-1"
    assert response.client_message_id == "client-1"
    assert response.reply == "Masz dzisiaj dobre pokrycie logowania i stabilne bialko."
    assert response.context_stats.used_summary is False
    assert response.context_stats.history_turns == 1
    assert response.context_stats.truncated is False
    assert response.context_stats.scope_decision == "ALLOW_NUTRITION"
    assert response.usage.total_tokens == 222
    assert response.credits is None
    assert response.persistence == "backend_owned"

    run = await harness.ai_run_service.get_run(run_id=response.run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.outcome == "completed"
    assert run.tools_used == [
        "resolve_time_scope",
        "get_nutrition_period_summary",
        "get_meal_logging_quality",
    ]
    assert run.total_tokens == 222

    summary = await harness.summary_service.get_current_summary(
        user_id="user-1",
        thread_id="thread-1",
    )
    assert summary is not None
    assert "Podsumuj moje dzisiejsze makro." in summary.summary
    assert "Masz dzisiaj dobre pokrycie" in summary.summary
    assert summary.covered_until_message_id == response.assistant_message_id
