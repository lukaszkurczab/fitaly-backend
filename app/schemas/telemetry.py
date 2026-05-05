"""Schemas for telemetry batch ingestion under the v2 API."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SESSION_ID_LENGTH = 128
MAX_EVENT_ID_LENGTH = 128
MAX_EVENT_NAME_LENGTH = 64
MAX_PLATFORM_LENGTH = 32
MAX_APP_VERSION_LENGTH = 40
MAX_BUILD_LENGTH = 40
MAX_LOCALE_LENGTH = 35
MAX_PROP_KEY_LENGTH = 40
MAX_PROP_STRING_LENGTH = 200
MAX_PROPS_JSON_LENGTH = 2_048
MAX_BATCH_SIZE = 50

ALLOWED_TELEMETRY_EVENT_NAMES = frozenset(
    {
        "session_start",
        "onboarding_completed",
        "meal_logged",
        "ai_meal_review_saved",
        "notification_opened",
        "paywall_view",
        "purchase_started",
        "purchase_succeeded",
        "entitlement_confirmed",
        "entitlement_confirmation_failed",
        "first_premium_feature_used",
        "restore_started",
        "restore_succeeded",
        "restore_failed",
        "weekly_report_opened",
        "weekly_report_locked_viewed",
        "weekly_report_access_blocked",
        "coach_insight_viewed",
        "coach_insight_tapped",
        "smart_reminder_suppressed",
        "smart_reminder_scheduled",
        "smart_reminder_noop",
        "smart_reminder_decision_failed",
        "smart_reminder_schedule_failed",
    }
)

SMART_REMINDER_CONFIDENCE_BUCKETS = frozenset({"low", "medium", "high"})
SMART_REMINDER_SCHEDULED_WINDOWS = frozenset(
    {"overnight", "morning", "afternoon", "evening", "late_evening"}
)
SMART_REMINDER_KINDS = frozenset(
    {"log_first_meal", "log_next_meal", "complete_day"}
)
SMART_REMINDER_SUPPRESSION_REASONS = frozenset(
    {
        "reminders_disabled",
        "quiet_hours",
        "already_logged_recently",
        "recent_activity_detected",
        "frequency_cap_reached",
    }
)
SMART_REMINDER_NOOP_REASONS = frozenset(
    {"insufficient_signal", "day_already_complete"}
)
SMART_REMINDER_DECISION_FAILURE_REASONS = frozenset(
    {"invalid_payload", "service_unavailable"}
)
SMART_REMINDER_SCHEDULE_FAILURE_REASONS = frozenset(
    {"permission_unavailable", "invalid_time", "schedule_error"}
)
MEAL_INPUT_METHODS = frozenset(
    {"manual", "photo", "barcode", "text", "saved", "quick_add"}
)
MEAL_SOURCES = frozenset({"manual", "ai", "saved"})
AI_MEAL_REVIEW_INPUT_METHODS = frozenset({"photo", "text"})
PAYWALL_SOURCES = frozenset({"manage_subscription", "meal_text_limit"})
PAYWALL_TRIGGER_SOURCES = frozenset(
    {"manage_subscription_screen", "meal_text_limit_modal"}
)
PURCHASE_SOURCES = frozenset({"manage_subscription"})
ENTITLEMENT_SOURCES = frozenset({"purchase", "restore"})
ENTITLEMENT_TIERS = frozenset({"premium"})
ENTITLEMENT_CONFIRMATION_FAILURE_REASONS = frozenset(
    {
        "billing_unavailable",
        "billing_not_initialized",
        "entitlement_inactive",
        "login_failed",
        "network",
        "no_offerings",
        "purchase_not_allowed",
        "sign_in_required",
        "store_problem",
        "sync_tier_failed",
        "credits_not_premium",
        "unknown",
    }
)
WEEKLY_REPORT_STATUSES = frozenset({"ready", "insufficient_data", "unavailable"})
WEEKLY_REPORT_SOURCES = frozenset({"remote", "fallback", "disabled"})
WEEKLY_REPORT_ACCESS_STATES = frozenset({"premium", "locked", "degraded", "unknown"})
COACH_INSIGHT_TYPES = frozenset(
    {
        "under_logging",
        "high_unknown_meal_details",
        "low_protein_consistency",
        "calorie_under_target",
        "positive_momentum",
        "stable",
    }
)
COACH_ACTION_TYPES = frozenset(
    {"log_next_meal", "open_chat", "review_history", "none"}
)
COACH_TAPPABLE_ACTION_TYPES = frozenset(
    {"log_next_meal", "open_chat", "review_history"}
)
COACH_INSIGHT_FRESHNESS = frozenset({"fresh", "degraded", "stale"})
ONBOARDING_MODES = frozenset({"first", "refill"})
NOTIFICATION_ORIGINS = frozenset(
    {"user_notifications", "system_notifications", "unknown"}
)

DISALLOWED_TELEMETRY_PROP_KEY_PATTERN = re.compile(
    r"(message|content|email|name|phone)",
    re.IGNORECASE,
)

ALLOWED_TELEMETRY_EVENT_PROPS: dict[str, frozenset[str]] = {
    "session_start": frozenset({"origin"}),
    "onboarding_completed": frozenset({"mode"}),
    "meal_logged": frozenset({"mealInputMethod", "ingredientCount", "source"}),
    "ai_meal_review_saved": frozenset(
        {"inputMethod", "corrected", "ingredientCount", "requestId"}
    ),
    "notification_opened": frozenset({"notificationType", "origin"}),
    "paywall_view": frozenset({"source", "trigger_source"}),
    "purchase_started": frozenset({"source"}),
    "purchase_succeeded": frozenset({"source"}),
    "entitlement_confirmed": frozenset({"source", "tier"}),
    "entitlement_confirmation_failed": frozenset({"source", "reason"}),
    "first_premium_feature_used": frozenset({"source", "feature"}),
    "restore_started": frozenset({"source"}),
    "restore_succeeded": frozenset({"source", "confirmed"}),
    "restore_failed": frozenset({"source", "reason"}),
    "weekly_report_opened": frozenset(
        {
            "reportStatus",
            "insightCount",
            "priorityCount",
            "source",
            "accessState",
            "accessReason",
        }
    ),
    "weekly_report_locked_viewed": frozenset(
        {"source", "accessState", "accessReason"}
    ),
    "weekly_report_access_blocked": frozenset(
        {"source", "accessState", "accessReason"}
    ),
    "coach_insight_viewed": frozenset(
        {"insightType", "actionType", "freshness"}
    ),
    "coach_insight_tapped": frozenset(
        {"insightType", "actionType", "freshness"}
    ),
    "smart_reminder_suppressed": frozenset(
        {"decision", "suppressionReason", "confidenceBucket"}
    ),
    "smart_reminder_scheduled": frozenset(
        {"reminderKind", "decision", "confidenceBucket", "scheduledWindow"}
    ),
    "smart_reminder_noop": frozenset(
        {"decision", "noopReason", "confidenceBucket"}
    ),
    "smart_reminder_decision_failed": frozenset({"failureReason"}),
    "smart_reminder_schedule_failed": frozenset(
        {"reminderKind", "decision", "confidenceBucket", "failureReason"}
    ),
}

ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES: dict[
    str, dict[str, frozenset[str]]
] = {
    "smart_reminder_suppressed": {
        "decision": frozenset({"suppress"}),
        "suppressionReason": SMART_REMINDER_SUPPRESSION_REASONS,
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
    },
    "smart_reminder_scheduled": {
        "reminderKind": SMART_REMINDER_KINDS,
        "decision": frozenset({"send"}),
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
        "scheduledWindow": SMART_REMINDER_SCHEDULED_WINDOWS,
    },
    "smart_reminder_noop": {
        "decision": frozenset({"noop"}),
        "noopReason": SMART_REMINDER_NOOP_REASONS,
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
    },
    "smart_reminder_decision_failed": {
        "failureReason": SMART_REMINDER_DECISION_FAILURE_REASONS,
    },
    "smart_reminder_schedule_failed": {
        "reminderKind": SMART_REMINDER_KINDS,
        "decision": frozenset({"send"}),
        "confidenceBucket": SMART_REMINDER_CONFIDENCE_BUCKETS,
        "failureReason": SMART_REMINDER_SCHEDULE_FAILURE_REASONS,
    },
    "onboarding_completed": {
        "mode": ONBOARDING_MODES,
    },
    "meal_logged": {
        "mealInputMethod": MEAL_INPUT_METHODS,
        "source": MEAL_SOURCES,
    },
    "ai_meal_review_saved": {
        "inputMethod": AI_MEAL_REVIEW_INPUT_METHODS,
    },
    "notification_opened": {
        "origin": NOTIFICATION_ORIGINS,
    },
    "paywall_view": {
        "source": PAYWALL_SOURCES,
        "trigger_source": PAYWALL_TRIGGER_SOURCES,
    },
    "purchase_started": {
        "source": PURCHASE_SOURCES,
    },
    "purchase_succeeded": {
        "source": PURCHASE_SOURCES,
    },
    "entitlement_confirmed": {
        "source": ENTITLEMENT_SOURCES,
        "tier": ENTITLEMENT_TIERS,
    },
    "entitlement_confirmation_failed": {
        "source": ENTITLEMENT_SOURCES,
        "reason": ENTITLEMENT_CONFIRMATION_FAILURE_REASONS,
    },
    "restore_started": {
        "source": PURCHASE_SOURCES,
    },
    "restore_failed": {
        "source": PURCHASE_SOURCES,
        "reason": ENTITLEMENT_CONFIRMATION_FAILURE_REASONS,
    },
    "weekly_report_opened": {
        "reportStatus": WEEKLY_REPORT_STATUSES,
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": WEEKLY_REPORT_ACCESS_STATES,
    },
    "weekly_report_locked_viewed": {
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": frozenset({"locked"}),
    },
    "weekly_report_access_blocked": {
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": frozenset({"degraded", "unknown"}),
    },
    "coach_insight_viewed": {
        "insightType": COACH_INSIGHT_TYPES,
        "actionType": COACH_ACTION_TYPES,
        "freshness": COACH_INSIGHT_FRESHNESS,
    },
    "coach_insight_tapped": {
        "insightType": COACH_INSIGHT_TYPES,
        "actionType": COACH_TAPPABLE_ACTION_TYPES,
        "freshness": COACH_INSIGHT_FRESHNESS,
    },
}

def _is_allowed_prop_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, str | int | float):
        return True
    if isinstance(value, list):
        values = cast(list[object], value)
        if len(values) > 10:
            return False
        return all(
            _is_allowed_prop_value(item) and not isinstance(item, list | dict)
            for item in values
        )
    return False


def _rejected_events_default() -> list["RejectedTelemetryEvent"]:
    return []


def _daily_event_counts_default() -> list["TelemetrySummaryEventCount"]:
    return []


def _daily_summary_buckets_default() -> list["TelemetryDailySummaryBucket"]:
    return []


def _smart_reason_counts_default() -> list["SmartReminderReasonCount"]:
    return []


def _smart_kind_counts_default() -> list["SmartReminderKindCount"]:
    return []


def _smart_daily_buckets_default() -> list["SmartReminderDailyBucket"]:
    return []


class TelemetryAppContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=MAX_PLATFORM_LENGTH)
    appVersion: str = Field(min_length=1, max_length=MAX_APP_VERSION_LENGTH)
    build: str | None = Field(default=None, max_length=MAX_BUILD_LENGTH)

    @field_validator("platform", "appVersion", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("build", mode="before")
    @classmethod
    def normalize_build(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class TelemetryDeviceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, max_length=MAX_LOCALE_LENGTH)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)

    @field_validator("locale", mode="before")
    @classmethod
    def normalize_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class TelemetryEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1, max_length=MAX_EVENT_ID_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_EVENT_NAME_LENGTH)
    ts: datetime
    props: dict[str, Any] | None = None

    @field_validator("eventId", "name", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("props")
    @classmethod
    def validate_props(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None

        for key, prop_value in value.items():
            if len(key) > MAX_PROP_KEY_LENGTH:
                raise ValueError("Telemetry property key is too long")
            if not _is_allowed_prop_value(prop_value):
                raise ValueError("Telemetry property value type is not allowed")
            if isinstance(prop_value, str) and len(prop_value) > MAX_PROP_STRING_LENGTH:
                raise ValueError("Telemetry property value is too long")
            if isinstance(prop_value, list):
                prop_items = cast(list[object], prop_value)
                for item in prop_items:
                    if isinstance(item, str) and len(item) > MAX_PROP_STRING_LENGTH:
                        raise ValueError("Telemetry property array value is too long")

        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(serialized.encode("utf-8")) > MAX_PROPS_JSON_LENGTH:
            raise ValueError("Telemetry props payload is too large")

        return value

    @model_validator(mode="after")
    def validate_props_contract(self) -> "TelemetryEventInput":
        if self.name not in ALLOWED_TELEMETRY_EVENT_NAMES:
            return self

        props = self.props or {}
        allowed_props = ALLOWED_TELEMETRY_EVENT_PROPS.get(self.name, frozenset())
        for key in props:
            if DISALLOWED_TELEMETRY_PROP_KEY_PATTERN.search(key):
                raise ValueError("Telemetry property key is privacy-sensitive")
            if key not in allowed_props:
                raise ValueError(
                    f"Telemetry property '{key}' is not allowed for event '{self.name}'"
                )

        allowed_enum_values = ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES.get(self.name, {})
        for key, allowed_values in allowed_enum_values.items():
            if key not in props:
                continue
            value = props[key]
            if not isinstance(value, str) or value not in allowed_values:
                raise ValueError(
                    f"Telemetry property '{key}' has invalid value for event '{self.name}'"
                )

        return self


class TelemetryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str = Field(min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    app: TelemetryAppContext
    device: TelemetryDeviceContext
    events: list[TelemetryEventInput] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @field_validator("sessionId", mode="before")
    @classmethod
    def normalize_session_id(cls, value: str) -> str:
        return value.strip()


class RejectedTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str
    name: str
    reason: str


class TelemetryBatchIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptedCount: int
    duplicateCount: int
    rejectedCount: int
    rejectedEvents: list[RejectedTelemetryEvent] = Field(default_factory=_rejected_events_default)

    @model_validator(mode="after")
    def validate_counts(self) -> "TelemetryBatchIngestResponse":
        if self.rejectedCount != len(self.rejectedEvents):
            raise ValueError("Rejected event count does not match rejected event list")
        return self


class TelemetrySummaryEventCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int = Field(ge=0)


class TelemetryDailySummaryBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str
    totalEvents: int = Field(ge=0)
    eventCounts: list[TelemetrySummaryEventCount] = Field(default_factory=_daily_event_counts_default)


class TelemetryDailySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generatedAt: str
    days: int = Field(ge=1, le=30)
    buckets: list[TelemetryDailySummaryBucket] = Field(default_factory=_daily_summary_buckets_default)


# ---------------------------------------------------------------------------
# Smart Reminders rollout summary
# ---------------------------------------------------------------------------

SMART_REMINDER_EVENT_NAMES = frozenset(
    {
        "smart_reminder_scheduled",
        "smart_reminder_suppressed",
        "smart_reminder_noop",
        "smart_reminder_decision_failed",
        "smart_reminder_schedule_failed",
    }
)


class SmartReminderOutcomeTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled: int = Field(ge=0)
    suppressed: int = Field(ge=0)
    noop: int = Field(ge=0)
    decisionFailed: int = Field(ge=0)
    scheduleFailed: int = Field(ge=0)
    sendRatio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Ratio of scheduled reminders to total outcomes "
            "(scheduled + suppressed + noop).  null when denominator is 0."
        ),
    )


class SmartReminderReasonCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    count: int = Field(ge=0)


class SmartReminderKindCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    count: int = Field(ge=0)


class SmartReminderDailyBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str
    scheduled: int = Field(ge=0, default=0)
    suppressed: int = Field(ge=0, default=0)
    noop: int = Field(ge=0, default=0)
    decisionFailed: int = Field(ge=0, default=0)
    scheduleFailed: int = Field(ge=0, default=0)


class SmartReminderRolloutSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generatedAt: str
    days: int = Field(ge=1, le=30)
    totals: SmartReminderOutcomeTotals
    suppressionReasons: list[SmartReminderReasonCount] = Field(default_factory=_smart_reason_counts_default)
    noopReasons: list[SmartReminderReasonCount] = Field(default_factory=_smart_reason_counts_default)
    reminderKinds: list[SmartReminderKindCount] = Field(default_factory=_smart_kind_counts_default)
    dailyBuckets: list[SmartReminderDailyBucket] = Field(default_factory=_smart_daily_buckets_default)
