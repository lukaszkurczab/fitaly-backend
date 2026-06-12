from __future__ import annotations

import json
from typing import Any, cast

from app.schemas.ai_chat.prompt import PromptBuildInputDto

SYSTEM_PROMPT = (
    "You are Fitaly AI Chat v2 assistant. "
    "Use only grounded backend data from developer payload. "
    "Prefer concise product analysis over raw record listing. "
    "Treat coverage/reliability/signals as first-class evidence. "
    "If data is partial, communicate limited confidence clearly. "
    "Never invent missing facts. "
    "Never provide medical diagnosis or treatment. "
    "Stay calm, supportive, smart, light, and non-judgmental."
)

_NUTRITION_CAPABILITIES = {
    "resolve_time_scope",
    "get_nutrition_period_summary",
    "compare_periods",
    "get_meal_logging_quality",
}

_WEEKLY_SCOPE_TYPES = {"calendar_week", "rolling_7d", "last_7d"}

_EXPLICIT_LISTING_MARKERS = (
    "wypisz",
    "lista",
    "listę",
    "dokladne posilki",
    "dokładne posiłki",
    "pokaz wszystkie posilki",
    "pokaż wszystkie posiłki",
    "szczegoly wpisow",
    "szczegóły wpisów",
    "show all meals",
    "list meals",
    "exact meals",
    "detailed entries",
)

_STYLE_RULES = {
    "calm_guide": {
        "label": "Calm Guide",
        "expression": "steady, clear, reassuring, minimal hype",
    },
    "cheerful_companion": {
        "label": "Cheerful Companion",
        "expression": "warmer and lightly encouraging without pressure",
    },
    "focused_coach": {
        "label": "Focused Coach",
        "expression": "direct, concise, action-oriented, no aggressive fitness tone",
    },
    "mediterranean_friend": {
        "label": "Mediterranean Friend",
        "expression": "warm, relaxed, food-positive, simple everyday framing",
    },
}

_CORE_STYLE_GUARDRAILS = [
    "Fitaly brand core is always calm, supportive, smart, light, and non-judgmental.",
    "Persona changes expression only; it never changes facts, safety, medical boundaries, or evidence confidence.",
    "No diagnosis, no treatment instructions, no shame, no guilt, no aggressive fitness tone.",
]

_GROUNDING_SECTION_ORDER = (
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
)

_NUTRITION_TOTAL_KEYS = ("kcal", "proteinG", "fatG", "carbsG")


