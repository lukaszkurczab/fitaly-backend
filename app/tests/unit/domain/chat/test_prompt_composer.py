from __future__ import annotations

import json
from typing import Any, cast

from app.domain.chat.prompt_composer import PromptComposer

_DEVELOPER_PAYLOAD_KEYS = {
    "contract",
    "language",
    "responseMode",
    "responseShape",
    "grounding",
    "brandCore",
    "styleRules",
    "responseBlueprint",
    "antiListingPolicy",
    "dataQualityWording",
    "scopeCorrectionPolicy",
    "rules",
}

_GROUNDING_KEYS = {
    "planner",
    "scope",
    "profileSummary",
    "goalContext",
    "nutritionSummary",
    "comparison",
    "mealLoggingQuality",
    "appHelpContext",
    "chatSummary",
    "threadMemory",
    "styleProfile",
}

_PROFILE_SUMMARY_KEYS = {
    "goal",
    "activityLevel",
    "preferences",
    "allergies",
    "language",
    "aiPersona",
    "styleProfile",
}

_NUTRITION_SUMMARY_KEYS = {
    "period",
    "loggingCoverage",
    "totals",
    "dailyBreakdown",
    "signals",
    "reliability",
}

_PERIOD_KEYS = {"type", "startDate", "endDate", "timezone", "isPartial"}
_LOGGING_COVERAGE_KEYS = {
    "daysInPeriod",
    "daysWithEntries",
    "mealCount",
    "coverageLevel",
}
_NUTRITION_TOTAL_KEYS = {"kcal", "proteinG", "fatG", "carbsG"}
_DAILY_BREAKDOWN_KEYS = {"date", "mealCount", "kcal", "proteinG", "fatG", "carbsG"}

_FORBIDDEN_PROVIDER_KEYS = (
    "rawProfile",
    "rawUserProfile",
    "rawFirestoreDoc",
    "rawMeals",
    "mealHistory",
    "history",
    "rawMessages",
    "rawPrompt",
    "rawResponse",
    "logs",
    "debug",
    "userId",
    "threadId",
    "email",
)

_FORBIDDEN_PROVIDER_SENTINELS = (
    "secret-profile",
    "secret-history",
    "secret-chat",
    "secret-log",
    "secret-user-id",
    "secret-thread-id",
    "secret@example.com",
)


def _base_grounding() -> dict[str, Any]:
    return {
        "planner": {
            "taskType": "mixed_capability_answer",
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "capabilities": [
                "resolve_time_scope",
                "get_goal_context",
                "get_nutrition_period_summary",
            ],
        },
        "scope": {"type": "calendar_week"},
        "profileSummary": {
            "aiPersona": "calm_guide",
            "styleProfile": {"id": "calm_guide", "label": "Calm Guide"},
        },
        "styleProfile": {"id": "calm_guide", "label": "Calm Guide"},
        "nutritionSummary": {"loggingCoverage": {"coverageLevel": "low"}},
    }


def _assert_no_forbidden_provider_context(value: Any) -> None:
    if isinstance(value, dict):
        raw_map = cast(dict[object, Any], value)
        for raw_key, item in raw_map.items():
            if not isinstance(raw_key, str):
                continue
            key = raw_key
            assert key not in _FORBIDDEN_PROVIDER_KEYS
            _assert_no_forbidden_provider_context(item)
        return
    if isinstance(value, list):
        raw_list = cast(list[Any], value)
        for item in raw_list:
            _assert_no_forbidden_provider_context(item)
        return

    serialized = json.dumps(value, ensure_ascii=False)
    for forbidden_key in _FORBIDDEN_PROVIDER_KEYS:
        if forbidden_key.startswith("raw") or forbidden_key in {"userId", "threadId", "email"}:
            assert forbidden_key not in serialized
    for forbidden_sentinel in _FORBIDDEN_PROVIDER_SENTINELS:
        assert forbidden_sentinel not in serialized


