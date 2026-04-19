from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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

    def resolve_tool_args(self, *, raw_args: dict, tool_outputs: dict) -> dict:
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

    def _resolve_tool_reference(self, *, reference: str, tool_outputs: dict) -> Any:
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
                current = current[chunk]
                continue
            if isinstance(current, list) and chunk.isdigit():
                index = int(chunk)
                if index >= len(current):
                    raise ValueError(f"Tool reference index out of range: {reference}")
                current = current[index]
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
    ) -> dict:
        bounded_turns = self._bound_recent_turns(recent_turns)
        grounding = {
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
                "resolvedFacts": (
                    list(getattr(memory_summary, "resolved_facts", []))
                    if memory_summary is not None
                    else []
                ),
                "summary": (
                    getattr(memory_summary, "summary", None)
                    if memory_summary is not None
                    else None
                ),
            },
        }
        self._trim_low_value_context(grounding)
        return grounding

    def _planner_to_grounding(self, planner_result: Any) -> dict[str, Any]:
        if planner_result is None:
            return {}
        try:
            capabilities = [
                capability.name for capability in getattr(planner_result, "capabilities", [])
            ]
            return {
                "taskType": getattr(planner_result, "task_type", None),
                "responseMode": getattr(planner_result, "response_mode", None),
                "needsFollowUp": getattr(planner_result, "needs_follow_up", False),
                "followUpQuestion": getattr(planner_result, "follow_up_question", None),
                "topics": list(
                    getattr(
                        getattr(planner_result, "query_understanding", None), "topics", []
                    )
                ),
                "capabilities": capabilities,
            }
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
            coverage = (
                nutrition.get("loggingCoverage", {})
                if isinstance(nutrition.get("loggingCoverage"), dict)
                else {}
            )
            coverage_level = coverage.get("coverageLevel")
            if coverage_level in {"none", "low"}:
                nutrition.pop("dailyBreakdown", None)

        app_help = grounding.get("appHelpContext")
        if isinstance(app_help, dict):
            facts = app_help.get("answerFacts")
            if isinstance(facts, list) and len(facts) > 5:
                app_help["answerFacts"] = facts[:5]

        chat_summary = grounding.get("chatSummary")
        if isinstance(chat_summary, dict):
            last_turns = chat_summary.get("lastTurns")
            if isinstance(last_turns, list) and len(last_turns) > 4:
                chat_summary["lastTurns"] = last_turns[-4:]

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

        grounding = developer_payload.get("grounding")
        if not isinstance(grounding, dict):
            return (
                working_messages,
                BudgetResult(
                    used_summary=used_summary,
                    truncated=True,
                    history_turns=0,
                ),
            )

        while total_tokens > self.soft_token_limit:
            changed = self._trim_for_budget(grounding, prefer_summary=used_summary)
            if not changed:
                break
            truncated = True
            working_messages[developer_idx]["content"] = json.dumps(
                developer_payload, ensure_ascii=False
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
            last_turns = thread_memory.get("lastTurns")
            if isinstance(last_turns, list) and len(last_turns) > (0 if prefer_summary else 1):
                thread_memory["lastTurns"] = last_turns[1:]
                return True

        nutrition = grounding.get("nutritionSummary")
        if isinstance(nutrition, dict):
            daily_breakdown = nutrition.get("dailyBreakdown")
            if isinstance(daily_breakdown, list) and len(daily_breakdown) > 3:
                nutrition["dailyBreakdown"] = daily_breakdown[:3]
                return True
            if isinstance(daily_breakdown, list) and daily_breakdown:
                nutrition["dailyBreakdown"] = []
                return True

        app_help = grounding.get("appHelpContext")
        if isinstance(app_help, dict):
            facts = app_help.get("answerFacts")
            if isinstance(facts, list) and len(facts) > 2:
                app_help["answerFacts"] = facts[:2]
                return True

        comparison = grounding.get("comparison")
        if isinstance(comparison, dict) and "delta" in comparison:
            comparison.pop("delta", None)
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
        grounding = payload.get("grounding")
        if not isinstance(grounding, dict):
            return 0
        thread_memory = grounding.get("threadMemory")
        if not isinstance(thread_memory, dict):
            return 0
        last_turns = thread_memory.get("lastTurns")
        if not isinstance(last_turns, list):
            return 0
        return len(last_turns)