class PromptComposer:
    def build_prompt_input(
        self,
        *,
        language: str,
        response_mode: str,
        grounding: dict[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "language": language if language in {"pl", "en"} else "pl",
            "responseMode": response_mode,
            "grounding": grounding,
            "userMessage": user_message,
        }
        dto = PromptBuildInputDto.model_validate(payload)
        return dto.model_dump(by_alias=True, exclude_none=True)

    def compose_messages(self, prompt_input: dict[str, Any]) -> list[dict[str, str]]:
        dto = PromptBuildInputDto.model_validate(prompt_input)
        grounding_payload = _sanitize_provider_grounding(
            dto.grounding.model_dump(by_alias=True, exclude_none=True)
        )

        explicit_listing_requested = self._is_explicit_listing_request(dto.user_message)
        response_shape = self._infer_response_shape(
            response_mode=dto.response_mode,
            grounding=grounding_payload,
            user_message=dto.user_message,
            explicit_listing_requested=explicit_listing_requested,
        )
        style_rules = self._style_rules(grounding_payload)
        developer_payload: dict[str, Any] = {
            "contract": "fitaly_chat_v2_grounded_response",
            "language": dto.language,
            "responseMode": dto.response_mode,
            "responseShape": response_shape,
            "grounding": grounding_payload,
            "brandCore": _CORE_STYLE_GUARDRAILS,
            "styleRules": style_rules,
            "responseBlueprint": self._response_blueprint(response_shape),
            "antiListingPolicy": {
                "explicitListingRequested": explicit_listing_requested,
                "defaultBehavior": "analysis_not_record_dump",
                "rule": (
                    "Do not list all meals/entries unless user explicitly asked "
                    "for listing details."
                ),
            },
            "dataQualityWording": {
                "preferredPolish": [
                    "Widze tylko czesc wpisow z tego tygodnia.",
                    "Na podstawie zapisow z tego tygodnia moge ocenic tylko fragment obrazu.",
                    "Dane sa niepelne, wiec ocena jest ograniczona.",
                ],
                "preferredEnglish": [
                    "I can see only part of this week's entries.",
                    "Based on this week's logs I can assess only part of the picture.",
                    "Data is incomplete, so confidence is limited.",
                ],
                "avoid": [
                    "nie mam dostepu do pelnej historii",
                    "i don't have access to full history",
                ],
            },
            "scopeCorrectionPolicy": {
                "rule": (
                    "If latest user message corrects previous time scope or intent, "
                    "follow latest correction over older context."
                ),
                "example": "Nie o dzis, tylko o caly tydzien -> use full-week scope.",
            },
            "rules": [
                "Use only facts from grounding payload.",
                "For analytical nutrition questions, answer in verdict-first structure.",
                "Analytical answer order is mandatory: verdict, coverage/data quality, 2-3 observations, practical next step, optional one follow-up question.",
                "Do not start analytical answers with meal entry enumeration.",
                "Coverage/reliability/signals must influence confidence and wording.",
                "With low/partial coverage, avoid confident reduction claims from logged kcal only.",
                "Goal-related questions require interpretation, not only target restatement.",
                "Persona/style settings are bounded voice controls only.",
                "Never use shame, guilt, diagnosis, treatment, or aggressive fitness pressure.",
                "If planner marked out_of_scope, refuse briefly and redirect to supported scope.",
                "Do not output hidden reasoning or internal chain-of-thought.",
            ],
        }

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "developer",
                "content": json.dumps(developer_payload, ensure_ascii=False),
            },
            {"role": "user", "content": dto.user_message},
        ]

    def build_refusal_response(self, language: str) -> str:
        if language == "en":
            return (
                "I can only help with Fitaly app usage, your in-app data, and nutrition topics."
            )
        return (
            "Mogę pomóc tylko w kwestiach Fitaly, Twoich danych w aplikacji oraz tematów żywieniowych."
        )

    def _style_rules(self, grounding: dict[str, Any]) -> dict[str, Any]:
        style_profile = grounding.get("styleProfile")
        style_map = cast(dict[str, Any], style_profile) if isinstance(style_profile, dict) else {}
        persona = str(style_map.get("id") or "").strip()
        if persona not in _STYLE_RULES:
            profile_summary = grounding.get("profileSummary")
            profile_map = (
                cast(dict[str, Any], profile_summary)
                if isinstance(profile_summary, dict)
                else {}
            )
            persona = str(profile_map.get("aiPersona") or "").strip()
        if persona not in _STYLE_RULES:
            persona = "calm_guide"

        selected = _STYLE_RULES[persona]
        return {
            "aiPersona": persona,
            "label": selected["label"],
            "expression": selected["expression"],
            "guardrails": _CORE_STYLE_GUARDRAILS,
        }

    def _infer_response_shape(
        self,
        *,
        response_mode: str,
        grounding: dict[str, Any],
        user_message: str,
        explicit_listing_requested: bool,
    ) -> str:
        planner = grounding.get("planner")
        planner_map = cast(dict[str, Any], planner) if isinstance(planner, dict) else {}
        task_type = str(planner_map.get("taskType") or "").strip()
        needs_follow_up = bool(planner_map.get("needsFollowUp"))
        capabilities_raw = planner_map.get("capabilities")
        capabilities: set[str]
        if isinstance(capabilities_raw, list):
            raw_list = cast(list[object], capabilities_raw)
            capabilities = {item for item in raw_list if isinstance(item, str)}
        else:
            capabilities = set()

        if task_type == "out_of_scope_refusal" or response_mode == "refusal_redirect":
            return "out_of_scope_refusal"
        if task_type == "follow_up_required" or needs_follow_up:
            return "follow_up_required"

        has_nutrition = bool(capabilities.intersection(_NUTRITION_CAPABILITIES))
        has_goal = "get_goal_context" in capabilities
        has_app_help = "get_app_help_context" in capabilities
        scope = grounding.get("scope")
        scope_map = cast(dict[str, Any], scope) if isinstance(scope, dict) else {}
        scope_type = (
            str(scope_map.get("type")).strip()
            if isinstance(scope_map.get("type"), str)
            else ""
        )

        if explicit_listing_requested and has_nutrition:
            return "explicit_listing_request"

        if has_app_help and has_nutrition:
            return "mixed_app_help_and_nutrition"
        if has_goal and has_nutrition:
            if scope_type in _WEEKLY_SCOPE_TYPES or "tydzien" in user_message.lower() or "week" in user_message.lower():
                return "mixed_weekly_summary_and_goal"
            return "mixed_nutrition_and_goal"
        if has_goal:
            return "goal_progress_feedback"

        message_lower = user_message.strip().lower()
        if "compare" in message_lower or "porown" in message_lower:
            return "pattern_analysis"

        if has_nutrition:
            analytical_markers = (
                "jak jad",
                "podsum",
                "ocen",
                "analiz",
                "how did i eat",
                "assess",
                "summary",
            )
            if any(marker in message_lower for marker in analytical_markers):
                return "history_summary"
            if response_mode == "comparison_plus_guidance":
                return "pattern_analysis"
            if response_mode == "assessment_plus_guidance":
                return "pattern_analysis"
            if scope_type in _WEEKLY_SCOPE_TYPES or "tydzien" in message_lower or "week" in message_lower:
                return "weekly_summary_analysis"
            return "history_summary"

        if has_app_help:
            return "app_help_only"

        if response_mode == "comparison_plus_guidance":
            return "pattern_analysis"
        if response_mode == "assessment_plus_guidance":
            return "pattern_analysis"
        return "weekly_summary_analysis"

    def _response_blueprint(self, response_shape: str) -> dict[str, Any]:
        if response_shape == "out_of_scope_refusal":
            return {
                "style": "short_refusal_redirect",
                "order": ["boundary", "supported_scope_examples"],
                "maxBullets": 2,
            }

        if response_shape == "follow_up_required":
            return {
                "style": "single_clarifying_question",
                "order": ["why_needed_short", "clarifying_question"],
                "maxSentences": 2,
            }

        if response_shape == "mixed_app_help_and_nutrition":
            return {
                "style": "two_track_answer",
                "order": [
                    "verdict",
                    "coverage_data_quality",
                    "nutrition_observations",
                    "app_help_specifics",
                    "practical_next_step",
                    "optional_focused_follow_up",
                ],
                "maxKeyObservations": 3,
            }

        if response_shape == "app_help_only":
            return {
                "style": "system_specific_explainer",
                "order": [
                    "direct_answer",
                    "how_it_works_backend_steps",
                    "data_sources_scope",
                    "one_practical_tip",
                ],
                "maxBullets": 4,
            }

        if response_shape == "explicit_listing_request":
            return {
                "style": "listing_allowed",
                "order": ["short_context", "requested_listing", "optional_next_step"],
                "maxItems": 10,
            }

        if response_shape in {
            "history_summary",
            "weekly_summary_analysis",
            "pattern_analysis",
            "goal_progress_feedback",
            "mixed_nutrition_and_goal",
            "mixed_weekly_summary_and_goal",
        }:
            return {
                "style": "verdict_first_product_analysis",
                "order": [
                    "verdict",
                    "coverage_data_quality",
                    "key_observations",
                    "practical_next_step",
                    "optional_focused_follow_up",
                ],
                "maxKeyObservations": 3,
                "goalAware": response_shape in {
                    "goal_progress_feedback",
                    "mixed_nutrition_and_goal",
                    "mixed_weekly_summary_and_goal",
                },
                "mustIncludeCoverageEarly": True,
            }

        return {
            "style": "concise_answer",
            "order": ["verdict", "practical_next_step"],
        }

    @staticmethod
    def _is_explicit_listing_request(user_message: str) -> bool:
        lowered = user_message.strip().lower()
        return any(marker in lowered for marker in _EXPLICIT_LISTING_MARKERS)