def test_prompt_composer_builds_structured_messages_without_blob_sections() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Jak jadlem w tym tygodniu?",
    )

    messages = composer.compose_messages(prompt_input)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "developer"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Jak jadlem w tym tygodniu?"

    developer_payload = json.loads(messages[1]["content"])
    assert developer_payload["contract"] == "fitaly_chat_v2_grounded_response"
    assert "grounding" in developer_payload

    # Guard against legacy PROFILE/HISTORY prompt blobs.
    developer_raw = messages[1]["content"]
    assert "PROFILE=" not in developer_raw
    assert "HISTORY=" not in developer_raw
    assert "MEALS_CONTEXT=" not in developer_raw


def test_prompt_composer_sanitizes_provider_bound_developer_payload() -> None:
    composer = PromptComposer()
    nutrition_summary: dict[str, Any] = {
        "period": {
            "type": "calendar_week",
            "startDate": "2026-04-13",
            "endDate": "2026-04-19",
            "timezone": "Europe/Warsaw",
            "isPartial": False,
            "debug": "secret-log",
        },
        "loggingCoverage": {
            "daysInPeriod": 7,
            "daysWithEntries": 5,
            "mealCount": 14,
            "coverageLevel": "medium",
            "userId": "secret-user-id",
        },
        "totals": {
            "kcal": 8400,
            "proteinG": 380,
            "fatG": 250,
            "carbsG": 980,
            "protein": 999,
            "fiberG": 50,
            "email": "secret@example.com",
        },
        "dailyBreakdown": [
            {
                "date": "2026-04-13",
                "mealCount": 3,
                "kcal": 1800,
                "proteinG": 95,
                "fatG": 70,
                "carbsG": 210,
                "mealId": "secret-history",
                "rawMeals": ["secret-history"],
            }
        ],
        "signals": ["logging_partial"],
        "reliability": {
            "summaryConfidence": "medium",
            "reason": "partial_logging_coverage",
            "debug": "secret-log",
        },
        "rawMeals": ["secret-history"],
        "mealHistory": ["secret-history"],
    }
    grounding: dict[str, Any] = {
        **_base_grounding(),
        "unknownTopLevelContext": {"debug": "secret-log"},
        "planner": {
            **_base_grounding()["planner"],
            "rawPrompt": "secret-log",
            "debug": {"trace": "secret-log"},
            "userId": "secret-user-id",
        },
        "scope": {
            "type": "calendar_week",
            "startDate": "2026-04-13",
            "endDate": "2026-04-19",
            "timezone": "Europe/Warsaw",
            "isPartial": False,
            "threadId": "secret-thread-id",
        },
        "profileSummary": {
            "goal": "maintenance",
            "activityLevel": "moderate",
            "preferences": ["quick dinners"],
            "allergies": ["peanuts"],
            "language": "en",
            "displayName": "Ala",
            "persona": "calm",
            "aiPersona": "calm_guide",
            "styleProfile": {
                "id": "calm_guide",
                "label": "Calm Guide",
                "debug": "secret-log",
            },
            "rawProfile": {"email": "secret@example.com"},
            "rawUserProfile": "secret-profile",
            "email": "secret@example.com",
        },
        "goalContext": {
            "goal": "maintenance",
            "calorieTarget": 2200,
            "proteinStrategy": "steady protein at each meal",
            "activeGoal": {"type": "maintenance", "targetKcal": 2200},
            "rawFirestoreDoc": {"private": "secret-profile"},
            "userId": "secret-user-id",
        },
        "nutritionSummary": nutrition_summary,
        "comparison": {
            "currentPeriod": nutrition_summary,
            "previousPeriod": {
                **nutrition_summary,
                "totals": {
                    "kcal": 7600,
                    "proteinG": 350,
                    "fatG": 230,
                    "carbsG": 900,
                    "debug": "secret-log",
                },
            },
            "coverageGuard": {
                "comparable": True,
                "reason": "ok",
                "logs": ["secret-log"],
            },
            "delta": {
                "kcal": {"absolute": 800, "percentage": 10.53, "debug": "secret-log"},
                "daysWithEntries": {
                    "absolute": 1,
                    "percentage": None,
                    "threadId": "secret-thread-id",
                },
                "customMetric": {
                    "absolute": -2,
                    "percentage": -4.5,
                    "email": "secret@example.com",
                },
                "rawMetric": "secret-history",
            },
            "baseline": {"kcal": 7600},
            "current": {"kcal": 8400},
            "history": ["secret-history"],
        },
        "mealLoggingQuality": {
            "coverageLevel": "medium",
            "daysWithEntries": 5,
            "missingDays": 2,
            "canSupportTrendAnalysis": True,
            "signals": ["weekend_gap"],
            "logs": ["secret-log"],
        },
        "appHelpContext": {
            "topic": "chat",
            "answerFacts": ["Chat uses bounded app context."],
            "debug": "secret-log",
        },
        "chatSummary": {
            "summary": "User asks about protein.",
            "resolvedFacts": ["prefers simple dinners"],
            "lastTurns": [{"role": "user", "content": "Earlier nutrition question."}],
            "hasSummary": True,
            "source": "memory_summary",
            "rawMessages": ["secret-chat"],
        },
        "threadMemory": {
            "summary": "Protein focus.",
            "resolvedFacts": ["goal: maintain"],
            "lastTurns": [
                {"role": "user", "content": "How did I eat?"},
                {
                    "role": "assistant",
                    "content": "You asked about protein.",
                    "debug": "secret-log",
                },
            ],
            "hasSummary": True,
            "source": "recent_turns_fallback",
            "rawMessages": ["secret-chat"],
            "rawResponse": "secret-chat",
        },
        "styleProfile": {
            "id": "focused_coach",
            "label": "Focused Coach",
            "debug": "secret-log",
        },
    }
    prompt_input = composer.build_prompt_input(
        language="en",
        response_mode="assessment_plus_guidance",
        grounding=grounding,
        user_message="How did I eat this week?",
    )

    messages = composer.compose_messages(prompt_input)
    developer_payload = json.loads(messages[1]["content"])

    assert set(developer_payload) == _DEVELOPER_PAYLOAD_KEYS
    assert set(developer_payload["grounding"]) == _GROUNDING_KEYS
    _assert_no_forbidden_provider_context(developer_payload)

    provider_grounding = developer_payload["grounding"]
    assert set(provider_grounding["planner"]) == {
        "taskType",
        "responseMode",
        "needsFollowUp",
        "capabilities",
    }
    assert set(provider_grounding["scope"]) == _PERIOD_KEYS

    profile_summary = provider_grounding["profileSummary"]
    assert set(profile_summary) == _PROFILE_SUMMARY_KEYS
    assert profile_summary["goal"] == "maintenance"
    assert profile_summary["activityLevel"] == "moderate"
    assert profile_summary["preferences"] == ["quick dinners"]
    assert profile_summary["allergies"] == ["peanuts"]
    assert profile_summary["language"] == "en"
    assert profile_summary["aiPersona"] == "calm_guide"
    assert profile_summary["styleProfile"] == {
        "id": "calm_guide",
        "label": "Calm Guide",
    }

    goal_context = provider_grounding["goalContext"]
    assert set(goal_context) == {"goal", "calorieTarget", "proteinStrategy"}
    assert goal_context["calorieTarget"] == 2200

    nutrition = provider_grounding["nutritionSummary"]
    assert set(nutrition) == _NUTRITION_SUMMARY_KEYS
    assert set(nutrition["period"]) == _PERIOD_KEYS
    assert set(nutrition["loggingCoverage"]) == _LOGGING_COVERAGE_KEYS
    assert set(nutrition["totals"]) == _NUTRITION_TOTAL_KEYS
    assert nutrition["totals"]["proteinG"] == 380
    assert set(nutrition["dailyBreakdown"][0]) == _DAILY_BREAKDOWN_KEYS
    assert nutrition["signals"] == ["logging_partial"]
    assert set(nutrition["reliability"]) == {"summaryConfidence", "reason"}

    comparison = provider_grounding["comparison"]
    assert set(comparison) == {
        "currentPeriod",
        "previousPeriod",
        "coverageGuard",
        "delta",
    }
    assert set(comparison["currentPeriod"]) == _NUTRITION_SUMMARY_KEYS
    assert set(comparison["previousPeriod"]["totals"]) == _NUTRITION_TOTAL_KEYS
    assert set(comparison["coverageGuard"]) == {"comparable", "reason"}
    assert set(comparison["delta"]) == {"kcal", "daysWithEntries", "customMetric"}
    assert comparison["delta"]["kcal"] == {"absolute": 800.0, "percentage": 10.53}
    assert comparison["delta"]["daysWithEntries"] == {
        "absolute": 1.0,
        "percentage": None,
    }
    assert comparison["delta"]["customMetric"] == {
        "absolute": -2.0,
        "percentage": -4.5,
    }

    assert set(provider_grounding["mealLoggingQuality"]) == {
        "coverageLevel",
        "daysWithEntries",
        "missingDays",
        "canSupportTrendAnalysis",
    }
    assert provider_grounding["appHelpContext"]["answerFacts"] == [
        "Chat uses bounded app context."
    ]
    assert set(provider_grounding["appHelpContext"]) == {"topic", "answerFacts"}

    assert set(provider_grounding["chatSummary"]) == {
        "summary",
        "resolvedFacts",
        "lastTurns",
        "hasSummary",
        "source",
    }
    assert provider_grounding["chatSummary"]["summary"] == "User asks about protein."
    assert provider_grounding["chatSummary"]["resolvedFacts"] == ["prefers simple dinners"]
    assert provider_grounding["chatSummary"]["lastTurns"] == [
        {"role": "user", "content": "Earlier nutrition question."}
    ]

    assert set(provider_grounding["threadMemory"]) == {
        "summary",
        "resolvedFacts",
        "lastTurns",
        "hasSummary",
        "source",
    }
    assert provider_grounding["threadMemory"]["summary"] == "Protein focus."
    assert provider_grounding["threadMemory"]["resolvedFacts"] == ["goal: maintain"]
    assert provider_grounding["threadMemory"]["lastTurns"] == [
        {"role": "user", "content": "How did I eat?"},
        {"role": "assistant", "content": "You asked about protein."},
    ]
    assert provider_grounding["styleProfile"] == {
        "id": "focused_coach",
        "label": "Focused Coach",
    }
    assert messages[2] == {"role": "user", "content": "How did I eat this week?"}


