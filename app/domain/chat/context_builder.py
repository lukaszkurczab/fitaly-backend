from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from app.core.token_counter import TokenCounter, TokenStats


@dataclass(frozen=True)
class BudgetResult:
    used_summary: bool
    truncated: bool
    history_turns: int


class ContextBuilder:
    def __init__(
        self,
        *,
        token_counter: TokenCounter | None = None,
        max_recent_turns: int = 6,
        soft_token_limit: int = 2200,
        hard_token_limit: int = 2800,
    ) -> None:
        self.token_counter = token_counter or TokenCounter()
        self.max_recent_turns = max_recent_turns
        self.soft_token_limit = soft_token_limit
        self.hard_token_limit = hard_token_limit

    def resolve_tool_args(
        self, *, raw_args: dict[str, Any], tool_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in raw_args.items():
            if isinstance(value, str) and value.startswith("$tool."):
                resolved[key] = self._resolve_tool_reference(
                    reference=value,
                    tool_outputs=tool_outputs,
                )
            else:
                resolved[key] = value
        return resolved

    def _resolve_tool_reference(
        self, *, reference: str, tool_outputs: dict[str, Any]
    ) -> Any:
        prefix = "$tool."
        if not reference.startswith(prefix):
            return reference
        path = reference[len(prefix) :]
        if "." not in path:
            raise ValueError(f"Invalid tool reference: {reference}")
        tool_name, field_path = path.split(".", 1)
        if tool_name not in tool_outputs:
            raise ValueError(f"Tool output not found: {tool_name}")
        current: Any = tool_outputs[tool_name]
        for chunk in field_path.split("."):
            if isinstance(current, dict) and chunk in current:
                current_map = cast(dict[str, Any], current)
                current = current_map[chunk]
                continue
            if isinstance(current, list) and chunk.isdigit():
                current_list = cast(list[object], current)
                index = int(chunk)
                if index >= len(current_list):
                    raise ValueError(f"Tool reference index out of range: {reference}")
                current = current_list[index]
                continue
            raise ValueError(f"Tool reference path not found: {reference}")
        return current

    def build_grounding(
        self,
        *,
        planner_result: Any,
        tool_outputs: dict[str, Any],
        recent_turns: list[dict[str, str]],
        memory_summary: Any,
    ) -> dict[str, Any]:
        bounded_turns = self._bound_recent_turns(recent_turns)
        resolved_facts: list[str] = []
        summary_text: str | None = None
        if memory_summary is not None:
            raw_facts = getattr(memory_summary, "resolved_facts", [])
            if isinstance(raw_facts, list):
                facts_list = cast(list[object], raw_facts)
                resolved_facts = [str(item).strip() for item in facts_list if str(item).strip()]
            raw_summary = getattr(memory_summary, "summary", None)
            if isinstance(raw_summary, str) and raw_summary.strip():
                summary_text = raw_summary

        grounding: dict[str, Any] = {
            "planner": self._planner_to_grounding(planner_result),
            "scope": tool_outputs.get("resolve_time_scope"),
            "profileSummary": tool_outputs.get("get_profile_summary"),
            "goalContext": tool_outputs.get("get_goal_context"),
            "nutritionSummary": tool_outputs.get("get_nutrition_period_summary"),
            "comparison": tool_outputs.get("compare_periods"),
            "mealLoggingQuality": tool_outputs.get("get_meal_logging_quality"),
            "appHelpContext": tool_outputs.get("get_app_help_context"),
            "chatSummary": tool_outputs.get("get_recent_chat_summary"),
            "threadMemory": {
                "lastTurns": bounded_turns,
                "resolvedFacts": resolved_facts,
                "summary": summary_text,
            },
        }
        profile_summary = grounding.get("profileSummary")
        if isinstance(profile_summary, dict):
            profile_map = cast(dict[str, Any], profile_summary)
            style_profile = profile_map.get("styleProfile")
            if isinstance(style_profile, dict):
                grounding["styleProfile"] = style_profile
        self._trim_low_value_context(grounding)
        return grounding

    def _planner_to_grounding(self, planner_result: Any) -> dict[str, Any]:
        if planner_result is None:
            return {}
        try:
            capabilities_raw = getattr(planner_result, "capabilities", [])
            capabilities: list[str] = []
            if isinstance(capabilities_raw, list):
                for capability in cast(list[object], capabilities_raw):
                    name = getattr(capability, "name", None)
                    if isinstance(name, str) and name.strip():
                        capabilities.append(name.strip())

            query_understanding = getattr(planner_result, "query_understanding", None)
            topics_raw = getattr(query_understanding, "topics", [])
            topics: list[str] = []
            if isinstance(topics_raw, list):
                for topic in cast(list[object], topics_raw):
                    if isinstance(topic, str) and topic.strip():
                        topics.append(topic.strip())

            planner_payload: dict[str, Any] = {
                "taskType": getattr(planner_result, "task_type", None),
                "responseMode": getattr(planner_result, "response_mode", None),
                "needsFollowUp": getattr(planner_result, "needs_follow_up", False),
                "followUpQuestion": getattr(planner_result, "follow_up_question", None),
                "topics": topics,
                "capabilities": capabilities,
            }
            return planner_payload
        except Exception:  # noqa: BLE001
            return {}

    def _bound_recent_turns(self, recent_turns: list[dict[str, str]]) -> list[dict[str, str]]:
        bounded = recent_turns[-self.max_recent_turns :]
        normalized: list[dict[str, str]] = []
        for turn in bounded:
            role = str(turn.get("role") or "assistant").strip().lower()
            if role not in {"user", "assistant", "system"}:
                role = "assistant"
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            if len(content) > 320:
                content = f"{content[:319].rstrip()}…"
            normalized.append({"role": role, "content": content})
        return normalized

    def _trim_low_value_context(self, grounding: dict[str, Any]) -> None:
        nutrition = grounding.get("nutritionSummary")
        if isinstance(nutrition, dict):
            nutrition_map = cast(dict[str, Any], nutrition)
            raw_coverage = nutrition_map.get("loggingCoverage")
            coverage = (
                cast(dict[str, Any], raw_coverage)
                if isinstance(raw_coverage, dict)
                else {}
            )
            coverage_level = coverage.get("coverageLevel")
            if coverage_level in {"none", "low"}:
                nutrition_map.pop("dailyBreakdown", None)

        app_help = grounding.get("appHelpContext")
        if isinstance(app_help, dict):
            app_help_map = cast(dict[str, Any], app_help)
            facts = app_help_map.get("answerFacts")
            if isinstance(facts, list):
                facts_list = cast(list[object], facts)
                if len(facts_list) > 5:
                    app_help_map["answerFacts"] = facts_list[:5]

        chat_summary = grounding.get("chatSummary")
        if isinstance(chat_summary, dict):
            chat_summary_map = cast(dict[str, Any], chat_summary)
            last_turns = chat_summary_map.get("lastTurns")
            if isinstance(last_turns, list):
                turns_list = cast(list[object], last_turns)
                if len(turns_list) > 4:
                    chat_summary_map["lastTurns"] = turns_list[-4:]

    def enforce_token_budget(
        self,
        *,
        messages: list[dict[str, str]],
        token_stats: TokenStats,
        memory_summary: Any,
    ) -> tuple[list[dict[str, str]], BudgetResult]:
        working_messages = [dict(item) for item in messages]
        total_tokens = token_stats.total_tokens
        used_summary = memory_summary is not None and bool(
            str(getattr(memory_summary, "summary", "")).strip()
        )
        truncated = False

        if total_tokens <= self.soft_token_limit:
            history_turns = self._extract_history_turn_count(working_messages)
            return (
                working_messages,
                BudgetResult(
                    used_summary=used_summary,
                    truncated=False,
                    history_turns=history_turns,
                ),
            )

        developer_idx = self._find_developer_message_index(working_messages)
        if developer_idx is None:
            return (
                working_messages,
                BudgetResult(
                    used_summary=used_summary,
                    truncated=True,
                    history_turns=0,
                ),
            )

        developer_content = working_messages[developer_idx].get("content", "")
        try:
            developer_payload = json.loads(developer_content)
        except json.JSONDecodeError:
            return (
                working_messages,
                BudgetResult(
                    used_summary=used_summary,
                    truncated=True,
                    history_turns=0,
                ),
            )

        developer_payload_map = cast(dict[str, Any], developer_payload)
        grounding = developer_payload_map.get("grounding")
        if not isinstance(grounding, dict):
            return (
                working_messages,
                BudgetResult(
                    used_summary=used_summary,
                    truncated=True,
                    history_turns=0,
                ),
            )

        grounding_map = cast(dict[str, Any], grounding)
        while total_tokens > self.soft_token_limit:
            changed = self._trim_for_budget(grounding_map, prefer_summary=used_summary)
            if not changed:
                break
            truncated = True
            working_messages[developer_idx]["content"] = json.dumps(
                developer_payload_map, ensure_ascii=False
            )
            total_tokens = self.token_counter.measure_messages(
                working_messages
            ).total_tokens

        if total_tokens > self.hard_token_limit:
            user_idx = self._find_user_message_index(working_messages)
            if user_idx is not None:
                user_content = str(working_messages[user_idx].get("content") or "")
                if len(user_content) > 1200:
                    working_messages[user_idx]["content"] = (
                        f"{user_content[:1199].rstrip()}…"
                    )
                    truncated = True

        history_turns = self._extract_history_turn_count(working_messages)
        return (
            working_messages,
            BudgetResult(
                used_summary=used_summary,
                truncated=truncated,
                history_turns=history_turns,
            ),
        )

    @staticmethod
    def _find_developer_message_index(messages: list[dict[str, str]]) -> int | None:
        for index, message in enumerate(messages):
            if message.get("role") == "developer":
                return index
        return None

    @staticmethod
    def _find_user_message_index(messages: list[dict[str, str]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index
        return None

    @staticmethod
    def _trim_for_budget(grounding: dict[str, Any], *, prefer_summary: bool) -> bool:
        thread_memory = grounding.get("threadMemory")
        if isinstance(thread_memory, dict):
            thread_memory_map = cast(dict[str, Any], thread_memory)
            last_turns = thread_memory_map.get("lastTurns")
            if isinstance(last_turns, list):
                turns_list = cast(list[object], last_turns)
                if len(turns_list) > (0 if prefer_summary else 1):
                    thread_memory_map["lastTurns"] = turns_list[1:]
                    return True

        nutrition = grounding.get("nutritionSummary")
        if isinstance(nutrition, dict):
            nutrition_map = cast(dict[str, Any], nutrition)
            daily_breakdown = nutrition_map.get("dailyBreakdown")
            if isinstance(daily_breakdown, list):
                breakdown_list = cast(list[object], daily_breakdown)
                if len(breakdown_list) > 3:
                    nutrition_map["dailyBreakdown"] = breakdown_list[:3]
                    return True
                if breakdown_list:
                    nutrition_map["dailyBreakdown"] = []
                    return True

        app_help = grounding.get("appHelpContext")
        if isinstance(app_help, dict):
            app_help_map = cast(dict[str, Any], app_help)
            facts = app_help_map.get("answerFacts")
            if isinstance(facts, list):
                facts_list = cast(list[object], facts)
                if len(facts_list) > 2:
                    app_help_map["answerFacts"] = facts_list[:2]
                    return True

        comparison = grounding.get("comparison")
        if isinstance(comparison, dict) and "delta" in comparison:
            comparison_map = cast(dict[str, Any], comparison)
            comparison_map.pop("delta", None)
            return True

        return False

    def _extract_history_turn_count(self, messages: list[dict[str, str]]) -> int:
        developer_idx = self._find_developer_message_index(messages)
        if developer_idx is None:
            return 0
        try:
            payload = json.loads(messages[developer_idx].get("content", ""))
        except json.JSONDecodeError:
            return 0
        if not isinstance(payload, dict):
            return 0
        payload_map = cast(dict[str, Any], payload)
        grounding = payload_map.get("grounding")
        if not isinstance(grounding, dict):
            return 0
        grounding_map = cast(dict[str, Any], grounding)
        thread_memory = grounding_map.get("threadMemory")
        if not isinstance(thread_memory, dict):
            return 0
        thread_memory_map = cast(dict[str, Any], thread_memory)
        last_turns = thread_memory_map.get("lastTurns")
        if not isinstance(last_turns, list):
            return 0
        return len(cast(list[object], last_turns))
