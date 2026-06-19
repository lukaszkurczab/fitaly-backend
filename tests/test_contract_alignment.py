"""Cross-repo contract alignment tests.

These tests validate that the canonical JSON fixtures in
``tests/contract_fixtures/`` can be parsed by the backend Pydantic models
and that all enum values match the backend's Literal definitions.

Mirror fixtures live in the mobile repo at
``src/__contract_fixtures__/``.  When a fixture changes, the
corresponding test must break in *both* repos to prevent silent drift.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from collections.abc import Callable
from typing import Any, cast, get_args

import pytest
from pytest_mock import MockerFixture
from pydantic import ValidationError

from app.schemas.barcode import BarcodeLookupFoundResponse
from app.schemas.coach import (
    CoachMeta,
    CoachActionType,
    CoachEmptyReason,
    CoachInsightType,
    CoachResponse,
    CoachSource,
)
from app.schemas.habits import CoachPriority, TopRisk
from app.schemas.food_library import (
    FOOD_LIBRARY_BARCODE_RESULT_OWNERS,
    FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES,
    FOOD_LIBRARY_DOMAIN_CONTRACTS,
    FOOD_LIBRARY_DOMAINS,
    FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS,
    FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL,
    FOOD_LIBRARY_LOGGED_MEAL_OWNER,
    FOOD_LIBRARY_LOGGED_MEAL_SCHEMA,
    FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS,
    INGREDIENT_PRODUCT_ALLERGEN_FLAGS,
    INGREDIENT_PRODUCT_BARCODE_MINIMAL_IDENTITY_FIELDS,
    INGREDIENT_PRODUCT_BARCODE_OPTIONAL_FIELDS,
    INGREDIENT_PRODUCT_CONFIDENCE_FIELDS,
    INGREDIENT_PRODUCT_CONFIDENCE_LEVELS,
    INGREDIENT_PRODUCT_DIETARY_FLAGS,
    INGREDIENT_PRODUCT_KINDS,
    INGREDIENT_PRODUCT_LIFECYCLE_STATES,
    INGREDIENT_PRODUCT_NUTRITION_BASES,
    INGREDIENT_PRODUCT_NUTRITION_OPTIONAL_FIELDS,
    INGREDIENT_PRODUCT_NUTRITION_REQUIRED_FIELDS,
    INGREDIENT_PRODUCT_OPTIONAL_FIELDS,
    INGREDIENT_PRODUCT_PROFILE_COMPATIBILITY_STATUSES,
    INGREDIENT_PRODUCT_RECORD_SCOPES,
    INGREDIENT_PRODUCT_REQUIRED_FIELDS,
    INGREDIENT_PRODUCT_SERVING_REQUIRED_FIELDS,
    INGREDIENT_PRODUCT_SERVING_SIZE_FIELDS,
    INGREDIENT_PRODUCT_SERVING_UNITS,
    INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_OPTIONAL_FIELDS,
    INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_REQUIRED_FIELDS,
    INGREDIENT_PRODUCT_SOURCE_TYPES,
    FoodLibraryDomainsContract,
)
from app.schemas.meal import (
    MealDocument,
    MealInputMethod,
    MealItem,
    MealSource,
    MealSyncState,
    MealType,
    MealUpsertRequest,
)
from app.schemas.media_asset import (
    MEDIA_ASSET_LIFECYCLE_OWNED_FIELDS,
    MEDIA_ASSET_LIFECYCLE_OWNER,
    MEDIA_ASSET_STATES,
    MEDIA_ASSET_SURFACES,
    SAVED_MEAL_PHOTO_LIBRARY_BRIDGE_DOMAINS,
    SAVED_MEAL_PHOTO_LIBRARY_NON_MIGRATION_TARGETS,
    SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN,
    SAVED_MEAL_PHOTO_STABLE_MEDIA_IDENTITY,
    MediaAssetLifecycleContract,
)
from app.schemas.nutrition_state import NutritionStateResponse
from app.schemas.smart_memory import (
    SMART_MEMORY_CANDIDATE_STATES,
    SMART_MEMORY_CENTER_STATES,
    SMART_MEMORY_CONFIDENCE_REASON_CODES,
    SMART_MEMORY_CONTRACT_NAME,
    SMART_MEMORY_PROJECTION_STATES,
    SMART_MEMORY_REVIEW_STATES,
    SMART_MEMORY_SCHEMA_VERSION,
    SMART_MEMORY_STATE_REASON_CODES,
    SMART_MEMORY_STATES,
    SMART_MEMORY_TYPES,
    SMART_MEMORY_USER_CONTROL_OPERATIONS,
    SMART_MEMORY_USER_VALUE_REASON_CODES,
    SmartMemoryCoreContract,
    SmartMemoryItemPatchRequest,
)
from app.schemas.reminders import (
    NOOP_REASON_CODES,
    SEND_REASON_CODES,
    SUPPRESS_REASON_CODES,
    ReminderDecision,
    ReminderDecisionType,
    ReminderKind,
    ReminderReasonCode,
)
from app.schemas.telemetry import (
    ALLOWED_TELEMETRY_EVENT_NAMES,
    ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES,
    ALLOWED_TELEMETRY_EVENT_PROPS,
)
from app.schemas.weekly_reports import (
    WeeklyReportInsightImportance,
    WeeklyReportInsightTone,
    WeeklyReportInsightType,
    WeeklyReportPriorityType,
    WeeklyReportResponse,
    WeeklyReportStatus,
)
from app.services.ai_gateway_service import (
    REJECT_REASON_OFF_TOPIC,
    REJECT_REASON_TOO_SHORT,
)
from app.services.coach_rule_engine import evaluate_coach_insights, select_top_insight
from app.services.coach_service import get_coach_response

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"
MOBILE_FIXTURES_DIR = (
    Path(__file__).resolve().parents[2] / "fitaly" / "src" / "__contract_fixtures__"
)
JSONDict = dict[str, Any]
StringListDict = dict[str, list[str]]
MEDIA_ASSET_DOMAIN_OWNED_URL_FIELDS_FORBIDDEN = {
    "avatarUrl",
    "attachmentUrl",
    "downloadUrl",
    "publicUrl",
    "resolvedDownloadUrl",
}


def _load_fixture(name: str) -> JSONDict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _collect_object_keys(value: object, keys: set[str] | None = None) -> set[str]:
    collected: set[str] = set() if keys is None else keys
    if isinstance(value, dict):
        raw = cast(dict[object, object], value)
        for key, item in raw.items():
            if isinstance(key, str):
                collected.add(key)
            _collect_object_keys(item, collected)
    elif isinstance(value, list):
        raw_items = cast(list[object], value)
        for item in raw_items:
            _collect_object_keys(item, collected)
    return collected


def _load_nutrition_state_fixture_model() -> NutritionStateResponse:
    return NutritionStateResponse.model_validate(_load_fixture("nutrition_state.json"))


def _build_runtime_coach_response_from_state(state: NutritionStateResponse) -> CoachResponse:
    evaluation = evaluate_coach_insights(state)
    return CoachResponse(
        dayKey=state.dayKey,
        computedAt=state.computedAt,
        source="rules",
        insights=evaluation.insights,
        topInsight=select_top_insight(evaluation.insights),
        meta=CoachMeta(
            available=True,
            emptyReason=evaluation.empty_reason,
            isDegraded=state.meta.componentStatus.streak == "error",
        ),
    )


# ---------------------------------------------------------------------------
# Fixture: meal_item.json
# ---------------------------------------------------------------------------


class TestMealItemContract:
    """Canonical meal fixture must parse through both MealItem and MealUpsertRequest."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("meal_item.json")

    def test_meal_item_parses(self, fixture: JSONDict) -> None:
        item = MealItem.model_validate(fixture)
        assert item.id == "meal-contract-1"
        assert item.type == "lunch"
        assert item.syncState == "synced"
        assert item.inputMethod == "photo"
        assert item.source == "ai"
        assert item.totals.kcal == 330.0
        assert len(item.ingredients) == 1
        assert item.ingredients[0].protein == 62.0
        assert item.aiMeta is not None
        assert item.aiMeta.model == "gpt-4o"
        assert item.loggedAt == "2026-03-18T12:00:00.000Z"
        assert item.dayKey == "2026-03-18"
        assert item.loggedAtLocalMin == 780
        assert item.tzOffsetMin == 60
        assert item.imageRef is not None
        assert item.imageRef.imageId == "img-001"
        assert item.deleted is False

    def test_meal_upsert_request_parses(self, fixture: JSONDict) -> None:
        req = MealUpsertRequest.model_validate(
            {**fixture, "clientMutationId": "mutation-contract-meal"}
        )
        assert req.id == "meal-contract-1"
        assert req.type == "lunch"
        assert req.totals is not None
        assert req.totals.protein == 62.0

    def test_meal_day_key_rejects_non_canonical_format(self, fixture: JSONDict) -> None:
        payload = dict(fixture)
        payload["dayKey"] = "2026/03/18"

        with pytest.raises(ValidationError):
            MealUpsertRequest.model_validate(payload)

    def test_fixture_round_trips_through_serialization(self, fixture: JSONDict) -> None:
        """Parse → serialize → parse must be stable."""
        item = MealItem.model_validate(fixture)
        serialized = item.model_dump(mode="json")
        reparsed = MealItem.model_validate(serialized)
        assert reparsed.id == item.id
        assert reparsed.totals.kcal == item.totals.kcal
        assert reparsed.ingredients[0].protein == item.ingredients[0].protein