def test_prompt_composer_adds_bounded_persona_style_rules() -> None:
    composer = PromptComposer()
    grounding = _base_grounding()
    grounding["styleProfile"] = {
        "id": "cheerful_companion",
        "label": "Cheerful Companion",
    }
    prompt_input = composer.build_prompt_input(
        language="en",
        response_mode="assessment_plus_guidance",
        grounding=grounding,
        user_message="How did I eat this week?",
    )

    developer_payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])

    assert developer_payload["styleRules"]["aiPersona"] == "cheerful_companion"
    assert "lightly encouraging" in developer_payload["styleRules"]["expression"]
    guardrails = " ".join(developer_payload["styleRules"]["guardrails"])
    assert "non-judgmental" in guardrails
    assert "No diagnosis" in guardrails
    assert "no shame" in guardrails
    assert "no aggressive fitness tone" in guardrails


def test_prompt_composer_persona_changes_expression_not_core_rules() -> None:
    composer = PromptComposer()

    focused = _base_grounding()
    focused["styleProfile"] = {"id": "focused_coach", "label": "Focused Coach"}
    mediterranean = _base_grounding()
    mediterranean["styleProfile"] = {
        "id": "mediterranean_friend",
        "label": "Mediterranean Friend",
    }

    focused_payload = json.loads(
        composer.compose_messages(
            composer.build_prompt_input(
                language="en",
                response_mode="assessment_plus_guidance",
                grounding=focused,
                user_message="How did I eat?",
            )
        )[1]["content"]
    )
    mediterranean_payload = json.loads(
        composer.compose_messages(
            composer.build_prompt_input(
                language="en",
                response_mode="assessment_plus_guidance",
                grounding=mediterranean,
                user_message="How did I eat?",
            )
        )[1]["content"]
    )

    assert focused_payload["styleRules"]["expression"] != mediterranean_payload["styleRules"]["expression"]
    assert focused_payload["styleRules"]["guardrails"] == mediterranean_payload["styleRules"]["guardrails"]


