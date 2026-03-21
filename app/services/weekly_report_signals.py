from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Literal

from app.core.coercion import round_metric
from app.services.weekly_report_aggregation import WeeklyAggregate, WeeklyDayAggregate

MIN_VALID_LOGGED_DAYS_FOR_REPORT = 4
MIN_START_OF_DAY_OBSERVED_DAYS = 3
MIN_COMPLETION_OBSERVED_DAYS = 3
DAY_COMPLETION_VALID_MEALS_MIN = 3
DAY_COMPLETION_LATE_HOUR_MIN = 18 * 60
DAY_COMPLETION_LATE_MEALS_MIN = 2
START_OF_DAY_STABLE_SPREAD_MAX_MIN = 120
START_OF_DAY_VARIABLE_SPREAD_MAX_MIN = 240
WEEKEND_DRIFT_RATIO_THRESHOLD = 0.25
IMPROVEMENT_DELTA_THRESHOLD = 2

WeeklyConsistencyLevel = Literal["strong", "mixed", "weak"]
WeeklyCoverageLevel = Literal["high", "medium", "low"]
WeeklyStabilityLevel = Literal["stable", "variable", "irregular", "unknown"]
WeeklyCompletionLevel = Literal["consistent", "mixed", "low", "unknown"]
WeeklyWeekendDriftPattern = Literal["none", "weekend_drop", "weekend_lift", "unknown"]
WeeklyImprovementDirection = Literal["improving", "stable", "declining", "unknown"]
WeeklySufficiencyReason = Literal["enough_valid_days", "too_few_valid_days"]


@dataclass(frozen=True)
class WeeklyConsistencySignal:
    type: Literal["consistency"]
    valid_logged_days: int
    coverage_ratio: float
    level: WeeklyConsistencyLevel
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyLoggingCoverageSignal:
    type: Literal["logging_coverage"]
    logged_days: int
    valid_logged_days: int
    unknown_detail_days: int
    valid_coverage_ratio: float
    level: WeeklyCoverageLevel
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyStartOfDaySignal:
    type: Literal["start_of_day_stability"]
    available: bool
    observed_days: int
    median_hour: float | None
    spread_minutes: int | None
    level: WeeklyStabilityLevel
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyDayCompletionSignal:
    type: Literal["day_completion_tendency"]
    available: bool
    complete_days: int
    observed_days: int
    completion_ratio: float | None
    level: WeeklyCompletionLevel
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyWeekendDriftSignal:
    type: Literal["weekend_drift"]
    available: bool
    weekend_valid_ratio: float | None
    weekday_valid_ratio: float | None
    delta: float | None
    pattern: WeeklyWeekendDriftPattern
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyImprovementSignal:
    type: Literal["improving_vs_previous_week"]
    available: bool
    current_valid_logged_days: int
    previous_valid_logged_days: int | None
    delta: int | None
    direction: WeeklyImprovementDirection
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeeklySignals:
    has_sufficient_data: bool
    sufficiency_reason: WeeklySufficiencyReason
    consistency: WeeklyConsistencySignal
    logging_coverage: WeeklyLoggingCoverageSignal
    start_of_day_stability: WeeklyStartOfDaySignal
    day_completion_tendency: WeeklyDayCompletionSignal
    weekend_drift: WeeklyWeekendDriftSignal
    improving_vs_previous_week: WeeklyImprovementSignal


def _valid_logged_days(days: tuple[WeeklyDayAggregate, ...]) -> list[WeeklyDayAggregate]:
    return [day for day in days if day.valid_meal_count > 0]


def _logged_days(days: tuple[WeeklyDayAggregate, ...]) -> list[WeeklyDayAggregate]:
    return [day for day in days if day.meal_count > 0]


def _unknown_detail_days(days: tuple[WeeklyDayAggregate, ...]) -> int:
    return sum(1 for day in days if day.has_unknown_meal_details)


def _signal_level_for_consistency(valid_logged_days: int) -> WeeklyConsistencyLevel:
    if valid_logged_days >= 6:
        return "strong"
    if valid_logged_days >= 4:
        return "mixed"
    return "weak"


def _signal_level_for_coverage(
    *,
    valid_logged_days: int,
    unknown_detail_days: int,
) -> WeeklyCoverageLevel:
    if valid_logged_days >= 6 and unknown_detail_days <= 1:
        return "high"
    if valid_logged_days >= 4:
        return "medium"
    return "low"