# ---------------------------------------------------------------------------
# Fixture: nutrition_state.json
# ---------------------------------------------------------------------------


class TestNutritionStateContract:
    """Canonical nutrition state fixture must parse through NutritionStateResponse."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("nutrition_state.json")

    def test_response_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.dayKey == "2026-03-18"
        assert state.targets.kcal == 2200.0
        assert state.consumed.protein == 98.0
        assert state.remaining.carbs == 90.0
        assert state.overTarget.kcal == 0.0
        assert state.quality.mealsLogged == 3
        assert state.quality.dataCompletenessScore == 1.0

    def test_habits_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.habits.available is True
        assert state.habits.behavior.loggingDays7 == 5
        assert state.habits.behavior.validLoggingDays7 == 4
        assert state.habits.behavior.loggingConsistency28 == 0.75
        assert state.habits.behavior.validLoggingConsistency28 == 0.61
        assert state.habits.behavior.avgValidMealsPerValidLoggedDay14 == 2.5
        assert state.habits.behavior.mealTypeCoverage14.coveredCount == 3
        assert state.habits.behavior.mealTypeFrequency14.lunch == 5
        assert state.habits.behavior.dayCoverage14.validLoggedDays == 8
        assert state.habits.behavior.proteinDaysHit14.ratio == 0.67
        assert state.habits.behavior.timingPatterns14.available is True
        assert state.habits.behavior.timingPatterns14.firstMealMedianHour == 8.25
        assert state.habits.topRisk == "none"
        assert state.habits.coachPriority == "maintain"
        assert state.habits.dataQuality.daysUsingTimestampTimingFallback14 == 2

    def test_streak_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.streak.available is True
        assert state.streak.current == 5
        assert state.streak.lastDate == "2026-03-18"

    def test_ai_summary_parses(self, fixture: JSONDict) -> None:
        state = NutritionStateResponse.model_validate(fixture)
        assert state.ai.available is True
        assert state.ai.tier == "free"
        assert state.ai.balance == 85
        assert state.ai.costs.chat == 1
        assert state.ai.costs.photo == 5
        assert state.meta.isDegraded is False
        assert state.meta.componentStatus.habits == "ok"

    def test_fixture_top_level_keys_match_schema(self, fixture: JSONDict) -> None:
        """Fixture must contain exactly the fields NutritionStateResponse declares."""
        expected_keys = set(NutritionStateResponse.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )


# ---------------------------------------------------------------------------
# Fixture: coach_response.json
# ---------------------------------------------------------------------------


class TestCoachResponseContract:
    """Canonical coach response fixture must parse through CoachResponse."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("coach_response.json")

    def test_response_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.dayKey == "2026-03-18"
        assert response.computedAt == "2026-03-18T12:00:00Z"
        assert response.source == "rules"
        assert len(response.insights) == 1
        assert response.meta.available is True
        assert response.meta.emptyReason is None
        assert response.meta.isDegraded is False

    def test_top_insight_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.topInsight is not None
        assert response.topInsight.id == "2026-03-18:positive_momentum"
        assert response.topInsight.type == "positive_momentum"
        assert response.topInsight.actionType == "open_chat"
        assert response.topInsight.reasonCodes == [
            "streak_positive",
            "consistency_improving",
        ]
        assert response.topInsight.validUntil == "2026-03-18T23:59:59Z"
        assert response.topInsight.confidence == 0.74
        assert response.topInsight.isPositive is True

    def test_fixture_matches_runtime_rule_engine_output(
        self,
        fixture: JSONDict,
    ) -> None:
        state = _load_nutrition_state_fixture_model()
        evaluation = evaluate_coach_insights(state)
        top_insight = select_top_insight(evaluation.insights)

        assert {
            "insights": [insight.model_dump(mode="json") for insight in evaluation.insights],
            "topInsight": (
                top_insight.model_dump(mode="json") if top_insight is not None else None
            ),
            "meta": {
                "available": True,
                "emptyReason": evaluation.empty_reason,
                "isDegraded": state.meta.componentStatus.streak == "error",
            },
        } == {
            "insights": fixture["insights"],
            "topInsight": fixture["topInsight"],
            "meta": fixture["meta"],
        }

    def test_fixture_matches_runtime_coach_response_output(
        self,
        fixture: JSONDict,
        mocker: MockerFixture,
    ) -> None:
        state = _load_nutrition_state_fixture_model()
        mocker.patch(
            "app.services.coach_service.get_nutrition_state",
            return_value=state,
        )

        response = asyncio.run(get_coach_response("user-contract-1", day_key=state.dayKey))

        assert response.model_dump(mode="json") == fixture

    def test_runtime_helper_matches_fixture(self, fixture: JSONDict) -> None:
        state = _load_nutrition_state_fixture_model()
        response = _build_runtime_coach_response_from_state(state)

        assert response.model_dump(mode="json") == fixture

    def test_single_insight_fixture_parses(self, fixture: JSONDict) -> None:
        response = CoachResponse.model_validate(fixture)
        assert response.insights == [response.topInsight]
        assert response.insights[0].id == "2026-03-18:positive_momentum"
        assert response.insights[0].validUntil == "2026-03-18T23:59:59Z"

    def test_fixture_top_level_keys_match_schema(self, fixture: JSONDict) -> None:
        expected_keys = set(CoachResponse.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )


# ---------------------------------------------------------------------------
# Fixture: reminder_decision.json
# ---------------------------------------------------------------------------