def _sanitize_provider_grounding(value: Any) -> dict[str, Any]:
    source = _as_string_map(value)
    if source is None:
        return {}

    sanitized: dict[str, Any] = {}
    for section in _GROUNDING_SECTION_ORDER:
        section_value = source.get(section)
        if section_value is None:
            continue
        section_payload = _sanitize_grounding_section(section, section_value)
        if section_payload is not None:
            sanitized[section] = section_payload
    return sanitized


def _sanitize_grounding_section(section: str, value: Any) -> dict[str, Any] | None:
    if section == "planner":
        return _sanitize_planner(value)
    if section == "scope":
        return _sanitize_scope(value)
    if section == "profileSummary":
        return _sanitize_profile_summary(value)
    if section == "goalContext":
        return _sanitize_goal_context(value)
    if section == "nutritionSummary":
        return _sanitize_nutrition_summary(value)
    if section == "comparison":
        return _sanitize_comparison(value)
    if section == "mealLoggingQuality":
        return _sanitize_meal_logging_quality(value)
    if section == "appHelpContext":
        return _sanitize_app_help_context(value)
    if section in {"chatSummary", "threadMemory"}:
        return _sanitize_chat_summary(value)
    if section == "styleProfile":
        return _sanitize_style_profile(value)
    return None


