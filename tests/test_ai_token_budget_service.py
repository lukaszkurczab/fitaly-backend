from datetime import datetime, timezone

from app.services.ai_token_budget_service import build_budgeted_prompt


def test_build_budgeted_prompt_includes_structured_meals_context() -> None:
    today_key = datetime.now(timezone.utc).date().isoformat()
    result = build_budgeted_prompt(
        user_message="Jak oceniasz moje dzisiejsze jedzenie?",
        language="pl",
        profile={"goal": "maintain", "language": "pl"},
        meals=[
            {
                "dayKey": today_key,
                "name": "Owsianka",
                "totals": {"kcal": 520, "protein": 35, "fat": 16, "carbs": 58},
            },
            {
                "dayKey": "2026-04-16",
                "name": "Kurczak z ryzem",
                "totals": {"kcal": 640, "protein": 48, "fat": 14, "carbs": 74},
            },
        ],
        history_messages=[],
        memory_summary=None,
    )

    prompt = result["prompt"]
    assert "MEALS_CONTEXT=count=2" in prompt
    assert f"today={today_key}" in prompt
    assert "today_count=1" in prompt
    assert "today_totals=kcal:520,p:35,f:16,c:58" in prompt
    assert "Owsianka(kcal=520,p=35,f=16,c=58)" in prompt


def test_build_budgeted_prompt_adds_data_access_policy_line() -> None:
    result = build_budgeted_prompt(
        user_message="Podsumuj moje dzisiejsze makro.",
        language="pl",
        profile={},
        meals=[],
        history_messages=[],
        memory_summary=None,
    )

    prompt = result["prompt"]
    assert "Use backend-provided Fitaly context" in prompt
    assert "do not claim you lack access to the user's meal history" in prompt