def test_prompt_composer_enforces_verdict_first_blueprint_for_analytical_modes() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Ocen moj tydzien i cel redukcji.",
    )

    messages = composer.compose_messages(prompt_input)
    developer_payload = json.loads(messages[1]["content"])

    assert developer_payload["responseShape"] == "mixed_weekly_summary_and_goal"
    blueprint = developer_payload["responseBlueprint"]
    assert blueprint["style"] == "verdict_first_product_analysis"
    assert blueprint["order"] == [
        "verdict",
        "coverage_data_quality",
        "key_observations",
        "practical_next_step",
        "optional_focused_follow_up",
    ]


def test_prompt_composer_defaults_to_analysis_not_listing() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Jak jadlem w tym tygodniu?",
    )
    developer_payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert developer_payload["antiListingPolicy"]["explicitListingRequested"] is False
    assert developer_payload["responseShape"] == "mixed_weekly_summary_and_goal"

    listing_prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Wypisz wszystkie posilki z tego tygodnia.",
    )
    listing_payload = json.loads(composer.compose_messages(listing_prompt_input)[1]["content"])
    assert listing_payload["antiListingPolicy"]["explicitListingRequested"] is True
    assert listing_payload["responseShape"] == "explicit_listing_request"


def test_prompt_composer_contains_low_coverage_wording_guidance() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=_base_grounding(),
        user_message="Ocen moj tydzien.",
    )
    developer_payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])

    wording = developer_payload["dataQualityWording"]
    assert "Widze tylko czesc wpisow z tego tygodnia." in wording["preferredPolish"]
    assert "nie mam dostepu do pelnej historii" in wording["avoid"]


