from __future__ import annotations

import json
from typing import Any, cast

from app.schemas.ai_chat.planner import CapabilityPlanDto, PlannerResultDto

ALLOWED_CAPABILITIES: tuple[str, ...] = (
    "resolve_time_scope",
    "get_profile_summary",
    "get_goal_context",
    "get_nutrition_period_summary",
    "compare_periods",
    "get_meal_logging_quality",
    "get_recent_chat_summary",
    "get_app_help_context",
)


class ChatPlanner:
    def __init__(
        self,
        openai_client: Any,
        *,
        model: str = "gpt-4o-mini",
    ) -> None:
        self.openai_client = openai_client
        self.model = model

    async def plan(
        self,
        *,
        user_id: str,
        user_message: str,
        recent_turns: list[dict[str, str]],
        memory_summary: Any,
        language: str,
    ) -> PlannerResultDto:
        planner_messages = self._build_planner_messages(
            user_message=user_message,
            recent_turns=recent_turns,
            memory_summary=memory_summary,
            language=language,
        )
        raw = await self.openai_client.responses_json(
            model=self.model,
            messages=planner_messages,
            schema=PlannerResultDto,
            temperature=0.0,
        )
        planned = PlannerResultDto.model_validate(self._coerce_raw_payload(raw))
        return self._sanitize_result(planned, language=language)

    def _build_planner_messages(
        self,
        *,
        user_message: str,
        recent_turns: list[dict[str, str]],
        memory_summary: Any,
        language: str,
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": self._build_system_prompt(),
            },
            {
                "role": "developer",
                "content": self._build_planner_context(
                    user_message=user_message,
                    recent_turns=recent_turns,
                    memory_summary=memory_summary,
                    language=language,
                ),
            },
        ]

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are the planning layer for Fitaly AI Chat v2.\n"
            "Return ONLY JSON matching the provided schema.\n"
            "Never answer the user directly.\n"
            "Never execute tools.\n"
            "Your job is capability planning only.\n\n"
            "Planning rules:\n"
            "1. Use only allowed capabilities.\n"
            "2. Support mixed-intent requests by returning multiple capabilities.\n"
            "3. Prefer minimal capability set that can ground the answer.\n"
            "4. Ask follow-up only if time scope or intent is truly ambiguous.\n"
            "5. If query is unrelated to Fitaly app usage, user data, meals, calories, macros, or nutrition,\n"
            "   mark out_of_scope_refusal with no capabilities.\n"
            "6. For app-help-only requests use get_app_help_context.\n"
            "7. For meal history/progress queries usually include resolve_time_scope first, then nutrition tools.\n"
            "8. For goal progress feedback include get_goal_context and profile/nutrition tools as needed.\n"
            "9. For mixed app-help + nutrition include both get_app_help_context and nutrition capabilities.\n"
            "10. Follow-up should be rare and specific."
        )

    def _build_planner_context(
        self,
        *,
        user_message: str,
        recent_turns: list[dict[str, str]],
        memory_summary: Any,
        language: str,
    ) -> str:
        summary_payload = None
        if memory_summary is not None:
            summary_payload = {
                "summary": getattr(memory_summary, "summary", None),
                "resolvedFacts": getattr(memory_summary, "resolved_facts", None),
                "coveredUntilMessageId": getattr(
                    memory_summary, "covered_until_message_id", None
                ),
            }
        context: dict[str, Any] = {
            "language": language if language in {"pl", "en"} else "pl",
            "allowedCapabilities": list(ALLOWED_CAPABILITIES),
            "hints": {
                "meal_history_lookup": [
                    "resolve_time_scope",
                    "get_nutrition_period_summary",
                    "get_meal_logging_quality",
                ],
                "goal_progress_feedback": [
                    "resolve_time_scope",
                    "get_goal_context",
                    "get_profile_summary",
                    "get_nutrition_period_summary",
                    "get_meal_logging_quality",
                ],
                "mixed_history_plus_goal": [
                    "resolve_time_scope",
                    "get_goal_context",
                    "get_nutrition_period_summary",
                    "get_meal_logging_quality",
                ],
                "mixed_app_help_plus_nutrition": [
                    "get_app_help_context",
                    "resolve_time_scope",
                    "get_nutrition_period_summary",
                ],
                "ambiguous_time_scope": ["resolve_time_scope", "follow_up_required"],
                "chat_memory_reference": ["get_recent_chat_summary"],
            },
            "recentTurns": recent_turns,
            "memorySummary": summary_payload,
            "userMessage": user_message,
        }
        return json.dumps(context, ensure_ascii=False)

    def _sanitize_result(
        self, result: PlannerResultDto, *, language: str
    ) -> PlannerResultDto:
        sanitized_capabilities: list[CapabilityPlanDto] = []
        seen: set[str] = set()
        for capability in sorted(result.capabilities, key=lambda item: item.priority):
            if capability.name in seen:
                continue
            if capability.name not in ALLOWED_CAPABILITIES:
                continue
            seen.add(capability.name)
            sanitized_capabilities.append(
                CapabilityPlanDto(
                    name=capability.name,
                    priority=len(sanitized_capabilities) + 1,
                    args=capability.args if isinstance(capability.args, dict) else {},
                )
            )

        needs_follow_up = bool(result.needs_follow_up)
        follow_up_question = (
            result.follow_up_question.strip()
            if isinstance(result.follow_up_question, str)
            and result.follow_up_question.strip()
            else None
        )

        if result.task_type == "out_of_scope_refusal":
            sanitized_capabilities = []
            needs_follow_up = False
            follow_up_question = None
            response_mode = "refusal_redirect"
        else:
            response_mode = result.response_mode

        if result.task_type == "follow_up_required":
            needs_follow_up = True
            if follow_up_question is None:
                follow_up_question = self._default_follow_up_question(language=language)

        if not needs_follow_up:
            follow_up_question = None

        if result.task_type == "app_help_only" and not sanitized_capabilities:
            sanitized_capabilities = [
                CapabilityPlanDto(
                    name="get_app_help_context",
                    priority=1,
                    args={"topic": "default"},
                )
            ]

        sanitized_data = result.model_dump(by_alias=True)
        sanitized_data["capabilities"] = [
            capability.model_dump() for capability in sanitized_capabilities
        ]
        sanitized_data["responseMode"] = response_mode
        sanitized_data["needsFollowUp"] = needs_follow_up
        sanitized_data["followUpQuestion"] = follow_up_question
        if needs_follow_up and sanitized_data["taskType"] != "follow_up_required":
            sanitized_data["taskType"] = "follow_up_required"
        if sanitized_data["taskType"] == "out_of_scope_refusal":
            sanitized_data["capabilities"] = []
        return PlannerResultDto.model_validate(sanitized_data)

    @staticmethod
    def _coerce_raw_payload(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        payload = cast(dict[str, Any], dict(cast(dict[object, object], raw)))
        raw_capabilities = payload.get("capabilities")
        if not isinstance(raw_capabilities, list):
            return payload

        filtered_capabilities: list[dict[str, Any]] = []
        for item in cast(list[object], raw_capabilities):
            if not isinstance(item, dict):
                continue
            item_map = cast(dict[str, Any], item)
            name = item_map.get("name")
            if name not in ALLOWED_CAPABILITIES:
                continue
            priority = item_map.get("priority")
            if isinstance(priority, bool):
                continue
            if not isinstance(priority, int):
                try:
                    if priority is None:
                        continue
                    priority = int(cast(str | float, priority))
                except (TypeError, ValueError):
                    continue
            args = item_map.get("args")
            filtered_capabilities.append(
                {
                    "name": name,
                    "priority": priority,
                    "args": args if isinstance(args, dict) else {},
                }
            )
        payload["capabilities"] = filtered_capabilities
        return payload

    @staticmethod
    def _default_follow_up_question(*, language: str) -> str:
        if language == "en":
            return "Which exact time range should I analyze?"
        return "Jaki dokladnie zakres czasu mam przeanalizowac?"