class TestReminderDecisionContract:
    """Canonical reminder decision fixtures must parse through ReminderDecision."""

    @pytest.fixture()
    def send_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision.json")

    @pytest.fixture()
    def suppress_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision_suppress.json")

    @pytest.fixture()
    def noop_fixture(self) -> JSONDict:
        return _load_fixture("reminder_decision_noop.json")

    def test_send_response_parses(self, send_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(send_fixture)
        assert decision.dayKey == "2026-03-18"
        assert decision.computedAt == "2026-03-18T12:00:00Z"
        assert decision.decision == "send"
        assert decision.kind == "log_next_meal"
        assert decision.reasonCodes == [
            "preferred_window_today",
            "day_partially_logged",
        ]
        assert decision.scheduledAtUtc == "2026-03-18T18:30:00Z"
        assert decision.confidence == 0.84
        assert decision.validUntil == "2026-03-18T19:30:00Z"

    def test_suppress_response_parses(self, suppress_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(suppress_fixture)
        assert decision.decision == "suppress"
        assert decision.kind is None
        assert decision.scheduledAtUtc is None
        assert decision.reasonCodes == ["quiet_hours"]
        assert decision.confidence == 1.0

    def test_noop_response_parses(self, noop_fixture: JSONDict) -> None:
        decision = ReminderDecision.model_validate(noop_fixture)
        assert decision.decision == "noop"
        assert decision.kind is None
        assert decision.scheduledAtUtc is None
        assert decision.reasonCodes == ["insufficient_signal"]
        assert decision.confidence == 0.65

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "reminder_decision.json",
            "reminder_decision_suppress.json",
            "reminder_decision_noop.json",
        ],
    )
    def test_fixture_round_trips_through_serialization(self, fixture_name: str) -> None:
        fixture = _load_fixture(fixture_name)
        decision = ReminderDecision.model_validate(fixture)
        serialized = decision.model_dump(mode="json")
        reparsed = ReminderDecision.model_validate(serialized)
        assert reparsed == decision

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "reminder_decision.json",
            "reminder_decision_suppress.json",
            "reminder_decision_noop.json",
        ],
    )
    def test_fixture_top_level_keys_match_schema(self, fixture_name: str) -> None:
        fixture = _load_fixture(fixture_name)
        expected_keys = set(ReminderDecision.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys, (
            f"Fixture keys drift. "
            f"Missing from fixture: {expected_keys - actual_keys}. "
            f"Extra in fixture: {actual_keys - expected_keys}."
        )

    def test_send_requires_kind_and_schedule(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "reasonCodes": ["preferred_window_open"],
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_noop_rejects_kind_and_schedule(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "noop",
                    "kind": "complete_day",
                    "reasonCodes": ["insufficient_signal"],
                    "scheduledAtUtc": "2026-03-18T20:00:00Z",
                    "confidence": 0.6,
                    "validUntil": "2026-03-18T23:59:59Z",
                }
            )

    def test_suppress_rejects_kind(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "suppress",
                    "kind": "log_next_meal",
                    "reasonCodes": ["quiet_hours"],
                    "confidence": 1.0,
                    "validUntil": "2026-03-18T23:59:59Z",
                }
            )

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("dayKey", "2026/03/18"),
            ("computedAt", "2026-03-18T12:00:00+00:00"),
            ("scheduledAtUtc", "2026-03-18T18:30:00+00:00"),
            ("validUntil", "2026-03-18T19:30:00.000Z"),
        ],
    )
    def test_rejects_non_canonical_date_time_formats(
        self,
        field_name: str,
        value: str,
    ) -> None:
        payload: dict[str, Any] = {
            "dayKey": "2026-03-18",
            "computedAt": "2026-03-18T12:00:00Z",
            "decision": "send",
            "kind": "log_next_meal",
            "reasonCodes": ["preferred_window_open"],
            "scheduledAtUtc": "2026-03-18T18:30:00Z",
            "confidence": 0.84,
            "validUntil": "2026-03-18T19:30:00Z",
        }
        payload[field_name] = value

        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(payload)

    def test_rejects_scheduled_at_utc_earlier_than_computed_at(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "kind": "log_next_meal",
                    "reasonCodes": ["preferred_window_open"],
                    "scheduledAtUtc": "2026-03-18T11:59:59Z",
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_rejects_scheduled_at_utc_later_than_valid_until(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "send",
                    "kind": "log_next_meal",
                    "reasonCodes": ["preferred_window_open"],
                    "scheduledAtUtc": "2026-03-18T19:30:01Z",
                    "confidence": 0.84,
                    "validUntil": "2026-03-18T19:30:00Z",
                }
            )

    def test_rejects_valid_until_earlier_than_computed_at(self) -> None:
        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(
                {
                    "dayKey": "2026-03-18",
                    "computedAt": "2026-03-18T12:00:00Z",
                    "decision": "noop",
                    "reasonCodes": ["insufficient_signal"],
                    "confidence": 0.65,
                    "validUntil": "2026-03-18T11:59:59Z",
                }
            )

    @pytest.mark.parametrize(
        ("decision_type", "reason_codes"),
        [
            ("send", ["quiet_hours"]),
            ("suppress", ["preferred_window_open"]),
            ("noop", ["already_logged_recently"]),
        ],
    )
    def test_rejects_reason_codes_not_allowed_for_decision(
        self,
        decision_type: str,
        reason_codes: list[str],
    ) -> None:
        payload: dict[str, Any] = {
            "dayKey": "2026-03-18",
            "computedAt": "2026-03-18T12:00:00Z",
            "decision": decision_type,
            "kind": "log_next_meal" if decision_type == "send" else None,
            "reasonCodes": reason_codes,
            "scheduledAtUtc": "2026-03-18T18:30:00Z" if decision_type == "send" else None,
            "confidence": 0.84 if decision_type == "send" else 1.0,
            "validUntil": "2026-03-18T19:30:00Z",
        }

        with pytest.raises(ValidationError):
            ReminderDecision.model_validate(payload)


# ---------------------------------------------------------------------------
# Fixture: smart_reminder_telemetry.json
# ---------------------------------------------------------------------------


class TestSmartReminderTelemetryContract:
    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("smart_reminder_telemetry.json")

    def test_event_names_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "smart_reminder_suppressed",
            "smart_reminder_scheduled",
            "smart_reminder_noop",
            "smart_reminder_decision_failed",
            "smart_reminder_schedule_failed",
        }
        assert set(fixture["eventNames"]) == expected
        assert expected.issubset(ALLOWED_TELEMETRY_EVENT_NAMES)

    def test_props_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "smart_reminder_suppressed": {
                "decision",
                "suppressionReason",
                "confidenceBucket",
            },
            "smart_reminder_scheduled": {
                "reminderKind",
                "decision",
                "confidenceBucket",
                "scheduledWindow",
            },
            "smart_reminder_noop": {
                "decision",
                "noopReason",
                "confidenceBucket",
            },
            "smart_reminder_decision_failed": {
                "failureReason",
            },
            "smart_reminder_schedule_failed": {
                "reminderKind",
                "decision",
                "confidenceBucket",
                "failureReason",
            },
        }
        assert set(fixture["propsByEvent"].keys()) == set(expected.keys())
        for event_name, prop_names in expected.items():
            assert set(fixture["propsByEvent"][event_name]) == prop_names
            assert ALLOWED_TELEMETRY_EVENT_PROPS[event_name] == frozenset(prop_names)

    def test_disallowed_event_names_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        for event_name in fixture["disallowedEventNames"]:
            assert event_name not in ALLOWED_TELEMETRY_EVENT_NAMES


# ---------------------------------------------------------------------------
# Fixture: autocomplete_telemetry.json
# ---------------------------------------------------------------------------


class TestAutocompleteTelemetryContract:
    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("autocomplete_telemetry.json")

    def test_event_names_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "autocomplete_search_outcome",
            "autocomplete_result_selected",
            "ingredient_product_create_outcome",
        }
        assert set(fixture["eventNames"]) == expected
        assert expected.issubset(ALLOWED_TELEMETRY_EVENT_NAMES)

    def test_props_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "autocomplete_search_outcome": {
                "surface",
                "outcome",
                "queryLengthBucket",
                "resultCountBucket",
                "sourceClass",
                "latencyBucket",
                "warningReason",
            },
            "autocomplete_result_selected": {
                "surface",
                "resultCountBucket",
                "sourceClass",
                "rankBucket",
                "selectionState",
                "warningReason",
            },
            "ingredient_product_create_outcome": {
                "surface",
                "outcome",
            },
        }
        assert set(fixture["propsByEvent"].keys()) == set(expected.keys())
        for event_name, prop_names in expected.items():
            assert set(fixture["propsByEvent"][event_name]) == prop_names
            assert ALLOWED_TELEMETRY_EVENT_PROPS[event_name] == frozenset(prop_names)

    def test_disallowed_event_names_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        for event_name in fixture["disallowedEventNames"]:
            assert event_name not in ALLOWED_TELEMETRY_EVENT_NAMES

    def test_disallowed_props_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        allowed_props: set[str] = set()
        for prop_names in ALLOWED_TELEMETRY_EVENT_PROPS.values():
            allowed_props.update(prop_names)
        for prop_name in fixture["disallowedPropNames"]:
            assert prop_name not in allowed_props


# ---------------------------------------------------------------------------
# Fixture: home_next_action_telemetry.json
# ---------------------------------------------------------------------------


