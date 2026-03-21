from dataclasses import replace

from app.services.weekly_report_selection import build_weekly_report_content
from app.services.weekly_report_signals import (
    WeeklyConsistencySignal,
    WeeklyDayCompletionSignal,
    WeeklyImprovementSignal,
    WeeklyLoggingCoverageSignal,
    WeeklySignals,
    WeeklyStartOfDaySignal,
    WeeklyWeekendDriftSignal,
)


def _base_signals() -> WeeklySignals:
    return WeeklySignals(
        has_sufficient_data=True,
        sufficiency_reason="enough_valid_days",
        consistency=WeeklyConsistencySignal(
            type="consistency",
            valid_logged_days=6,
            coverage_ratio=0.8571,
            level="strong",
            reason_codes=("valid_logged_days_7_high",),
        ),
        logging_coverage=WeeklyLoggingCoverageSignal(
            type="logging_coverage",
            logged_days=6,
            valid_logged_days=6,
            unknown_detail_days=0,
            valid_coverage_ratio=0.8571,
            level="high",
            reason_codes=("logged_days_7_ok", "unknown_detail_days_absent"),
        ),
        start_of_day_stability=WeeklyStartOfDaySignal(
            type="start_of_day_stability",
            available=True,
            observed_days=6,
            median_hour=8.25,
            spread_minutes=90,
            level="stable",
            reason_codes=("start_of_day_stable",),
        ),
        day_completion_tendency=WeeklyDayCompletionSignal(
            type="day_completion_tendency",
            available=True,
            complete_days=5,
            observed_days=6,
            completion_ratio=0.8333,
            level="consistent",
            reason_codes=("day_completion_consistent",),
        ),
        weekend_drift=WeeklyWeekendDriftSignal(
            type="weekend_drift",
            available=True,
            weekend_valid_ratio=1.0,
            weekday_valid_ratio=0.8,
            delta=0.2,
            pattern="none",
            reason_codes=("weekend_logging_stable",),
        ),
        improving_vs_previous_week=WeeklyImprovementSignal(
            type="improving_vs_previous_week",
            available=True,
            current_valid_logged_days=6,
            previous_valid_logged_days=4,
            delta=2,
            direction="improving",
            reason_codes=("valid_logged_days_up",),
        ),
    )


def test_build_weekly_report_content_selects_strongest_positive_insight_first() -> None:
    content = build_weekly_report_content(_base_signals())

    assert content.insights[0].type == "consistency"
    assert content.insights[0].tone == "positive"
    assert content.insights[0].importance == "high"


def test_build_weekly_report_content_selects_biggest_gap() -> None:
    signals = replace(
        _base_signals(),
        logging_coverage=replace(
            _base_signals().logging_coverage,
            valid_logged_days=2,
            logged_days=3,
            unknown_detail_days=1,
            valid_coverage_ratio=0.2857,
            level="low",
            reason_codes=("logged_days_7_low", "unknown_detail_days_present"),
        ),
        consistency=replace(
            _base_signals().consistency,
            valid_logged_days=2,
            coverage_ratio=0.2857,
            level="weak",
            reason_codes=("valid_logged_days_7_low",),
        ),
    )

    content = build_weekly_report_content(signals)

    assert any(insight.type == "logging_coverage" and insight.tone == "negative" for insight in content.insights)
    assert content.insights[1].type == "logging_coverage"


def test_build_weekly_report_content_skips_redundant_logging_coverage() -> None:
    content = build_weekly_report_content(_base_signals())

    insight_types = [insight.type for insight in content.insights]
    assert "logging_coverage" not in insight_types


def test_build_weekly_report_content_aligns_priorities_with_selected_insights() -> None:
    signals = replace(
        _base_signals(),
        start_of_day_stability=replace(
            _base_signals().start_of_day_stability,
            level="irregular",
            spread_minutes=330,
            reason_codes=("start_of_day_irregular",),
        ),
        day_completion_tendency=replace(
            _base_signals().day_completion_tendency,
            complete_days=1,
            completion_ratio=0.2,
            level="low",
            reason_codes=("day_completion_low",),
        ),
    )

    content = build_weekly_report_content(signals)

    priority_types = [priority.type for priority in content.priorities]
    assert "improve_day_completion" in priority_types
    assert "stabilize_start_of_day" in priority_types


def test_build_weekly_report_content_orders_insights_deterministically() -> None:
    signals = replace(
        _base_signals(),
        improving_vs_previous_week=replace(
            _base_signals().improving_vs_previous_week,
            direction="declining",
            delta=-2,
            current_valid_logged_days=4,
            previous_valid_logged_days=6,
            reason_codes=("valid_logged_days_down",),
        ),
    )

    content_a = build_weekly_report_content(signals)
    content_b = build_weekly_report_content(signals)

    assert content_a == content_b
    assert [insight.type for insight in content_a.insights] == [
        "consistency",
        "improving_trend",
        "day_completion_pattern",
    ]


def test_build_weekly_report_content_stays_bounded() -> None:
    signals = replace(
        _base_signals(),
        weekend_drift=replace(
            _base_signals().weekend_drift,
            pattern="weekend_drop",
            delta=-0.4,
            weekend_valid_ratio=0.5,
            weekday_valid_ratio=0.9,
            reason_codes=("weekend_logging_below_weekday",),
        ),
    )

    content = build_weekly_report_content(signals)

    assert len(content.insights) <= 4
    assert len(content.priorities) <= 2
    assert len(content.summary) <= 160