def test_prompt_composer_resolves_mixed_app_help_and_nutrition_shape() -> None:
    composer = PromptComposer()
    grounding: dict[str, Any] = {
        "planner": {
            "taskType": "mixed_capability_answer",
            "responseMode": "assessment_plus_guidance",
            "needsFollowUp": False,
            "capabilities": [
                "get_app_help_context",
                "resolve_time_scope",
                "get_nutrition_period_summary",
            ],
        },
        "appHelpContext": {"answerFacts": ["f1"]},
        "nutritionSummary": {"loggingCoverage": {"coverageLevel": "medium"}},
    }
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding=grounding,
        user_message="Jak dziala chat i jak jadlem w tygodniu?",
    )

    payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert payload["responseShape"] == "mixed_app_help_and_nutrition"


def test_prompt_composer_resolves_app_help_only_shape() -> None:
    composer = PromptComposer()
    grounding: dict[str, Any] = {
        "planner": {
            "taskType": "app_help_only",
            "responseMode": "concise_answer",
            "needsFollowUp": False,
            "capabilities": ["get_app_help_context"],
        },
        "appHelpContext": {"answerFacts": ["f1", "f2"]},
    }
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="concise_answer",
        grounding=grounding,
        user_message="Jak działa chat w Fitaly i z czego korzysta?",
    )

    payload = json.loads(composer.compose_messages(prompt_input)[1]["content"])
    assert payload["responseShape"] == "app_help_only"
    assert payload["responseBlueprint"]["style"] == "system_specific_explainer"


def test_prompt_composer_refusal_helper() -> None:
    composer = PromptComposer()
    assert "Fitaly" in composer.build_refusal_response("pl")
    assert "Fitaly" in composer.build_refusal_response("en")