class TestHomeNextActionTelemetryContract:
    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("home_next_action_telemetry.json")

    def test_event_names_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "home_next_action_shown",
            "home_next_action_started",
            "home_next_action_dismissed",
        }
        assert set(fixture["eventNames"]) == expected
        assert expected.issubset(ALLOWED_TELEMETRY_EVENT_NAMES)

    def test_props_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "home_next_action_shown": {
                "actionType",
                "state",
                "reasonCode",
                "sourceDomain",
            },
            "home_next_action_started": {
                "actionType",
                "ownerFlow",
                "state",
            },
            "home_next_action_dismissed": {
                "actionType",
                "reasonCode",
                "cooldownBucket",
            },
        }
        assert set(fixture["propsByEvent"].keys()) == set(expected.keys())
        for event_name, prop_names in expected.items():
            assert set(fixture["propsByEvent"][event_name]) == prop_names
            assert ALLOWED_TELEMETRY_EVENT_PROPS[event_name] == frozenset(prop_names)

    def test_enum_values_match_backend_allowlist(self, fixture: JSONDict) -> None:
        expected = {
            "home_next_action_shown": {
                "actionType": {"continue_review", "continue_planned_item"},
                "state": {"eligible"},
                "reasonCode": {"review_draft_available", "planned_item_due"},
                "sourceDomain": {"review_draft", "planned_meal"},
            },
            "home_next_action_started": {
                "actionType": {"continue_review", "continue_planned_item"},
                "ownerFlow": {"ReviewMeal", "Planning"},
                "state": {"eligible"},
            },
            "home_next_action_dismissed": {
                "actionType": {"continue_review", "continue_planned_item"},
                "reasonCode": {"review_draft_available", "planned_item_due"},
                "cooldownBucket": {"24h"},
            },
        }

        raw_enum_values = cast(JSONDict, fixture["enumValuesByEvent"])
        assert set(raw_enum_values.keys()) == set(expected.keys())
        for event_name, prop_values in expected.items():
            event_values = cast(dict[str, list[str]], raw_enum_values[event_name])
            assert set(event_values.keys()) == set(prop_values.keys())
            for prop_name, values in prop_values.items():
                assert set(event_values[prop_name]) == values
                assert ALLOWED_TELEMETRY_EVENT_PROP_ENUM_VALUES[event_name][
                    prop_name
                ] == frozenset(values)

    def test_disallowed_event_names_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        for event_name in fixture["disallowedEventNames"]:
            assert event_name not in ALLOWED_TELEMETRY_EVENT_NAMES

    def test_disallowed_props_stay_out_of_allowlist(self, fixture: JSONDict) -> None:
        allowed_props: set[str] = set()
        for prop_names in ALLOWED_TELEMETRY_EVENT_PROPS.values():
            allowed_props.update(prop_names)
        for prop_name in fixture["disallowedPropNames"]:
            assert prop_name not in allowed_props


# ---------------------------------------------------------------------------
# Fixture: gateway_reject.json
# ---------------------------------------------------------------------------


class TestGatewayRejectContract:
    """Canonical gateway reject fixture matches route HTTP 400 shape."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("gateway_reject.json")

    def test_reject_detail_shape(self, fixture: JSONDict) -> None:
        detail = fixture["detail"]
        assert detail["message"] == "AI request blocked by gateway"
        assert detail["code"] == "AI_GATEWAY_BLOCKED"
        assert isinstance(detail["reason"], str)
        assert isinstance(detail["score"], (int, float))

    def test_reject_reason_is_canonical(self, fixture: JSONDict) -> None:
        """The reason in the fixture must be one of the backend's canonical constants."""
        canonical_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert fixture["detail"]["reason"] in canonical_reasons


# ---------------------------------------------------------------------------
# Fixture: ai_rejections.json
# ---------------------------------------------------------------------------


class TestAiRejectionContract:
    """Canonical AI rejection fixtures match product-safe HTTP error shapes."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("ai_rejections.json")

    def test_consent_required_rejection_shape(self, fixture: JSONDict) -> None:
        rejection = fixture["rejections"]["consentRequired"]
        detail = rejection["detail"]
        ai_consent = detail["aiConsent"]

        assert rejection["status"] == 403
        assert detail["code"] == "AI_CONSENT_REQUIRED"
        assert detail["code"] != "_".join(["AI", "CHAT", "CONSENT", "REQUIRED"])
        assert detail["message"] == "AI health data consent required."
        assert ai_consent["required"] is True
        assert ai_consent["scope"] == "global_ai_health_data"

    def test_meal_analysis_disabled_rejection_shape(self, fixture: JSONDict) -> None:
        rejection = fixture["rejections"]["mealAnalysisDisabled"]
        detail = rejection["detail"]

        assert rejection["status"] == 503
        assert detail["code"] == "AI_MEAL_ANALYSIS_DISABLED"
        assert detail["message"] == "Meal analysis AI is temporarily disabled."
        assert "aiConsent" not in detail

    def test_meal_analysis_idempotency_conflict_rejection_shape(
        self,
        fixture: JSONDict,
    ) -> None:
        rejection = fixture["rejections"]["mealAnalysisIdempotencyConflict"]
        detail = rejection["detail"]

        assert rejection["status"] == 409
        assert detail["code"] == "AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT"
        assert detail["message"] == (
            "Meal analysis request is already in progress or completed."
        )
        assert "aiConsent" not in detail

    def test_provider_unavailable_rejection_shape(self, fixture: JSONDict) -> None:
        rejection = fixture["rejections"]["providerUnavailable"]
        detail = rejection["detail"]

        assert rejection["status"] == 503
        assert detail["code"] == "AI_CHAT_PROVIDER_UNAVAILABLE"
        assert detail["message"] == "AI provider is temporarily unavailable."
        assert "OpenAI" not in detail["message"]
        assert "aiConsent" not in detail

    def test_provider_timeout_rejection_shape(self, fixture: JSONDict) -> None:
        rejection = fixture["rejections"]["providerTimeout"]
        detail = rejection["detail"]

        assert rejection["status"] == 504
        assert detail["code"] == "AI_CHAT_TIMEOUT"
        assert detail["message"] == "AI provider timed out before a response was generated."
        assert "OpenAI" not in detail["message"]
        assert "aiConsent" not in detail

    def test_credits_exhausted_rejection_shape(self, fixture: JSONDict) -> None:
        rejection = fixture["rejections"]["creditsExhausted"]
        detail = rejection["detail"]
        credits = detail["credits"]

        assert rejection["status"] == 402
        assert detail["code"] == "AI_CREDITS_EXHAUSTED"
        assert detail["message"] == "AI credits exhausted."
        assert "aiConsent" not in detail
        assert set(credits.keys()) >= {
            "userId",
            "tier",
            "balance",
            "allocation",
            "periodStartAt",
            "periodEndAt",
            "costs",
            "renewalAnchorSource",
            "revenueCatEntitlementId",
            "revenueCatExpirationAt",
            "lastRevenueCatEventId",
        }
        assert credits["userId"] == "user-1"
        assert credits["tier"] == "free"
        assert credits["balance"] == 0
        assert credits["allocation"] == 100
        assert credits["periodStartAt"] == "2026-04-19T00:00:00Z"
        assert credits["periodEndAt"] == "2026-05-19T00:00:00Z"
        assert credits["costs"] == {
            "chat": 1,
            "textMeal": 1,
            "photo": 5,
        }


# ---------------------------------------------------------------------------
# Fixture: weekly_report.json
# ---------------------------------------------------------------------------


class TestWeeklyReportContract:
    """Canonical weekly report fixture must parse through WeeklyReportResponse."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("weekly_report.json")

    def test_response_parses(self, fixture: JSONDict) -> None:
        report = WeeklyReportResponse.model_validate(fixture)

        assert report.status == "ready"
        assert report.period.startDay == "2026-03-09"
        assert report.period.endDay == "2026-03-15"
        assert report.summary is not None
        assert "Logging stayed steady across the week." in report.summary
        assert len(report.insights) == 1
        assert len(report.priorities) == 1
        assert report.insights[0].type == "consistency"
        assert report.priorities[0].type == "maintain_consistency"

    def test_top_level_keys_match_schema(self, fixture: JSONDict) -> None:
        expected_keys = set(WeeklyReportResponse.model_fields.keys())
        actual_keys = set(fixture.keys())
        assert actual_keys == expected_keys

    def test_fixture_values_match_backend_literals(self, fixture: JSONDict) -> None:
        report = WeeklyReportResponse.model_validate(fixture)

        assert report.status in get_args(WeeklyReportStatus)
        assert len(report.insights) <= 4
        assert len(report.priorities) <= 2

        for insight in report.insights:
            assert insight.type in get_args(WeeklyReportInsightType)
            assert insight.importance in get_args(WeeklyReportInsightImportance)
            assert insight.tone in get_args(WeeklyReportInsightTone)

        for priority in report.priorities:
            assert priority.type in get_args(WeeklyReportPriorityType)


# ---------------------------------------------------------------------------
# Fixture: enums.json — enum value parity
# ---------------------------------------------------------------------------


