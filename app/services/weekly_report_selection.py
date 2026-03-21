from __future__ import annotations

from dataclasses import dataclass

from app.schemas.weekly_reports import (
    WeeklyReportInsight,
    WeeklyReportPriority,
)
from app.services.weekly_report_signals import WeeklySignals

MAX_WEEKLY_REPORT_INSIGHTS = 3
MAX_WEEKLY_REPORT_PRIORITIES = 2


@dataclass(frozen=True)
class WeeklyReportContent:
    summary: str
    insights: list[WeeklyReportInsight]
    priorities: list[WeeklyReportPriority]


@dataclass(frozen=True)
class _InsightCandidate:
    score: int
    tone_rank: int
    insight: WeeklyReportInsight


@dataclass(frozen=True)
class _PriorityCandidate:
    score: int
    priority: WeeklyReportPriority


def build_weekly_report_content(signals: WeeklySignals) -> WeeklyReportContent:
    insight_candidates = _build_insight_candidates(signals)
    insights = _select_insights(insight_candidates)
    priorities = _select_priorities(insights)
    summary = _build_summary(insights, priorities)
    return WeeklyReportContent(
        summary=summary,
        insights=insights,
        priorities=priorities,
    )


def _build_insight_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    candidates = [
        *_consistency_candidates(signals),
        *_logging_coverage_candidates(signals),
        *_start_of_day_candidates(signals),
        *_day_completion_candidates(signals),
        *_weekend_drift_candidates(signals),
        *_improvement_candidates(signals),
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.tone_rank,
            candidate.insight.type,
            candidate.insight.title,
        ),
    )