def _median_hour(values: list[int]) -> float | None:
    if not values:
        return None
    return round_metric(float(median(values)) / 60, 2)


def _spread_minutes(values: list[int]) -> int | None:
    if not values:
        return None
    return max(values) - min(values)


def _start_of_day_level(spread_minutes: int | None) -> WeeklyStabilityLevel:
    if spread_minutes is None:
        return "unknown"
    if spread_minutes <= START_OF_DAY_STABLE_SPREAD_MAX_MIN:
        return "stable"
    if spread_minutes <= START_OF_DAY_VARIABLE_SPREAD_MAX_MIN:
        return "variable"
    return "irregular"


def _is_complete_logging_day(day: WeeklyDayAggregate) -> bool:
    if day.valid_meal_count >= DAY_COMPLETION_VALID_MEALS_MIN:
        return True
    if "dinner" in day.valid_meal_types:
        return True
    return (
        day.valid_meal_count >= DAY_COMPLETION_LATE_MEALS_MIN
        and day.last_logged_at_local_min is not None
        and day.last_logged_at_local_min >= DAY_COMPLETION_LATE_HOUR_MIN
    )


def _completion_level(completion_ratio: float | None) -> WeeklyCompletionLevel:
    if completion_ratio is None:
        return "unknown"
    if completion_ratio >= 0.75:
        return "consistent"
    if completion_ratio >= 0.5:
        return "mixed"
    return "low"