def _sanitize_planner(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(
        sanitized,
        source,
        ("taskType", "responseMode", "followUpQuestion"),
    )
    _copy_bool_fields(sanitized, source, ("needsFollowUp",))
    _copy_string_list_fields(sanitized, source, ("topics", "capabilities"))
    return sanitized


def _sanitize_scope(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("type", "startDate", "endDate", "timezone"))
    _copy_bool_fields(sanitized, source, ("isPartial",))
    return sanitized


def _sanitize_profile_summary(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(
        sanitized,
        source,
        ("goal", "activityLevel", "language", "aiPersona"),
    )
    _copy_string_list_fields(sanitized, source, ("preferences", "allergies"))
    style_profile = _sanitize_style_profile(source.get("styleProfile"))
    if style_profile is not None:
        sanitized["styleProfile"] = style_profile
    return sanitized


def _sanitize_style_profile(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("id", "label"))
    return sanitized


def _sanitize_goal_context(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("goal", "proteinStrategy"))
    _copy_number_fields(sanitized, source, ("calorieTarget",))
    return sanitized


def _sanitize_nutrition_summary(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}

    period = _sanitize_scope(source.get("period"))
    if period is not None:
        sanitized["period"] = period

    logging_coverage = _sanitize_logging_coverage(source.get("loggingCoverage"))
    if logging_coverage is not None:
        sanitized["loggingCoverage"] = logging_coverage

    totals = _sanitize_nutrition_totals(source.get("totals"))
    if totals is not None:
        sanitized["totals"] = totals

    daily_breakdown = _sanitize_daily_breakdown(source.get("dailyBreakdown"))
    if daily_breakdown is not None:
        sanitized["dailyBreakdown"] = daily_breakdown

    signals = _string_list_value(source.get("signals"))
    if signals is not None:
        sanitized["signals"] = signals

    reliability = _sanitize_reliability(source.get("reliability"))
    if reliability is not None:
        sanitized["reliability"] = reliability

    return sanitized


def _sanitize_logging_coverage(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_int_fields(sanitized, source, ("daysInPeriod", "daysWithEntries", "mealCount"))
    _copy_string_fields(sanitized, source, ("coverageLevel",))
    return sanitized


def _sanitize_nutrition_totals(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_number_fields(sanitized, source, _NUTRITION_TOTAL_KEYS)
    return sanitized


def _sanitize_daily_breakdown(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    items: list[dict[str, Any]] = []
    for item in cast(list[object], value):
        source = _as_string_map(item)
        if source is None:
            continue
        sanitized: dict[str, Any] = {}
        _copy_string_fields(sanitized, source, ("date",))
        _copy_int_fields(sanitized, source, ("mealCount",))
        _copy_number_fields(sanitized, source, _NUTRITION_TOTAL_KEYS)
        items.append(sanitized)
    return items


def _sanitize_reliability(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("summaryConfidence", "reason"))
    return sanitized


def _sanitize_meal_logging_quality(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("coverageLevel",))
    _copy_int_fields(sanitized, source, ("daysWithEntries", "missingDays"))
    _copy_bool_fields(sanitized, source, ("canSupportTrendAnalysis",))
    return sanitized


def _sanitize_app_help_context(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("topic",))
    _copy_string_list_fields(sanitized, source, ("answerFacts",))
    return sanitized


def _sanitize_chat_summary(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_string_fields(sanitized, source, ("summary", "source"))
    _copy_string_list_fields(sanitized, source, ("resolvedFacts",))
    _copy_bool_fields(sanitized, source, ("hasSummary",))
    last_turns = _sanitize_last_turns(source.get("lastTurns"))
    if last_turns is not None:
        sanitized["lastTurns"] = last_turns
    return sanitized


def _sanitize_last_turns(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    turns: list[dict[str, str]] = []
    for item in cast(list[object], value):
        source = _as_string_map(item)
        if source is None:
            continue
        turn: dict[str, str] = {}
        role = source.get("role")
        content = source.get("content")
        if isinstance(role, str):
            turn["role"] = role
        if isinstance(content, str):
            turn["content"] = content
        turns.append(turn)
    return turns


def _sanitize_comparison(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}

    current_period = _sanitize_nutrition_summary(source.get("currentPeriod"))
    if current_period is not None:
        sanitized["currentPeriod"] = current_period

    previous_period = _sanitize_nutrition_summary(source.get("previousPeriod"))
    if previous_period is not None:
        sanitized["previousPeriod"] = previous_period

    coverage_guard = _sanitize_coverage_guard(source.get("coverageGuard"))
    if coverage_guard is not None:
        sanitized["coverageGuard"] = coverage_guard

    delta = _sanitize_delta(source.get("delta"))
    if delta is not None:
        sanitized["delta"] = delta

    return sanitized


def _sanitize_coverage_guard(value: Any) -> dict[str, Any] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, Any] = {}
    _copy_bool_fields(sanitized, source, ("comparable",))
    _copy_string_fields(sanitized, source, ("reason",))
    return sanitized


def _sanitize_delta(value: Any) -> dict[str, dict[str, float | None]] | None:
    source = _as_string_map(value)
    if source is None:
        return None
    sanitized: dict[str, dict[str, float | None]] = {}
    for metric_name, metric_value in source.items():
        metric_source = _as_string_map(metric_value)
        if metric_source is None:
            continue
        delta_value: dict[str, float | None] = {}
        absolute = metric_source.get("absolute")
        if isinstance(absolute, int | float) and not isinstance(absolute, bool):
            delta_value["absolute"] = float(absolute)
        percentage = metric_source.get("percentage")
        if percentage is None or (
            isinstance(percentage, int | float) and not isinstance(percentage, bool)
        ):
            delta_value["percentage"] = (
                None if percentage is None else float(percentage)
            )
        sanitized[metric_name] = delta_value
    return sanitized


def _as_string_map(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, Any], value)
    return {key: item for key, item in raw_map.items() if isinstance(key, str)}


def _copy_string_fields(
    target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        value = source.get(field)
        if isinstance(value, str):
            target[field] = value


def _copy_string_list_fields(
    target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        items = _string_list_value(source.get(field))
        if items is not None:
            target[field] = items


def _string_list_value(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item for item in cast(list[object], value) if isinstance(item, str)]


def _copy_bool_fields(
    target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        value = source.get(field)
        if isinstance(value, bool):
            target[field] = value


def _copy_int_fields(
    target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        value = source.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            target[field] = value


def _copy_number_fields(
    target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        value = source.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            target[field] = value
