"""Prompt budgeting utilities for backend-owned AI chat context."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, TypedDict

SOFT_PROMPT_TOKEN_LIMIT = 2_200
HARD_PROMPT_TOKEN_LIMIT = 2_800
MAX_HISTORY_LINES = 12
MAX_USER_MESSAGE_CHARS = 2_200


class TokenBudgetResult(TypedDict):
    prompt: str
    used_summary: bool
    history_turns: int
    truncated: bool
    estimated_prompt_tokens: int
    generated_summary: str | None


def estimate_tokens(text: str) -> int:
    normalized = text.strip()
    if not normalized:
        return 1
    return max(1, math.ceil(len(normalized) / 4))


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return 0.0
        try:
            return float(candidate)
        except ValueError:
            return 0.0
    return 0.0


def _clip(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    if limit <= 1:
        return normalized[:limit]
    return f"{normalized[: limit - 1].rstrip()}…"


def _build_history_lines(history_messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in history_messages[-MAX_HISTORY_LINES:]:
        role = _as_text(item.get("role")) or "assistant"
        if role not in {"assistant", "user", "system"}:
            role = "assistant"
        content = _as_text(item.get("content"))
        if not content:
            continue
        lines.append(f"{role}: {_clip(content, 240)}")
    return lines


def _summarize_dropped_lines(lines: list[str]) -> str | None:
    if not lines:
        return None
    preview = " | ".join(_clip(line, 80) for line in lines[-6:])
    clipped = _clip(preview, 420)
    return clipped or None


def _compact_profile(profile: dict[str, Any], language: str) -> str:
    parts: list[str] = []
    for key in (
        "goal",
        "activityLevel",
        "preferences",
        "allergies",
        "chronicDiseases",
        "aiStyle",
        "aiFocus",
        "calorieTarget",
    ):
        value = profile.get(key)
        if isinstance(value, list):
            normalized = [str(item).strip() for item in value if str(item).strip()]
            if normalized:
                parts.append(f"{key}={','.join(normalized[:8])}")
            continue
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            parts.append(f"{key}={_clip(normalized, 80)}")
    parts.append(f"language={language}")
    return "; ".join(parts) if parts else "none"


def _compact_meals(meals: list[dict[str, Any]]) -> str:
    if not meals:
        return (
            "count=0; today_count=0; "
            "today_totals=kcal:0,p:0,f:0,c:0; items=none"
        )

    def _resolve_day_key(meal: dict[str, Any]) -> str:
        for key in ("dayKey", "timestamp", "createdAt", "updatedAt"):
            raw = _as_text(meal.get(key))
            if len(raw) >= 10:
                return raw[:10]
        return "unknown"

    def _resolve_meal_totals(meal: dict[str, Any]) -> tuple[float, float, float, float]:
        totals = meal.get("totals")
        totals_map = totals if isinstance(totals, dict) else {}
        kcal = _as_number(totals_map.get("kcal")) or _as_number(meal.get("kcal"))
        protein = _as_number(totals_map.get("protein")) or _as_number(meal.get("protein"))
        fat = _as_number(totals_map.get("fat")) or _as_number(meal.get("fat"))
        carbs = _as_number(totals_map.get("carbs")) or _as_number(meal.get("carbs"))
        return kcal, protein, fat, carbs

    def _fmt(number: float) -> str:
        rounded = round(number, 1)
        if abs(rounded - round(rounded)) < 0.05:
            return str(int(round(rounded)))
        return f"{rounded:.1f}"

    today_key = datetime.now(timezone.utc).date().isoformat()
    today_count = 0
    today_totals = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    items: list[str] = []

    for meal in meals[:8]:
        day_key = _resolve_day_key(meal)
        name = _as_text(meal.get("name") or meal.get("type")) or "meal"
        kcal, protein, fat, carbs = _resolve_meal_totals(meal)
        if day_key == today_key:
            today_count += 1
            today_totals["kcal"] += kcal
            today_totals["protein"] += protein
            today_totals["fat"] += fat
            today_totals["carbs"] += carbs
        items.append(
            (
                f"{day_key}:{_clip(name, 34)}"
                f"(kcal={_fmt(kcal)},p={_fmt(protein)},f={_fmt(fat)},c={_fmt(carbs)})"
            )
        )

    items_line = _clip(" | ".join(items), 900) if items else "none"
    return (
        f"count={len(meals)}; today={today_key}; today_count={today_count}; "
        f"today_totals=kcal:{_fmt(today_totals['kcal'])},"
        f"p:{_fmt(today_totals['protein'])},"
        f"f:{_fmt(today_totals['fat'])},"
        f"c:{_fmt(today_totals['carbs'])}; "
        f"items={items_line}"
    )


def _base_policy(language: str) -> str:
    return "\n".join(
        [
            "You are the Fitaly nutrition assistant.",
            f"Reply in {language}.",
            "Use backend-provided Fitaly context (PROFILE, MEALS_CONTEXT, SUMMARY, HISTORY) as your source of truth.",
            "If MEALS_CONTEXT has logged entries, do not claim you lack access to the user's meal history.",
            "If there are no logged meals in the requested period, state that clearly and ask one focused follow-up question.",
            (
                "Allowed topics only: (1) Fitaly app usage/features/settings, "
                "(2) the user's own Fitaly data such as meal history/statistics/preferences, "
                "(3) food, nutrition, meals, calories, macros, and healthy eating."
            ),
            (
                "If the user asks outside those topics, refuse briefly and redirect "
                "to one of the allowed topic groups."
            ),
            "Never provide medical diagnosis or treatment.",
            "Keep advice practical and concise.",
        ]
    )


def build_budgeted_prompt(
    *,
    user_message: str,
    language: str,
    profile: dict[str, Any],
    meals: list[dict[str, Any]],
    history_messages: list[dict[str, Any]],
    memory_summary: str | None,
) -> TokenBudgetResult:
    normalized_user_message = _clip(user_message, MAX_USER_MESSAGE_CHARS)

    history_lines = _build_history_lines(history_messages)
    dropped_lines: list[str] = []
    truncated = False
    used_summary = bool(memory_summary and memory_summary.strip())
    generated_summary: str | None = None

    def compose_prompt(summary_line: str | None) -> str:
        sections: list[str] = [
            _base_policy(language),
            f"PROFILE={_compact_profile(profile, language)}",
            f"MEALS_CONTEXT={_compact_meals(meals)}",
        ]
        if summary_line:
            sections.append(f"SUMMARY={_clip(summary_line, 800)}")
        sections.append(f"HISTORY={' | '.join(history_lines) if history_lines else 'none'}")
        sections.append(f"USER_MESSAGE={normalized_user_message}")
        return "\n".join(sections)

    effective_summary = _as_text(memory_summary) or None
    prompt = compose_prompt(effective_summary)
    estimated = estimate_tokens(prompt)

    while estimated > SOFT_PROMPT_TOKEN_LIMIT and history_lines:
        dropped_lines.append(history_lines.pop(0))
        truncated = True
        prompt = compose_prompt(effective_summary)
        estimated = estimate_tokens(prompt)

    if dropped_lines:
        generated_summary = _summarize_dropped_lines(dropped_lines)
        if generated_summary:
            if effective_summary:
                effective_summary = _clip(
                    f"{effective_summary} | {generated_summary}",
                    800,
                )
            else:
                effective_summary = generated_summary
            used_summary = True
            prompt = compose_prompt(effective_summary)
            estimated = estimate_tokens(prompt)

    if estimated > HARD_PROMPT_TOKEN_LIMIT:
        clipped_message = _clip(normalized_user_message, 1_200)
        prompt = prompt.replace(
            f"USER_MESSAGE={normalized_user_message}",
            f"USER_MESSAGE={clipped_message}",
        )
        truncated = True
        estimated = estimate_tokens(prompt)

    return {
        "prompt": prompt,
        "used_summary": used_summary,
        "history_turns": len(history_lines),
        "truncated": truncated,
        "estimated_prompt_tokens": estimated,
        "generated_summary": generated_summary,
    }