def derive_weekly_signals(aggregate: WeeklyAggregate) -> WeeklySignals:
    days = aggregate.days
    previous_days = aggregate.previous_days
    logged_days = _logged_days(days)
    valid_logged_days = _valid_logged_days(days)
    previous_valid_logged_days = _valid_logged_days(previous_days)
    unknown_detail_days = _unknown_detail_days(days)

    valid_logged_day_count = len(valid_logged_days)
    logged_day_count = len(logged_days)
    valid_coverage_ratio = round_metric(valid_logged_day_count / len(days))
    has_sufficient_data = valid_logged_day_count >= MIN_VALID_LOGGED_DAYS_FOR_REPORT
    sufficiency_reason: WeeklySufficiencyReason = (
        "enough_valid_days" if has_sufficient_data else "too_few_valid_days"
    )

    consistency = WeeklyConsistencySignal(
        type="consistency",
        valid_logged_days=valid_logged_day_count,
        coverage_ratio=valid_coverage_ratio,
        level=_signal_level_for_consistency(valid_logged_day_count),
        reason_codes=(
            "valid_logged_days_7_high"
            if valid_logged_day_count >= 6
            else "valid_logged_days_7_medium"
            if valid_logged_day_count >= 4
            else "valid_logged_days_7_low",
        ),
    )

    logging_coverage = WeeklyLoggingCoverageSignal(
        type="logging_coverage",
        logged_days=logged_day_count,
        valid_logged_days=valid_logged_day_count,
        unknown_detail_days=unknown_detail_days,
        valid_coverage_ratio=valid_coverage_ratio,
        level=_signal_level_for_coverage(
            valid_logged_days=valid_logged_day_count,
            unknown_detail_days=unknown_detail_days,
        ),
        reason_codes=(
            "logged_days_7_low" if logged_day_count < 4 else "logged_days_7_ok",
            "unknown_detail_days_present"
            if unknown_detail_days > 0
            else "unknown_detail_days_absent",
        ),
    )

    first_meal_minutes = [
        day.first_logged_at_local_min
        for day in logged_days
        if day.first_logged_at_local_min is not None
    ]
    start_spread = _spread_minutes(first_meal_minutes)
    start_available = len(first_meal_minutes) >= MIN_START_OF_DAY_OBSERVED_DAYS
    start_of_day_stability = WeeklyStartOfDaySignal(
        type="start_of_day_stability",
        available=start_available,
        observed_days=len(first_meal_minutes),
        median_hour=_median_hour(first_meal_minutes) if start_available else None,
        spread_minutes=start_spread if start_available else None,
        level=_start_of_day_level(start_spread) if start_available else "unknown",
        reason_codes=(
            ("start_of_day_observed_days_low",)
            if not start_available
            else (
                "start_of_day_stable"
                if _start_of_day_level(start_spread) == "stable"
                else "start_of_day_variable"
                if _start_of_day_level(start_spread) == "variable"
                else "start_of_day_irregular",
            )
        ),
    )

    completion_observed_days = len(valid_logged_days)
    complete_days = sum(1 for day in valid_logged_days if _is_complete_logging_day(day))
    completion_available = completion_observed_days >= MIN_COMPLETION_OBSERVED_DAYS
    completion_ratio = (
        round_metric(complete_days / completion_observed_days)
        if completion_available and completion_observed_days > 0
        else None
    )
    day_completion_tendency = WeeklyDayCompletionSignal(
        type="day_completion_tendency",
        available=completion_available,
        complete_days=complete_days,
        observed_days=completion_observed_days,
        completion_ratio=completion_ratio,
        level=_completion_level(completion_ratio) if completion_available else "unknown",
        reason_codes=(
            ("day_completion_observed_days_low",)
            if not completion_available
            else (
                "day_completion_consistent"
                if _completion_level(completion_ratio) == "consistent"
                else "day_completion_mixed"
                if _completion_level(completion_ratio) == "mixed"
                else "day_completion_low",
            )
        ),
    )

    weekend_days = [day for day in days if day.is_weekend]
    weekday_days = [day for day in days if not day.is_weekend]
    weekend_valid_ratio = (
        round_metric(sum(1 for day in weekend_days if day.valid_meal_count > 0) / len(weekend_days))
        if weekend_days
        else None
    )
    weekday_valid_ratio = (
        round_metric(sum(1 for day in weekday_days if day.valid_meal_count > 0) / len(weekday_days))
        if weekday_days
        else None
    )
    weekend_drift_available = (
        weekend_valid_ratio is not None
        and weekday_valid_ratio is not None
        and valid_logged_day_count >= MIN_VALID_LOGGED_DAYS_FOR_REPORT
    )
    weekend_delta = (
        round_metric(weekend_valid_ratio - weekday_valid_ratio)
        if weekend_drift_available
        else None
    )
    if not weekend_drift_available:
        weekend_pattern: WeeklyWeekendDriftPattern = "unknown"
        weekend_reason_codes: tuple[str, ...] = ("weekend_drift_insufficient_data",)
    elif weekend_delta <= -WEEKEND_DRIFT_RATIO_THRESHOLD:
        weekend_pattern = "weekend_drop"
        weekend_reason_codes = ("weekend_logging_below_weekday",)
    elif weekend_delta >= WEEKEND_DRIFT_RATIO_THRESHOLD:
        weekend_pattern = "weekend_lift"
        weekend_reason_codes = ("weekend_logging_above_weekday",)
    else:
        weekend_pattern = "none"
        weekend_reason_codes = ("weekend_logging_stable",)
    weekend_drift = WeeklyWeekendDriftSignal(
        type="weekend_drift",
        available=weekend_drift_available,
        weekend_valid_ratio=weekend_valid_ratio if weekend_drift_available else None,
        weekday_valid_ratio=weekday_valid_ratio if weekend_drift_available else None,
        delta=weekend_delta,
        pattern=weekend_pattern,
        reason_codes=weekend_reason_codes,
    )

    previous_valid_day_count = len(previous_valid_logged_days)
    improvement_available = previous_valid_day_count > 0
    delta = valid_logged_day_count - previous_valid_day_count if improvement_available else None
    if not improvement_available:
        improvement_direction: WeeklyImprovementDirection = "unknown"
        improvement_reason_codes: tuple[str, ...] = ("previous_week_missing",)
    elif delta >= IMPROVEMENT_DELTA_THRESHOLD:
        improvement_direction = "improving"
        improvement_reason_codes = ("valid_logged_days_up",)
    elif delta <= -IMPROVEMENT_DELTA_THRESHOLD:
        improvement_direction = "declining"
        improvement_reason_codes = ("valid_logged_days_down",)
    else:
        improvement_direction = "stable"
        improvement_reason_codes = ("valid_logged_days_flat",)
    improving_vs_previous_week = WeeklyImprovementSignal(
        type="improving_vs_previous_week",
        available=improvement_available,
        current_valid_logged_days=valid_logged_day_count,
        previous_valid_logged_days=previous_valid_day_count if improvement_available else None,
        delta=delta,
        direction=improvement_direction,
        reason_codes=improvement_reason_codes,
    )

    return WeeklySignals(
        has_sufficient_data=has_sufficient_data,
        sufficiency_reason=sufficiency_reason,
        consistency=consistency,
        logging_coverage=logging_coverage,
        start_of_day_stability=start_of_day_stability,
        day_completion_tendency=day_completion_tendency,
        weekend_drift=weekend_drift,
        improving_vs_previous_week=improving_vs_previous_week,
    )