class TestEnumParity:
    """Enum values in the fixture must exactly match backend Literal definitions."""

    @pytest.fixture()
    def enums(self) -> StringListDict:
        return _load_fixture("enums.json")

    def test_meal_type_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealType"]) == sorted(get_args(MealType))

    def test_meal_sync_state_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealSyncState"]) == sorted(get_args(MealSyncState))

    def test_meal_input_method_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["MealInputMethod"]) == sorted(get_args(MealInputMethod))

    def test_meal_source_parity(self, enums: StringListDict) -> None:
        # MealSource is Literal["ai", "manual", "saved"] | None — extract the Literal part.
        literal_args = get_args(MealSource)
        # The union is (Literal[...], None); extract from Literal.
        source_values: list[str] = []
        for arg in literal_args:
            inner = get_args(arg)
            if inner:
                source_values.extend(value for value in inner if isinstance(value, str))
        assert sorted(enums["MealSource"]) == sorted(source_values)

    def test_gateway_reject_reasons_parity(self, enums: StringListDict) -> None:
        backend_reasons = {REJECT_REASON_OFF_TOPIC, REJECT_REASON_TOO_SHORT}
        assert sorted(enums["GatewayRejectReasons"]) == sorted(backend_reasons)

    def test_top_risk_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["TopRisk"]) == sorted(get_args(TopRisk))

    def test_coach_priority_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["CoachPriority"]) == sorted(get_args(CoachPriority))

    def test_ai_tier_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["AiTier"]) == sorted(["free", "premium"])

    def test_reminder_decision_type_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderDecisionType"]) == sorted(
            get_args(ReminderDecisionType)
        )

    def test_reminder_kind_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderKind"]) == sorted(get_args(ReminderKind))

    def test_reminder_reason_code_parity(self, enums: StringListDict) -> None:
        assert sorted(enums["ReminderReasonCode"]) == sorted(
            get_args(ReminderReasonCode)
        )


# ---------------------------------------------------------------------------
# Fixture: food_library_domains_v1.json
# ---------------------------------------------------------------------------


