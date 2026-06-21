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
MAX_ACTOR_ID_LENGTH = 128
MAX_PLATFORM_LENGTH = 32
MAX_APP_VERSION_LENGTH = 40
MAX_BUILD_LENGTH = 40
MAX_LOCALE_LENGTH = 35
MAX_TIMEZONE_LENGTH = 64
MAX_REQUEST_ID_LENGTH = 128
MAX_PROP_KEY_LENGTH = 40
MAX_PROP_STRING_LENGTH = 200
MAX_PROPS_JSON_LENGTH = 2_048
MAX_BATCH_SIZE = 50
CURRENT_TELEMETRY_SCHEMA_VERSION = 2
TELEMETRY_REJECTION_EVENT_NOT_ALLOWED = "event_not_allowed"
TELEMETRY_REJECTION_ACTOR_AUTH_MISMATCH = "actor_auth_mismatch"
TELEMETRY_REJECTION_UNAUTHENTICATED_USER_ACTOR = "unauthenticated_user_actor"
TELEMETRY_REJECTION_REASONS = frozenset(
    {
        TELEMETRY_REJECTION_EVENT_NOT_ALLOWED,
        TELEMETRY_REJECTION_ACTOR_AUTH_MISMATCH,
        TELEMETRY_REJECTION_UNAUTHENTICATED_USER_ACTOR,
    }
)

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
        "autocomplete_search_outcome",
        "autocomplete_result_selected",
        "ingredient_product_create_outcome",
        "home_next_action_shown",
        "home_next_action_started",
        "home_next_action_dismissed",
        "memory_candidate_created",
        "memory_candidate_confirmed",
        "memory_candidate_dismissed",
        "memory_used",
        "memory_muted",
        "memory_deleted",
        "planned_meal_created",
        "planned_meal_confirmed",
        "planned_meal_changed",
        "planned_meal_skipped",
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
    {"permission_unavailable", "channel_unavailable", "invalid_time", "schedule_error"}
)
MEAL_INPUT_METHODS = frozenset({"manual", "photo", "barcode", "text"})
MEAL_SOURCES = frozenset({"manual", "ai", "saved"})
AI_MEAL_REVIEW_INPUT_METHODS = frozenset({"photo", "text"})
PAYWALL_SOURCES = frozenset({"manage_subscription", "meal_text_limit"})
PAYWALL_TRIGGER_SOURCES = frozenset(
    {"manage_subscription_screen", "meal_text_limit_modal"}
)
PURCHASE_SOURCES = frozenset({"manage_subscription"})
RESTORE_SOURCES = frozenset({"manage_subscription"})
ENTITLEMENT_SOURCES = frozenset({"purchase", "restore", "manage_subscription"})
ENTITLEMENT_TIERS = frozenset({"premium"})
ENTITLEMENT_CONFIRMATION_FAILURE_REASONS = frozenset(
    {
        "billing_unavailable",
        "billing_not_initialized",
        "rc_not_configured",
        "no_active_entitlement",
        "entitlement_inactive",
        "login_failed",
        "network",
        "no_offerings",
        "purchase_not_allowed",
        "sign_in_required",
        "store_problem",
        "sync_tier_failed",
        "access_unknown_degraded",
        "credits_missing",
        "uid_mismatch",
        "credits_not_premium",
        "unknown",
    }
)
WEEKLY_REPORT_STATUSES = frozenset({"ready", "insufficient_data", "unavailable"})
WEEKLY_REPORT_SOURCES = frozenset({"remote", "fallback", "disabled"})
WEEKLY_REPORT_ACCESS_STATES = frozenset({"premium", "locked", "degraded", "unknown"})
WEEKLY_REPORT_ACCESS_REASONS = frozenset(
    {"requires_premium", "premium_required", "degraded", "feature_disabled"}
)
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
SESSION_START_ORIGINS = frozenset({"app_boot"})
ONBOARDING_MODES = frozenset({"first", "refill"})
NOTIFICATION_ORIGINS = frozenset(
    {"user_notifications", "system_notifications", "unknown"}
)
NOTIFICATION_TYPES = frozenset(
    {
        "meal_reminder",
        "day_fill",
        "stats_weekly_summary",
        "motivation_dont_give_up",
        "calorie_goal",
        "unknown",
    }
)
NOTIFICATION_ACTION_IDENTIFIERS = frozenset({"default", "open_chat"})
AUTOCOMPLETE_SURFACES = frozenset({"manual_ingredient_sheet"})
AUTOCOMPLETE_SEARCH_OUTCOMES = frozenset(
    {
        "results",
        "no_results",
        "offline",
        "warning",
        "stale",
        "backend_degraded",
        "error",
    }
)
AUTOCOMPLETE_QUERY_LENGTH_BUCKETS = frozenset({"2_3", "4_8", "9_16", "17_plus"})
AUTOCOMPLETE_RESULT_COUNT_BUCKETS = frozenset(
    {"0", "1", "2_3", "4_6", "7_12", "13_plus"}
)
AUTOCOMPLETE_LATENCY_BUCKETS = frozenset(
    {"under_250_ms", "250_750_ms", "750_1500_ms", "1500_ms_plus"}
)
AUTOCOMPLETE_SOURCE_CLASSES = frozenset(
    {"remote", "cache", "none", "global", "user_scoped"}
)
AUTOCOMPLETE_RANK_BUCKETS = frozenset({"1", "2_3", "4_6", "7_12", "13_plus"})
AUTOCOMPLETE_SELECTION_STATES = frozenset({"selected"})
INGREDIENT_PRODUCT_CREATE_OUTCOMES = frozenset({"synced", "queued", "failed"})
AUTOCOMPLETE_WARNING_REASONS = frozenset(
    {
        "profile_unknown",
        "profile_warning",
        "profile_incompatible",
        "nutrition_low_confidence",
        "nutrition_missing",
        "source_candidate_only",
        "cache_stale",
        "offline_cache",
        "pending_user_record",
        "query_too_short",
        "backend_degraded",
    }
)
HOME_NEXT_ACTION_TYPES = frozenset(
    {"continue_review", "continue_planned_item", "confirm_known_pattern"}
)
HOME_NEXT_ACTION_STATES = frozenset({"eligible"})
HOME_NEXT_ACTION_REASON_CODES = frozenset(
    {"review_draft_available", "planned_item_due", "known_pattern_available"}
)
HOME_NEXT_ACTION_SOURCE_DOMAINS = frozenset(
    {"review_draft", "planned_meal", "known_pattern_candidate"}
)
HOME_NEXT_ACTION_OWNER_FLOWS = frozenset(
    {"ReviewMeal", "Planning", "MealAddMethod"}
)
HOME_NEXT_ACTION_COOLDOWN_BUCKETS = frozenset({"24h"})
C5_MEMORY_TYPES = frozenset(
    {"typical_portion", "review_correction", "ingredient_product_selection"}
)
C5_TELEMETRY_SURFACES = frozenset(
    {"review", "memory_center", "settings", "planning", "home_next_action"}
)
C5_CONFIDENCE_BUCKETS = frozenset({"low", "medium", "high"})
C5_ACTION_RESULTS = frozenset({"succeeded", "queued", "blocked", "failed"})
C5_FEATURE_STATES = frozenset({"enabled", "disabled", "shadow"})
C5_PLANNED_MEAL_SOURCE_TYPES = frozenset(
    {"manual", "saved_meal", "recipe", "ingredient_product_draft"}
)
C5_PLANNED_MEAL_ESTIMATE_STATES = frozenset({"known", "partial", "unknown"})

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
    "notification_opened": frozenset(
        {"notificationType", "origin", "actionIdentifier", "openedFromBackground"}
    ),
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
    "autocomplete_search_outcome": frozenset(
        {
            "surface",
            "outcome",
            "queryLengthBucket",
            "resultCountBucket",
            "sourceClass",
            "latencyBucket",
            "warningReason",
        }
    ),
    "autocomplete_result_selected": frozenset(
        {
            "surface",
            "resultCountBucket",
            "sourceClass",
            "rankBucket",
            "selectionState",
            "warningReason",
        }
    ),
    "ingredient_product_create_outcome": frozenset({"surface", "outcome"}),
    "home_next_action_shown": frozenset(
        {"actionType", "state", "reasonCode", "sourceDomain"}
    ),
    "home_next_action_started": frozenset({"actionType", "ownerFlow", "state"}),
    "home_next_action_dismissed": frozenset(
        {"actionType", "reasonCode", "cooldownBucket"}
    ),
    "memory_candidate_created": frozenset(
        {"memoryType", "surface", "confidenceBucket", "featureState"}
    ),
    "memory_candidate_confirmed": frozenset(
        {
            "memoryType",
            "surface",
            "confidenceBucket",
            "actionResult",
            "featureState",
        }
    ),
    "memory_candidate_dismissed": frozenset(
        {"memoryType", "surface", "actionResult", "featureState"}
    ),
    "memory_used": frozenset(
        {"memoryType", "surface", "actionResult", "featureState"}
    ),
    "memory_muted": frozenset(
        {"memoryType", "surface", "actionResult", "featureState"}
    ),
    "memory_deleted": frozenset(
        {"memoryType", "surface", "actionResult", "featureState"}
    ),
    "planned_meal_created": frozenset(
        {"sourceType", "estimateState", "surface", "featureState"}
    ),
    "planned_meal_confirmed": frozenset(
        {"sourceType", "estimateState", "surface", "actionResult", "featureState"}
    ),
    "planned_meal_changed": frozenset(
        {"sourceType", "estimateState", "surface", "actionResult", "featureState"}
    ),
    "planned_meal_skipped": frozenset(
        {"sourceType", "estimateState", "surface", "actionResult", "featureState"}
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
    "session_start": {
        "origin": SESSION_START_ORIGINS,
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
        "notificationType": NOTIFICATION_TYPES,
        "origin": NOTIFICATION_ORIGINS,
        "actionIdentifier": NOTIFICATION_ACTION_IDENTIFIERS,
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
        "source": RESTORE_SOURCES,
    },
    "restore_succeeded": {
        "source": RESTORE_SOURCES,
    },
    "restore_failed": {
        "source": RESTORE_SOURCES,
        "reason": ENTITLEMENT_CONFIRMATION_FAILURE_REASONS,
    },
    "weekly_report_opened": {
        "reportStatus": WEEKLY_REPORT_STATUSES,
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": WEEKLY_REPORT_ACCESS_STATES,
        "accessReason": WEEKLY_REPORT_ACCESS_REASONS,
    },
    "weekly_report_locked_viewed": {
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": frozenset({"locked"}),
        "accessReason": WEEKLY_REPORT_ACCESS_REASONS,
    },
    "weekly_report_access_blocked": {
        "source": WEEKLY_REPORT_SOURCES,
        "accessState": frozenset({"degraded", "unknown"}),
        "accessReason": WEEKLY_REPORT_ACCESS_REASONS,
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
    "autocomplete_search_outcome": {
        "surface": AUTOCOMPLETE_SURFACES,
        "outcome": AUTOCOMPLETE_SEARCH_OUTCOMES,
        "queryLengthBucket": AUTOCOMPLETE_QUERY_LENGTH_BUCKETS,
        "resultCountBucket": AUTOCOMPLETE_RESULT_COUNT_BUCKETS,
        "sourceClass": AUTOCOMPLETE_SOURCE_CLASSES,
        "latencyBucket": AUTOCOMPLETE_LATENCY_BUCKETS,
        "warningReason": AUTOCOMPLETE_WARNING_REASONS,
    },
    "autocomplete_result_selected": {
        "surface": AUTOCOMPLETE_SURFACES,
        "resultCountBucket": AUTOCOMPLETE_RESULT_COUNT_BUCKETS,
        "sourceClass": AUTOCOMPLETE_SOURCE_CLASSES,
        "rankBucket": AUTOCOMPLETE_RANK_BUCKETS,
        "selectionState": AUTOCOMPLETE_SELECTION_STATES,
        "warningReason": AUTOCOMPLETE_WARNING_REASONS,
    },
    "ingredient_product_create_outcome": {
        "surface": AUTOCOMPLETE_SURFACES,
        "outcome": INGREDIENT_PRODUCT_CREATE_OUTCOMES,
    },
    "home_next_action_shown": {
        "actionType": HOME_NEXT_ACTION_TYPES,
        "state": HOME_NEXT_ACTION_STATES,
        "reasonCode": HOME_NEXT_ACTION_REASON_CODES,
        "sourceDomain": HOME_NEXT_ACTION_SOURCE_DOMAINS,
    },
    "home_next_action_started": {
        "actionType": HOME_NEXT_ACTION_TYPES,
        "ownerFlow": HOME_NEXT_ACTION_OWNER_FLOWS,
        "state": HOME_NEXT_ACTION_STATES,
    },
    "home_next_action_dismissed": {
        "actionType": HOME_NEXT_ACTION_TYPES,
        "reasonCode": HOME_NEXT_ACTION_REASON_CODES,
        "cooldownBucket": HOME_NEXT_ACTION_COOLDOWN_BUCKETS,
    },
    "memory_candidate_created": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "confidenceBucket": C5_CONFIDENCE_BUCKETS,
        "featureState": C5_FEATURE_STATES,
    },
    "memory_candidate_confirmed": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "confidenceBucket": C5_CONFIDENCE_BUCKETS,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "memory_candidate_dismissed": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "memory_used": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "memory_muted": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "memory_deleted": {
        "memoryType": C5_MEMORY_TYPES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "planned_meal_created": {
        "sourceType": C5_PLANNED_MEAL_SOURCE_TYPES,
        "estimateState": C5_PLANNED_MEAL_ESTIMATE_STATES,
        "surface": C5_TELEMETRY_SURFACES,
        "featureState": C5_FEATURE_STATES,
    },
    "planned_meal_confirmed": {
        "sourceType": C5_PLANNED_MEAL_SOURCE_TYPES,
        "estimateState": C5_PLANNED_MEAL_ESTIMATE_STATES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "planned_meal_changed": {
        "sourceType": C5_PLANNED_MEAL_SOURCE_TYPES,
        "estimateState": C5_PLANNED_MEAL_ESTIMATE_STATES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
    },
    "planned_meal_skipped": {
        "sourceType": C5_PLANNED_MEAL_SOURCE_TYPES,
        "estimateState": C5_PLANNED_MEAL_ESTIMATE_STATES,
        "surface": C5_TELEMETRY_SURFACES,
        "actionResult": C5_ACTION_RESULTS,
        "featureState": C5_FEATURE_STATES,
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


class TelemetryActorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userId: str | None = Field(default=None, min_length=1, max_length=MAX_ACTOR_ID_LENGTH)
    anonymousId: str | None = Field(
        default=None, min_length=1, max_length=MAX_ACTOR_ID_LENGTH
    )

    @field_validator("userId", "anonymousId", mode="before")
    @classmethod
    def normalize_actor_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def validate_exactly_one_actor(self) -> "TelemetryActorContext":
        if bool(self.userId) == bool(self.anonymousId):
            raise ValueError("Telemetry actor requires exactly one identity")
        return self


class TelemetryEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1, max_length=MAX_EVENT_ID_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_EVENT_NAME_LENGTH)
    ts: datetime
    occurredAt: datetime | None = None
    sessionId: str | None = Field(default=None, min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    actor: TelemetryActorContext | None = None
    platform: str | None = Field(default=None, min_length=1, max_length=MAX_PLATFORM_LENGTH)
    appVersion: str | None = Field(default=None, min_length=1, max_length=MAX_APP_VERSION_LENGTH)
    build: str | None = Field(default=None, max_length=MAX_BUILD_LENGTH)
    locale: str | None = Field(default=None, max_length=MAX_LOCALE_LENGTH)
    timezone: str | None = Field(default=None, min_length=1, max_length=MAX_TIMEZONE_LENGTH)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)
    schemaVersion: int = Field(default=1, ge=1, le=CURRENT_TELEMETRY_SCHEMA_VERSION)
    requestId: str | None = Field(default=None, max_length=MAX_REQUEST_ID_LENGTH)
    props: dict[str, Any] | None = None

    @field_validator(
        "eventId",
        "name",
        "sessionId",
        "platform",
        "appVersion",
        "build",
        "locale",
        "timezone",
        "requestId",
        mode="before",
    )
    @classmethod
    def normalize_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
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

    @model_validator(mode="after")
    def validate_identity_contract(self) -> "TelemetryEventInput":
        if self.occurredAt is None:
            self.occurredAt = self.ts

        if self.schemaVersion >= CURRENT_TELEMETRY_SCHEMA_VERSION:
            missing_fields = [
                field_name
                for field_name, value in (
                    ("sessionId", self.sessionId),
                    ("actor", self.actor),
                    ("platform", self.platform),
                    ("appVersion", self.appVersion),
                    ("locale", self.locale),
                    ("timezone", self.timezone),
                )
                if value is None
            ]
            if missing_fields:
                raise ValueError(
                    "Telemetry v2 event is missing required correlation fields: "
                    + ", ".join(missing_fields)
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

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value not in TELEMETRY_REJECTION_REASONS:
            raise ValueError("Telemetry rejection reason is not allowed")
        return value


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