def _consistency_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.consistency
    if signal.level == "strong":
        return [
            _candidate(
                score=92,
                tone_rank=0,
                type="consistency",
                importance="high",
                tone="positive",
                title="You stayed consistent on most days",
                body=(
                    f"You had valid logging on {signal.valid_logged_days} of 7 days, "
                    "which made the week readable."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    if signal.level == "mixed":
        return [
            _candidate(
                score=68,
                tone_rank=2,
                type="consistency",
                importance="medium",
                tone="neutral",
                title="The week had some rhythm, but not enough yet",
                body=(
                    f"You had valid logging on {signal.valid_logged_days} of 7 days, "
                    "so patterns were only partly visible."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=88,
            tone_rank=1,
            type="consistency",
            importance="high",
            tone="negative",
            title="The week was too patchy to read cleanly",
            body=(
                f"You had valid logging on {signal.valid_logged_days} of 7 days, "
                "so weekly patterns were thin."
            ),
            reason_codes=list(signal.reason_codes),
        )
    ]


def _logging_coverage_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.logging_coverage
    if not _should_surface_logging_coverage(signals):
        return []
    if signal.level == "high":
        return [
            _candidate(
                score=82,
                tone_rank=0,
                type="logging_coverage",
                importance="medium",
                tone="positive",
                title="You gave the week strong logging coverage",
                body=(
                    f"{signal.valid_logged_days} days had enough detail to use in the report."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    if signal.level == "medium":
        return [
            _candidate(
                score=70,
                tone_rank=2,
                type="logging_coverage",
                importance="medium",
                tone="neutral",
                title="Coverage was usable, but still a bit thin",
                body=(
                    f"{signal.valid_logged_days} days were valid, "
                    f"with {signal.unknown_detail_days} lower-detail day(s)."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=94,
            tone_rank=1,
            type="logging_coverage",
            importance="high",
            tone="negative",
            title="Logging coverage was the biggest gap this week",
            body=(
                f"Only {signal.valid_logged_days} day(s) had enough detail to use confidently."
            ),
            reason_codes=list(signal.reason_codes),
        )
    ]


def _should_surface_logging_coverage(signals: WeeklySignals) -> bool:
    signal = signals.logging_coverage
    return signal.unknown_detail_days > 0 or signal.logged_days > signal.valid_logged_days


def _start_of_day_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.start_of_day_stability
    if not signal.available:
        return []
    if signal.level == "stable":
        return [
            _candidate(
                score=74,
                tone_rank=0,
                type="start_of_day_pattern",
                importance="medium",
                tone="positive",
                title="Your first meal timing stayed fairly stable",
                body=(
                    f"Your first log clustered around {signal.median_hour:.2f}h, "
                    "which made the start of the day predictable."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    if signal.level == "variable":
        return [
            _candidate(
                score=62,
                tone_rank=2,
                type="start_of_day_pattern",
                importance="medium",
                tone="neutral",
                title="Your first meal timing moved around",
                body="The start of the day was visible, but not yet anchored to a steady window.",
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=78,
            tone_rank=1,
            type="start_of_day_pattern",
            importance="medium",
            tone="negative",
            title="The start of the day lacked a clear pattern",
            body="Your first log shifted a lot across the week, so mornings looked inconsistent.",
            reason_codes=list(signal.reason_codes),
        )
    ]


def _day_completion_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.day_completion_tendency
    if not signal.available:
        return []
    if signal.level == "consistent":
        return [
            _candidate(
                score=84,
                tone_rank=0,
                type="day_completion_pattern",
                importance="high",
                tone="positive",
                title="You closed most logged days well",
                body=(
                    f"{signal.complete_days} of {signal.observed_days} valid days looked complete enough "
                    "to trust."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    if signal.level == "mixed":
        return [
            _candidate(
                score=72,
                tone_rank=2,
                type="day_completion_pattern",
                importance="medium",
                tone="neutral",
                title="Some days were complete, but not enough of them",
                body=(
                    f"Only {signal.complete_days} of {signal.observed_days} valid days looked complete."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=86,
            tone_rank=1,
            type="day_completion_pattern",
            importance="high",
            tone="negative",
            title="Too many days stopped before they looked complete",
            body=(
                f"Only {signal.complete_days} of {signal.observed_days} valid days looked complete."
            ),
            reason_codes=list(signal.reason_codes),
        )
    ]


def _weekend_drift_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.weekend_drift
    if not signal.available or signal.pattern == "none":
        return []
    if signal.pattern == "weekend_drop":
        return [
            _candidate(
                score=80,
                tone_rank=1,
                type="weekend_drift",
                importance="medium",
                tone="negative",
                title="Weekends broke the pattern from the rest of the week",
                body="Saturday and Sunday were lighter than weekdays, which disrupted the weekly rhythm.",
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=66,
            tone_rank=0,
            type="weekend_drift",
            importance="low",
            tone="positive",
            title="Your weekends were stronger than your weekdays",
            body="Weekend logging held up better than weekday logging.",
            reason_codes=list(signal.reason_codes),
        )
    ]


def _improvement_candidates(signals: WeeklySignals) -> list[_InsightCandidate]:
    signal = signals.improving_vs_previous_week
    if not signal.available or signal.direction == "stable":
        return []
    if signal.direction == "improving":
        return [
            _candidate(
                score=76,
                tone_rank=0,
                type="improving_trend",
                importance="medium",
                tone="positive",
                title="This week was stronger than the previous one",
                body=(
                    f"You moved from {signal.previous_valid_logged_days} to "
                    f"{signal.current_valid_logged_days} valid logged days."
                ),
                reason_codes=list(signal.reason_codes),
            )
        ]
    return [
        _candidate(
            score=82,
            tone_rank=1,
            type="improving_trend",
            importance="medium",
            tone="negative",
            title="This week slipped versus the previous one",
            body=(
                f"You moved from {signal.previous_valid_logged_days} to "
                f"{signal.current_valid_logged_days} valid logged days."
            ),
            reason_codes=list(signal.reason_codes),
        )
    ]


def _candidate(
    *,
    score: int,
    tone_rank: int,
    type: str,
    importance: str,
    tone: str,
    title: str,
    body: str,
    reason_codes: list[str],
) -> _InsightCandidate:
    return _InsightCandidate(
        score=score,
        tone_rank=tone_rank,
        insight=WeeklyReportInsight(
            type=type,
            importance=importance,
            tone=tone,
            title=title,
            body=body,
            reasonCodes=reason_codes,
        ),
    )


def _select_insights(candidates: list[_InsightCandidate]) -> list[WeeklyReportInsight]:
    positives = [candidate for candidate in candidates if candidate.insight.tone == "positive"]
    negatives = [candidate for candidate in candidates if candidate.insight.tone == "negative"]
    actionables = [
        candidate
        for candidate in candidates
        if candidate.insight.tone != "positive"
        and candidate.insight.type != "improving_trend"
    ]
    trends = [candidate for candidate in candidates if candidate.insight.type == "improving_trend"]

    selected: list[_InsightCandidate] = []
    used_types: set[str] = set()

    def add_candidate(candidate: _InsightCandidate | None) -> None:
        if candidate is None:
            return
        if candidate.insight.type in used_types:
            return
        selected.append(candidate)
        used_types.add(candidate.insight.type)

    add_candidate(positives[0] if positives else None)
    add_candidate(negatives[0] if negatives else None)
    add_candidate(
        next(
            (
                candidate
                for candidate in actionables
                if candidate.insight.type not in used_types
            ),
            None,
        )
    )
    add_candidate(trends[0] if trends else None)

    for candidate in candidates:
        if len(selected) >= MAX_WEEKLY_REPORT_INSIGHTS:
            break
        add_candidate(candidate)

    return [candidate.insight for candidate in selected]


def _select_priorities(insights: list[WeeklyReportInsight]) -> list[WeeklyReportPriority]:
    candidates: list[_PriorityCandidate] = []
    for insight in insights:
        candidates.extend(_priority_candidates_for_insight(insight))

    if not candidates:
        return []

    selected_by_type: dict[str, _PriorityCandidate] = {}
    for candidate in sorted(
        candidates,
        key=lambda candidate: (-candidate.score, candidate.priority.type, candidate.priority.text),
    ):
        selected_by_type.setdefault(candidate.priority.type, candidate)

    ordered = sorted(
        selected_by_type.values(),
        key=lambda candidate: (-candidate.score, candidate.priority.type, candidate.priority.text),
    )
    return [candidate.priority for candidate in ordered[:MAX_WEEKLY_REPORT_PRIORITIES]]


def _priority_candidates_for_insight(
    insight: WeeklyReportInsight,
) -> list[_PriorityCandidate]:
    if insight.type == "consistency" and insight.tone == "positive":
        return [
            _PriorityCandidate(
                score=58,
                priority=WeeklyReportPriority(
                    type="maintain_consistency",
                    text="Keep the same logging rhythm on most days.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    if insight.type in {"consistency", "logging_coverage"} and insight.tone != "positive":
        return [
            _PriorityCandidate(
                score=95 if insight.tone == "negative" else 80,
                priority=WeeklyReportPriority(
                    type="increase_logging_coverage",
                    text="Get to at least 4 valid logged days next week.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    if insight.type == "start_of_day_pattern" and insight.tone != "positive":
        return [
            _PriorityCandidate(
                score=82,
                priority=WeeklyReportPriority(
                    type="stabilize_start_of_day",
                    text="Log your first meal in a similar morning window.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    if insight.type == "day_completion_pattern" and insight.tone != "positive":
        return [
            _PriorityCandidate(
                score=88,
                priority=WeeklyReportPriority(
                    type="improve_day_completion",
                    text="Close more days with a logged dinner or late meal.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    if insight.type == "weekend_drift" and insight.tone != "positive":
        return [
            _PriorityCandidate(
                score=90,
                priority=WeeklyReportPriority(
                    type="reduce_weekend_drift",
                    text="Protect Saturday and Sunday so they do not break the week.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    if insight.type in {
        "day_completion_pattern",
        "improving_trend",
        "weekend_drift",
    } and insight.tone == "positive":
        return [
            _PriorityCandidate(
                score=54,
                priority=WeeklyReportPriority(
                    type="maintain_consistency",
                    text="Keep the same logging rhythm on most days.",
                    reasonCodes=list(insight.reasonCodes),
                ),
            )
        ]
    return []


def _build_summary(
    insights: list[WeeklyReportInsight],
    priorities: list[WeeklyReportPriority],
) -> str:
    if not insights:
        return "Weekly report is ready."

    lead = _summary_lead(insights[0])
    if not priorities:
        return lead

    focus = f" Next focus: {priorities[0].text[0].lower()}{priorities[0].text[1:]}"
    summary = f"{lead}{focus}"
    if len(summary) <= 160:
        return summary
    return lead


def _summary_lead(insight: WeeklyReportInsight) -> str:
    if insight.type == "consistency" and insight.tone == "positive":
        return "Logging stayed steady across the week."
    if insight.type == "consistency":
        return "Logging was not steady enough across the week."
    if insight.type == "logging_coverage" and insight.tone == "positive":
        return "You gave the week strong logging coverage."
    if insight.type == "logging_coverage":
        return "Logging coverage was the main gap this week."
    if insight.type == "start_of_day_pattern" and insight.tone == "positive":
        return "Your first meal timing stayed fairly stable."
    if insight.type == "start_of_day_pattern":
        return "Your first meal timing moved around too much."
    if insight.type == "day_completion_pattern" and insight.tone == "positive":
        return "You closed most logged days well."
    if insight.type == "day_completion_pattern":
        return "Too many days stopped before they looked complete."
    if insight.type == "weekend_drift" and insight.tone == "positive":
        return "Weekend logging held up well."
    if insight.type == "weekend_drift":
        return "Weekends broke the pattern from the rest of the week."
    if insight.type == "improving_trend" and insight.tone == "positive":
        return "This week was stronger than the previous one."
    if insight.type == "improving_trend":
        return "This week slipped versus the previous one."
    return "Weekly report is ready."