class TestFoodLibraryDomainsContract:
    """CH-06 food-library domain split must stay separate from logged Meal."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("food_library_domains_v1.json")

    def test_fixture_uses_exact_product_contract_keys(self, fixture: JSONDict) -> None:
        assert set(fixture.keys()) == {
            "contract",
            "libraryDomains",
            "domainContracts",
            "ingredientProductRecordContract",
            "loggedMealBoundary",
            "currentSavedMealsBoundary",
            "barcodeBoundary",
        }

        record_contract = cast(
            dict[str, object],
            fixture["ingredientProductRecordContract"],
        )
        assert set(record_contract.keys()) == {
            "recordKinds",
            "recordScopes",
            "lifecycleStates",
            "verifiedMeaning",
            "requiredFields",
            "optionalFields",
            "kindSpecificRequiredFields",
            "ownership",
            "sourceAttribution",
            "confidence",
            "nutritionPer100",
            "serving",
            "profileFlags",
            "barcodeIdentities",
            "localCacheBoundary",
        }
        assert set(cast(dict[str, object], record_contract["ownership"])) == {
            "scopeField",
            "ownerField",
            "userScopedScope",
            "userScopedRequiresOwnerUserId",
            "globalScopesMustNotUseOwnerUserId",
            "globalRecordsAreUserAccountData",
        }
        assert set(cast(dict[str, object], record_contract["sourceAttribution"])) == {
            "requiredFields",
            "optionalFields",
            "sourceTypes",
            "candidateOnlySourceTypes",
            "durableTruthRequiresNonAiSource",
        }
        assert set(cast(dict[str, object], record_contract["confidence"])) == {
            "requiredFields",
            "levels",
            "unknownMeansNotSafeToAssume",
        }
        assert set(cast(dict[str, object], record_contract["nutritionPer100"])) == {
            "requiredFields",
            "optionalFields",
            "allowedBases",
            "missingNutritionPolicy",
            "runtimeAiMayBecomeDurableNutritionTruth",
        }
        assert set(cast(dict[str, object], record_contract["profileFlags"])) == {
            "requiredFields",
            "allowedDietaryFlags",
            "allowedAllergenFlags",
            "compatibilityStatuses",
            "missingProfilePolicy",
            "verifiedIsMedicalOrDietarySafetyClaim",
            "runtimeAiMayBecomeDurableProfileTruth",
        }

    def test_contract_parses_and_declares_exact_library_domains(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)

        assert contract.contract == "food_library_domains_v1"
        assert tuple(contract.libraryDomains) == FOOD_LIBRARY_DOMAINS
        assert contract.libraryDomains == [
            "MealTemplate",
            "Recipe",
            "Ingredient/Product",
            "ShoppingList",
        ]

    def test_domain_contracts_declare_exact_domain_owned_fields(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)

        assert tuple(contract.domainContracts.keys()) == FOOD_LIBRARY_DOMAINS
        for domain, expected in FOOD_LIBRARY_DOMAIN_CONTRACTS.items():
            expected_owner, expected_identity_fields, expected_owned_fields = expected
            domain_contract = contract.domainContracts[domain]

            assert domain_contract.owner == expected_owner
            assert tuple(domain_contract.identityFields) == expected_identity_fields
            assert tuple(domain_contract.ownedFields) == expected_owned_fields

    def test_ingredient_product_contract_defines_exact_foundation_fields(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        product_contract = contract.ingredientProductRecordContract

        assert tuple(product_contract.recordKinds) == INGREDIENT_PRODUCT_KINDS
        assert tuple(product_contract.recordScopes) == INGREDIENT_PRODUCT_RECORD_SCOPES
        assert tuple(product_contract.lifecycleStates) == (
            INGREDIENT_PRODUCT_LIFECYCLE_STATES
        )
        assert product_contract.verifiedMeaning == (
            "verified_for_fitaly_catalog_use_not_medical_or_dietary_safety_claim"
        )
        assert tuple(product_contract.requiredFields) == (
            INGREDIENT_PRODUCT_REQUIRED_FIELDS
        )
        assert tuple(product_contract.optionalFields) == (
            INGREDIENT_PRODUCT_OPTIONAL_FIELDS
        )
        assert product_contract.kindSpecificRequiredFields.generic_ingredient == [
            "ingredientName"
        ]
        assert product_contract.kindSpecificRequiredFields.branded_product == [
            "brandName"
        ]
        assert product_contract.ownership.scopeField == "recordScope"
        assert product_contract.ownership.ownerField == "ownerUserId"
        assert product_contract.ownership.userScopedScope == "user_scoped"
        assert product_contract.ownership.userScopedRequiresOwnerUserId is True
        assert product_contract.ownership.globalScopesMustNotUseOwnerUserId == [
            "global_seed",
            "global_internal",
        ]
        assert product_contract.ownership.globalRecordsAreUserAccountData is False

    def test_ingredient_product_contract_enforces_source_confidence_and_no_guessing(
        self,
        fixture: JSONDict,
    ) -> None:
        product_contract = FoodLibraryDomainsContract.model_validate(
            fixture
        ).ingredientProductRecordContract

        assert tuple(product_contract.sourceAttribution.sourceTypes) == (
            INGREDIENT_PRODUCT_SOURCE_TYPES
        )
        assert product_contract.sourceAttribution.requiredFields == [
            *INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_REQUIRED_FIELDS,
        ]
        assert product_contract.sourceAttribution.optionalFields == [
            *INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_OPTIONAL_FIELDS,
        ]
        assert product_contract.sourceAttribution.candidateOnlySourceTypes == [
            "barcode_identity",
            "runtime_ai_candidate",
        ]
        assert (
            product_contract.sourceAttribution.durableTruthRequiresNonAiSource
            is True
        )
        assert tuple(product_contract.confidence.levels) == (
            INGREDIENT_PRODUCT_CONFIDENCE_LEVELS
        )
        assert product_contract.confidence.requiredFields == [
            *INGREDIENT_PRODUCT_CONFIDENCE_FIELDS,
        ]
        assert product_contract.confidence.unknownMeansNotSafeToAssume is True
        assert product_contract.nutritionPer100.missingNutritionPolicy == (
            "unknown_not_guessed"
        )
        assert (
            product_contract.nutritionPer100.runtimeAiMayBecomeDurableNutritionTruth
            is False
        )
        assert product_contract.profileFlags.missingProfilePolicy == (
            "unknown_not_guessed"
        )
        assert (
            product_contract.profileFlags.runtimeAiMayBecomeDurableProfileTruth
            is False
        )

    def test_ingredient_product_contract_enforces_data_boundaries(
        self,
        fixture: JSONDict,
    ) -> None:
        product_contract = FoodLibraryDomainsContract.model_validate(
            fixture
        ).ingredientProductRecordContract

        assert tuple(product_contract.nutritionPer100.allowedBases) == (
            INGREDIENT_PRODUCT_NUTRITION_BASES
        )
        assert product_contract.nutritionPer100.requiredFields == [
            *INGREDIENT_PRODUCT_NUTRITION_REQUIRED_FIELDS,
        ]
        assert product_contract.nutritionPer100.optionalFields == [
            *INGREDIENT_PRODUCT_NUTRITION_OPTIONAL_FIELDS,
        ]
        assert tuple(product_contract.serving.allowedUnits) == (
            INGREDIENT_PRODUCT_SERVING_UNITS
        )
        assert product_contract.serving.requiredFields == [
            *INGREDIENT_PRODUCT_SERVING_REQUIRED_FIELDS,
        ]
        assert product_contract.serving.servingSizeFields == [
            *INGREDIENT_PRODUCT_SERVING_SIZE_FIELDS,
        ]
        assert tuple(product_contract.profileFlags.allowedDietaryFlags) == (
            INGREDIENT_PRODUCT_DIETARY_FLAGS
        )
        assert tuple(product_contract.profileFlags.allowedAllergenFlags) == (
            INGREDIENT_PRODUCT_ALLERGEN_FLAGS
        )
        assert tuple(product_contract.profileFlags.compatibilityStatuses) == (
            INGREDIENT_PRODUCT_PROFILE_COMPATIBILITY_STATUSES
        )
        assert (
            product_contract.profileFlags.verifiedIsMedicalOrDietarySafetyClaim
            is False
        )
        assert product_contract.barcodeIdentities.minimalIdentityFields == [
            *INGREDIENT_PRODUCT_BARCODE_MINIMAL_IDENTITY_FIELDS,
        ]
        assert product_contract.barcodeIdentities.optionalFields == [
            *INGREDIENT_PRODUCT_BARCODE_OPTIONAL_FIELDS,
        ]
        assert product_contract.barcodeIdentities.noCatalogWriteInThisSlice is True
        assert product_contract.barcodeIdentities.noTopLevelAddMealBarcodePath is True
        assert product_contract.localCacheBoundary.representedAs == "projection_only"
        assert product_contract.localCacheBoundary.localCacheIsTruth is False
        assert (
            product_contract.localCacheBoundary.mayPromoteToGlobalWithoutReview
            is False
        )

    def test_meal_template_contract_excludes_logged_meal_only_fields(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        template_contract = contract.domainContracts["MealTemplate"]
        template_fields = {
            *template_contract.identityFields,
            *template_contract.ownedFields,
        }

        assert template_fields.isdisjoint(
            FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS
        )

    def test_logged_meal_boundary_is_narrow_and_not_library_catch_all(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        boundary = contract.loggedMealBoundary

        assert boundary.owner == FOOD_LIBRARY_LOGGED_MEAL_OWNER
        assert boundary.schemaName == FOOD_LIBRARY_LOGGED_MEAL_SCHEMA
        assert boundary.mustRemainNarrow is True
        assert boundary.mustNotServeAsLibraryCatchAll is True
        assert tuple(boundary.mustNotGainFields) == (
            FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        )
        assert "persisted eaten-meal schema" in boundary.rationale

    def test_current_saved_meals_are_not_final_library_foundation(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        boundary = contract.currentSavedMealsBoundary

        assert tuple(boundary.currentNames) == FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES
        assert boundary.isFinalLibraryFoundation is False
        assert boundary.laterTargetDomain == "MealTemplate"
        assert boundary.compatibilityFallbackToOldShapeAccepted is False
        assert tuple(boundary.legacyMarkersNotCanonicalLibraryFoundation) == (
            FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL
        )
        assert tuple(boundary.mustNotExpandWith) == (
            FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        )

    def test_barcode_boundary_is_backend_adapter_draft_source_only(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        boundary = contract.barcodeBoundary

        assert tuple(boundary.resultOwnership) == FOOD_LIBRARY_BARCODE_RESULT_OWNERS
        assert boundary.addMealDraftSourceOnly is True
        assert boundary.createsFirstPartyProductCatalogInThisSlice is False
        assert boundary.mustNotWriteLibraryDomains == ["Ingredient/Product"]

    def test_logged_meal_models_do_not_include_forbidden_library_fields(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = FoodLibraryDomainsContract.model_validate(fixture)
        forbidden_fields = set(contract.loggedMealBoundary.mustNotGainFields)

        assert forbidden_fields.isdisjoint(MealDocument.model_fields)
        assert forbidden_fields.isdisjoint(MealUpsertRequest.model_fields)

    def test_rejects_extra_fields(self, fixture: JSONDict) -> None:
        payload = json.loads(json.dumps(fixture))
        payload["loggedMealBoundary"]["templateFieldsAllowed"] = False

        with pytest.raises(ValidationError):
            FoodLibraryDomainsContract.model_validate(payload)

    def test_rejects_missing_domain_contract(self, fixture: JSONDict) -> None:
        payload = json.loads(json.dumps(fixture))
        del payload["domainContracts"]["ShoppingList"]

        with pytest.raises(ValidationError):
            FoodLibraryDomainsContract.model_validate(payload)

    def test_rejects_extra_domain_contract(self, fixture: JSONDict) -> None:
        payload = json.loads(json.dumps(fixture))
        payload["domainContracts"]["PantryItem"] = {
            "owner": "ingredient_product_library",
            "identityFields": ["ingredientProductId", "ownerUserId"],
            "ownedFields": ["displayName"],
        }

        with pytest.raises(ValidationError):
            FoodLibraryDomainsContract.model_validate(payload)

    def test_rejects_domain_drift(self, fixture: JSONDict) -> None:
        payload = json.loads(json.dumps(fixture))
        payload["libraryDomains"] = [
            "MealTemplate",
            "Recipe",
            "Recipe",
            "ShoppingList",
        ]

        with pytest.raises(ValidationError):
            FoodLibraryDomainsContract.model_validate(payload)

    def test_rejects_meal_template_logged_meal_only_field(
        self,
        fixture: JSONDict,
    ) -> None:
        payload = json.loads(json.dumps(fixture))
        payload["domainContracts"]["MealTemplate"]["ownedFields"].append("loggedAt")

        with pytest.raises(ValidationError):
            FoodLibraryDomainsContract.model_validate(payload)


# ---------------------------------------------------------------------------
# Fixture: smart_memory_core_v1.json
# ---------------------------------------------------------------------------


class TestSmartMemoryCoreContract:
    """Smart Memory backend/mobile contract must lock states before UI ships."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("smart_memory_core_v1.json")

    def test_fixture_uses_exact_contract_keys(self, fixture: JSONDict) -> None:
        assert set(fixture.keys()) == {
            "contract",
            "schemaVersion",
            "memoryTypes",
            "memoryStates",
            "candidateStates",
            "reasonCodes",
            "userControlOperations",
            "offlineProjectionStates",
            "apiEndpoints",
            "apiResponseExamples",
            "stateTransitionExamples",
            "memoryCenter",
            "review",
            "privacyBoundary",
        }
        assert set(cast(dict[str, object], fixture["reasonCodes"])) == {
            "stateReasonCodes",
            "confidenceReasonCodes",
            "userValueReasonCodes",
        }
        assert set(cast(dict[str, object], fixture["apiResponseExamples"])) == {
            "emptyItemsPage",
            "itemsPage",
            "candidateResponse",
            "itemDeleteResponse",
            "settingsEnabledResponse",
            "settingsDisabledResponse",
        }
        for example in cast(list[dict[str, object]], fixture["stateTransitionExamples"]):
            assert set(example) == {
                "case",
                "memoryType",
                "backendState",
                "projectionState",
                "reviewState",
                "memoryItemId",
                "candidateId",
                "queuedOperation",
                "suggestionUse",
            }

    def test_contract_parses_and_matches_backend_constants(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = SmartMemoryCoreContract.model_validate(fixture)

        assert contract.contract == SMART_MEMORY_CONTRACT_NAME
        assert contract.schemaVersion == SMART_MEMORY_SCHEMA_VERSION
        assert tuple(contract.memoryTypes) == SMART_MEMORY_TYPES
        assert tuple(contract.memoryStates) == SMART_MEMORY_STATES
        assert tuple(contract.candidateStates) == SMART_MEMORY_CANDIDATE_STATES
        assert (
            tuple(contract.reasonCodes.stateReasonCodes)
            == SMART_MEMORY_STATE_REASON_CODES
        )
        assert (
            tuple(contract.reasonCodes.confidenceReasonCodes)
            == SMART_MEMORY_CONFIDENCE_REASON_CODES
        )
        assert (
            tuple(contract.reasonCodes.userValueReasonCodes)
            == SMART_MEMORY_USER_VALUE_REASON_CODES
        )
        assert tuple(contract.userControlOperations) == (
            SMART_MEMORY_USER_CONTROL_OPERATIONS
        )
        assert tuple(contract.offlineProjectionStates) == (
            SMART_MEMORY_PROJECTION_STATES
        )
        assert tuple(contract.memoryCenter.states) == SMART_MEMORY_CENTER_STATES
        assert tuple(contract.review.states) == SMART_MEMORY_REVIEW_STATES

    def test_mobile_fixture_is_byte_identical(self) -> None:
        backend_fixture = (FIXTURES_DIR / "smart_memory_core_v1.json").read_bytes()
        mobile_fixture = (MOBILE_FIXTURES_DIR / "smart_memory_core_v1.json").read_bytes()

        assert mobile_fixture == backend_fixture

    def test_state_examples_cover_required_release_states(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = SmartMemoryCoreContract.model_validate(fixture)
        cases = {example.case for example in contract.stateTransitionExamples}

        assert cases == set(SMART_MEMORY_PROJECTION_STATES)
        blocked_cases = {
            "no_signal",
            "activated",
            "muted",
            "deleted_suppressed",
            "disabled",
            "source_deleted",
            "sync_failed",
            "conflicted",
            "queued_edit",
            "queued_mute",
            "queued_delete",
            "queued_disable",
        }
        for example in contract.stateTransitionExamples:
            if example.case in blocked_cases:
                assert example.suggestionUse == "blocked"
                assert example.reviewState != "used"
            if example.suggestionUse == "allowed":
                assert example.reviewState == "used"
            if example.case.startswith("queued_") or example.case in {
                "pending_offline_candidate",
                "sync_failed",
                "conflicted",
            }:
                assert example.queuedOperation is not None

    def test_backend_schemas_reject_unknown_reason_codes(
        self,
        fixture: JSONDict,
    ) -> None:
        mutated = cast(JSONDict, json.loads(json.dumps(fixture)))
        response_examples = cast(JSONDict, mutated["apiResponseExamples"])
        items_page = cast(JSONDict, response_examples["itemsPage"])
        items = cast(list[JSONDict], items_page["items"])
        items[0]["stateReason"] = "unknown_reason"
        with pytest.raises(ValidationError):
            SmartMemoryCoreContract.model_validate(mutated)

        mutated = cast(JSONDict, json.loads(json.dumps(fixture)))
        response_examples = cast(JSONDict, mutated["apiResponseExamples"])
        items_page = cast(JSONDict, response_examples["itemsPage"])
        items = cast(list[JSONDict], items_page["items"])
        items[0]["confidenceReasonCodes"] = ["unknown_reason"]
        with pytest.raises(ValidationError):
            SmartMemoryCoreContract.model_validate(mutated)

        mutated = cast(JSONDict, json.loads(json.dumps(fixture)))
        response_examples = cast(JSONDict, mutated["apiResponseExamples"])
        items_page = cast(JSONDict, response_examples["itemsPage"])
        items = cast(list[JSONDict], items_page["items"])
        user_value = cast(JSONDict, items[0]["userValue"])
        user_value["reasonCode"] = "unknown_reason"
        with pytest.raises(ValidationError):
            SmartMemoryCoreContract.model_validate(mutated)

        with pytest.raises(ValidationError):
            SmartMemoryItemPatchRequest.model_validate(
                {
                    "clientMutationId": "contract-mutation-bad-reason",
                    "userValue": {
                        "amount": 60,
                        "unit": "g",
                        "reasonCode": "unknown_reason",
                    },
                }
            )

    def test_api_examples_parse_through_backend_response_models(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = SmartMemoryCoreContract.model_validate(fixture)

        assert contract.apiResponseExamples.emptyItemsPage.items == []
        assert {item.state for item in contract.apiResponseExamples.itemsPage.items} == {
            "active",
            "muted",
        }
        assert contract.apiResponseExamples.candidateResponse.candidate.state == (
            "candidate"
        )
        assert contract.apiResponseExamples.itemDeleteResponse.item.state == (
            "deleted_suppressed"
        )
        assert contract.apiResponseExamples.itemDeleteResponse.item.subject == {}
        assert contract.apiResponseExamples.itemDeleteResponse.item.sourceRefs == []
        assert contract.apiResponseExamples.settingsEnabledResponse.settings.enabled is True
        assert contract.apiResponseExamples.settingsDisabledResponse.settings.enabled is (
            False
        )

    def test_fixture_keeps_private_and_provider_payloads_out(
        self,
        fixture: JSONDict,
    ) -> None:
        forbidden_keys = {
            "rawPrompt",
            "rawResponse",
            "providerMessages",
            "fullPayload",
            "openaiPayload",
            "providerPayload",
            "telemetryPayload",
            "rawReviewDiff",
            "rawDiff",
            "mealSnapshot",
        }
        assert _collect_object_keys(fixture).isdisjoint(forbidden_keys)
        contract = SmartMemoryCoreContract.model_validate(fixture)
        assert contract.privacyBoundary.excludesMealNarrativeText is True
        assert contract.privacyBoundary.excludesReviewDiffs is True
        assert contract.privacyBoundary.excludesProviderPayloads is True
        assert contract.privacyBoundary.excludesTelemetryPrivateIdentifiers is True
        assert contract.privacyBoundary.usesHashedSubjectAndSourceRefs is True


# ---------------------------------------------------------------------------
# Fixture: barcode_lookup_v1.json
# ---------------------------------------------------------------------------


class TestBarcodeLookupContract:
    """CH-06 barcode lookup response is backend-owned and mobile-consumable."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("barcode_lookup_v1.json")

    def test_found_response_parses_through_backend_schema(
        self,
        fixture: JSONDict,
    ) -> None:
        response = BarcodeLookupFoundResponse.model_validate(fixture["found"])

        assert response.kind == "found"
        assert response.name == "Greek yogurt"
        assert response.ingredient.id == "5901234123457"
        assert response.ingredient.amount == 100
        assert response.ingredient.unit == "g"
        assert response.ingredient.kcal == 120
        assert response.ingredient.protein == 12
        assert response.ingredient.fat == 4
        assert response.ingredient.carbs == 8

    def test_declares_exact_route_and_error_mapping(
        self,
        fixture: JSONDict,
    ) -> None:
        assert fixture["contract"] == "barcode_lookup_v1"
        assert fixture["route"] == {
            "method": "GET",
            "path": "/users/me/barcode/lookup",
            "query": {"barcode": "5901234123457"},
        }
        assert fixture["errors"] == {
            "invalid": {
                "status": 400,
                "detail": {
                    "code": "BARCODE_INVALID",
                    "message": "Barcode must be 8, 12, or 13 digits",
                },
            },
            "not_found": {
                "status": 404,
                "detail": {
                    "code": "BARCODE_NOT_FOUND",
                    "message": "Barcode product not found",
                },
            },
            "timeout": {
                "status": 504,
                "detail": {
                    "code": "BARCODE_PROVIDER_TIMEOUT",
                    "message": "Barcode provider timed out",
                },
            },
            "provider_error": {
                "status": 502,
                "detail": {
                    "code": "BARCODE_PROVIDER_FAILURE",
                    "message": "Barcode provider unavailable",
                },
            },
        }


# ---------------------------------------------------------------------------
# Fixture: media_asset_lifecycle_v1.json
# ---------------------------------------------------------------------------


class TestMediaAssetLifecycleContract:
    """Shared media asset lifecycle fixture must parse and stay exact."""

    @pytest.fixture()
    def fixture(self) -> JSONDict:
        return _load_fixture("media_asset_lifecycle_v1.json")

    def test_contract_parses(self, fixture: JSONDict) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)

        assert contract.contract == "media_asset_lifecycle_v1"
        assert contract.lifecycleOwner == MEDIA_ASSET_LIFECYCLE_OWNER
        assert (
            tuple(contract.assetLifecycleOwns)
            == MEDIA_ASSET_LIFECYCLE_OWNED_FIELDS
        )
        assert {"opId", "clientMutationId"}.issubset(contract.assetLifecycleOwns)

    def test_state_vocabulary_is_exact(self, fixture: JSONDict) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)

        assert tuple(contract.assetStates) == MEDIA_ASSET_STATES

    def test_release_surfaces_are_exact(self, fixture: JSONDict) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)

        assert set(contract.surfaces.keys()) == set(MEDIA_ASSET_SURFACES)

    def test_every_surface_uses_shared_states_and_owner_boundaries(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)

        for surface in MEDIA_ASSET_SURFACES:
            surface_contract = contract.surfaces[surface]

            assert surface_contract.usesAssetStates == "assetStates"
            assert surface_contract.domainDocumentOwns
            assert surface_contract.domainDocumentMustNotOwn == list(
                contract.assetLifecycleOwns
            )
            assert not MEDIA_ASSET_DOMAIN_OWNED_URL_FIELDS_FORBIDDEN.intersection(
                surface_contract.domainDocumentOwns
            )
            assert not any(
                field.endswith(("Url", "URL"))
                for field in surface_contract.domainDocumentOwns
            )

    def test_saved_meal_photo_bridges_to_future_library_domains(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)
        bridge = contract.surfaces["saved_meal_photo"].futureLibraryBridge

        assert bridge is not None
        assert bridge.currentDomain == "saved_meal"
        assert tuple(bridge.stableMediaIdentity) == (
            SAVED_MEAL_PHOTO_STABLE_MEDIA_IDENTITY
        )
        assert tuple(bridge.bridgesToDomains) == (
            SAVED_MEAL_PHOTO_LIBRARY_BRIDGE_DOMAINS
        )
        assert (
            bridge.bridgeMechanism
            == "reuse_imageRef_storagePath_without_storage_rewrite"
        )
        assert bridge.requiresSeparateMediaMigration is False

    def test_saved_meal_photo_excludes_product_and_shopping_list_migration_targets(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)
        bridge = contract.surfaces["saved_meal_photo"].futureLibraryBridge

        assert bridge is not None
        assert tuple(
            (target.domain, target.boundaryMechanism, target.reason)
            for target in bridge.nonMigrationTargets
        ) == SAVED_MEAL_PHOTO_LIBRARY_NON_MIGRATION_TARGETS
        assert "Ingredient/Product" not in bridge.bridgesToDomains
        assert "ShoppingList" not in bridge.bridgesToDomains

    def test_saved_meal_photo_non_migration_boundary_rejects_drift(
        self,
        fixture: JSONDict,
    ) -> None:
        payload = dict(fixture)
        payload["surfaces"] = dict(cast(JSONDict, fixture["surfaces"]))
        payload["surfaces"]["saved_meal_photo"] = dict(
            payload["surfaces"]["saved_meal_photo"]
        )
        payload["surfaces"]["saved_meal_photo"]["futureLibraryBridge"] = dict(
            payload["surfaces"]["saved_meal_photo"]["futureLibraryBridge"]
        )
        payload["surfaces"]["saved_meal_photo"]["futureLibraryBridge"][
            "nonMigrationTargets"
        ] = [
            dict(target)
            for target in payload["surfaces"]["saved_meal_photo"][
                "futureLibraryBridge"
            ]["nonMigrationTargets"]
        ]
        payload["surfaces"]["saved_meal_photo"]["futureLibraryBridge"][
            "nonMigrationTargets"
        ][0]["domain"] = "Recipe"

        with pytest.raises(ValidationError):
            MediaAssetLifecycleContract.model_validate(payload)

    def test_saved_meal_bridge_does_not_expand_current_domain_documents(
        self,
        fixture: JSONDict,
    ) -> None:
        contract = MediaAssetLifecycleContract.model_validate(fixture)
        saved_meal_photo = contract.surfaces["saved_meal_photo"]
        bridge = saved_meal_photo.futureLibraryBridge

        assert bridge is not None
        assert bridge.loggedMealMustRemainNarrow is True
        assert saved_meal_photo.domainDocumentOwns == [
            "imageRef",
            "displayMetadata",
            "savedMealDomainMetadata",
        ]
        assert not set(SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN).intersection(
            saved_meal_photo.domainDocumentOwns
        )
        assert tuple(bridge.currentSavedMealMustNotExpandWith) == (
            SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN
        )

    def test_library_bridge_is_only_valid_for_saved_meal_photo(
        self,
        fixture: JSONDict,
    ) -> None:
        payload = dict(fixture)
        payload["surfaces"] = dict(cast(JSONDict, fixture["surfaces"]))
        payload["surfaces"]["meal_photo"] = dict(payload["surfaces"]["meal_photo"])
        payload["surfaces"]["meal_photo"]["futureLibraryBridge"] = dict(
            payload["surfaces"]["saved_meal_photo"]["futureLibraryBridge"]
        )

        with pytest.raises(ValidationError):
            MediaAssetLifecycleContract.model_validate(payload)


class TestCoachContractEnums:
    """Coach contract Literals must stay aligned with the v1 contract doc."""

    def test_coach_insight_type_values(self) -> None:
        assert sorted(get_args(CoachInsightType)) == sorted(
            [
                "under_logging",
                "high_unknown_meal_details",
                "low_protein_consistency",
                "calorie_under_target",
                "positive_momentum",
                "stable",
            ]
        )

    def test_coach_action_type_values(self) -> None:
        assert sorted(get_args(CoachActionType)) == sorted(
            ["log_next_meal", "open_chat", "review_history", "none"]
        )

    def test_coach_source_values(self) -> None:
        assert sorted(get_args(CoachSource)) == ["rules"]

    def test_coach_empty_reason_values(self) -> None:
        assert sorted(get_args(CoachEmptyReason)) == sorted(
            ["no_data", "insufficient_data"]
        )


class TestSmartRemindersContractSnapshotFreshness:
    """Guarantee that the committed snapshot is never stale.

    Re-generates the contract in-memory from the current Python types and
    asserts it matches the committed JSON byte-for-byte.  If this test
    fails, run ``python scripts/export_reminder_contract.py`` and commit
    the updated snapshot.

    This is the canonical freshness gate: backend CI will reject any PR
    where Python types changed but the snapshot was not re-exported.
    """

    def test_committed_snapshot_matches_regenerated_contract(self) -> None:
        import sys

        sys.path.insert(0, str(FIXTURES_DIR.parent.parent / "scripts"))
        from export_reminder_contract import build_contract as build_contract_untyped  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

        build_contract = cast(Callable[[], dict[str, Any]], build_contract_untyped)
        expected = json.dumps(build_contract(), indent=2, ensure_ascii=False) + "\n"
        committed = (FIXTURES_DIR / "smart_reminders_v1.contract.json").read_text(
            encoding="utf-8"
        )
        assert committed == expected, (
            "Committed smart_reminders_v1.contract.json is stale. "
            "Run: python scripts/export_reminder_contract.py"
        )


class TestSmartRemindersContractSnapshot:
    """Validate that backend Python types match the canonical contract snapshot.

    The snapshot at ``smart_reminders_v1.contract.json`` is the cross-repo
    source of truth.  An identical copy lives in the mobile repo.  If this
    test fails, either the snapshot is stale (re-run
    ``scripts/export_reminder_contract.py``) or the types changed
    intentionally and the snapshot needs to be re-exported and synced.
    """

    @pytest.fixture()
    def contract(self) -> JSONDict:
        return _load_fixture("smart_reminders_v1.contract.json")

    def test_decision_types_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderDecisionType)) == sorted(
            contract["decisionTypes"]
        )

    def test_reminder_kinds_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderKind)) == sorted(contract["reminderKinds"])

    def test_all_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(get_args(ReminderReasonCode)) == sorted(
            contract["reasonCodes"]["all"]
        )

    def test_send_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(SEND_REASON_CODES) == sorted(contract["reasonCodes"]["send"])

    def test_suppress_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(SUPPRESS_REASON_CODES) == sorted(
            contract["reasonCodes"]["suppress"]
        )

    def test_noop_reason_codes_match_snapshot(self, contract: JSONDict) -> None:
        assert sorted(NOOP_REASON_CODES) == sorted(contract["reasonCodes"]["noop"])

    def test_telemetry_allowed_events_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        assert sorted(telemetry["eventNames"]) == sorted(
            contract["telemetry"]["allowedEvents"]
        )

    def test_telemetry_disallowed_events_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        assert sorted(telemetry["disallowedEventNames"]) == sorted(
            contract["telemetry"]["disallowedEvents"]
        )

    def test_telemetry_props_match_snapshot(self, contract: JSONDict) -> None:
        telemetry = _load_fixture("smart_reminder_telemetry.json")
        for event_name, props in telemetry["propsByEvent"].items():
            assert sorted(props) == sorted(
                contract["telemetry"]["propsByEvent"][event_name]
            ), f"Props mismatch for {event_name}"

    def test_decision_shape_required_fields(self, contract: JSONDict) -> None:
        schema_fields = set(ReminderDecision.model_fields.keys())
        snapshot_fields = set(contract["decisionShape"]["requiredFields"])
        assert schema_fields == snapshot_fields

    def test_reason_code_groups_are_exhaustive(self, contract: JSONDict) -> None:
        """send + suppress + noop reason codes must equal all reason codes."""
        grouped = sorted(
            contract["reasonCodes"]["send"]
            + contract["reasonCodes"]["suppress"]
            + contract["reasonCodes"]["noop"]
        )
        assert grouped == sorted(contract["reasonCodes"]["all"])
