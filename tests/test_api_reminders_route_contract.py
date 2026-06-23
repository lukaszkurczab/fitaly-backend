from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.nutrition_state import NutritionComponentState, NutritionStateResponse
from app.services.reminder_decision_store import DailySendCountResult
from tests.types import AuthHeaders

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"
AUTH_UID = "reminders-route-user-42"
DAY_KEY = "2026-03-18"

client = TestClient(app)

JsonObject = dict[str, object]


class JsonResponse(Protocol):
    def json(self) -> object: ...


def _load_state_fixture() -> NutritionStateResponse:
    payload: object = json.loads(
        (FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8")
    )
    state = NutritionStateResponse.model_validate(payload)
    state.quality.mealsLogged = 2
    return state


def _response_json(response: JsonResponse) -> JsonObject:
    return cast(JsonObject, response.json())


def _patch_reminder_foundations(
    mocker: MockerFixture,
    state: NutritionStateResponse | None = None,
    *,
    state_side_effect: Exception | None = None,
) -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    get_nutrition_state = cast(
        AsyncMock,
        mocker.patch(
            "app.services.reminder_service.get_nutrition_state",
            return_value=state,
            side_effect=state_side_effect,
        ),
    )
    get_notification_prefs = cast(
        AsyncMock,
        mocker.patch(
            "app.services.reminder_service.get_notification_prefs",
            return_value={"smartRemindersEnabled": True},
        ),
    )
    list_history = cast(
        AsyncMock,
        mocker.patch(
            "app.services.reminder_inputs.list_history",
            return_value=([], None),
        ),
    )
    list_changes = cast(
        AsyncMock,
        mocker.patch(
            "app.services.reminder_inputs.list_changes",
            return_value=([], None),
        ),
    )
    get_daily_send_count = cast(
        AsyncMock,
        mocker.patch(
            "app.services.reminder_inputs.get_daily_send_count",
            return_value=DailySendCountResult(count=0, degraded=False),
        ),
    )
    return (
        get_nutrition_state,
        get_notification_prefs,
        list_history,
        list_changes,
        get_daily_send_count,
    )


def test_get_reminder_decision_route_uses_service_inputs_and_rule_engine(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state = _load_state_fixture()
    (
        get_nutrition_state,
        get_notification_prefs,
        list_history,
        list_changes,
        get_daily_send_count,
    ) = _patch_reminder_foundations(mocker, state)
    record_send = cast(
        AsyncMock,
        mocker.patch("app.services.reminder_service.record_send_decision_if_new"),
    )
    mocker.patch(
        "app.services.reminder_service.utc_now",
        return_value=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
    )

    response = client.get(
        f"/api/v2/users/me/reminders/decision?day={DAY_KEY}&tzOffsetMin=120",
        headers=auth_headers(AUTH_UID),
    )

    body = _response_json(response)
    assert response.status_code == 200
    assert body["dayKey"] == DAY_KEY
    assert body["computedAt"] == "2026-03-18T12:00:00Z"
    assert body["decision"] == "send"
    assert body["kind"] == "log_next_meal"
    assert body["reasonCodes"] == [
        "habit_window_match",
        "day_partially_logged",
        "logging_usually_happens_now",
    ]
    assert body["scheduledAtUtc"] == "2026-03-18T12:00:00Z"
    assert body["confidence"] == 0.84
    assert body["validUntil"] == "2026-03-18T12:30:00Z"
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)
    get_notification_prefs.assert_awaited_once_with(AUTH_UID)
    list_history.assert_any_await(
        AUTH_UID,
        limit_count=5,
        logged_at_start="2026-03-18T10:30:00Z",
        logged_at_end="2026-03-18T12:00:00Z",
    )
    list_history.assert_any_await(AUTH_UID, limit_count=1)
    list_changes.assert_awaited_once_with(
        AUTH_UID,
        limit_count=20,
        after_cursor="2026-03-18T10:30:00Z",
    )
    get_daily_send_count.assert_awaited_once_with(AUTH_UID, DAY_KEY)
    record_send.assert_awaited_once_with(
        AUTH_UID,
        DAY_KEY,
        kind="log_next_meal",
        scheduled_at_utc="2026-03-18T12:00:00Z",
    )


def test_get_reminder_decision_kill_switch_returns_503_before_foundation_reads(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch("app.services.reminder_service.settings.SMART_REMINDERS_ENABLED", False)
    get_nutrition_state = cast(
        AsyncMock,
        mocker.patch("app.services.reminder_service.get_nutrition_state"),
    )
    get_notification_prefs = cast(
        AsyncMock,
        mocker.patch("app.services.reminder_service.get_notification_prefs"),
    )

    response = client.get(
        f"/api/v2/users/me/reminders/decision?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Smart reminders are unavailable"}
    get_nutrition_state.assert_not_awaited()
    get_notification_prefs.assert_not_awaited()


def test_get_reminder_decision_returns_503_when_habits_foundation_is_unavailable(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state = _load_state_fixture()
    state.habits.available = False
    state.meta.componentStatus.habits = cast(NutritionComponentState, "error")
    get_nutrition_state, get_notification_prefs, _, _, _ = _patch_reminder_foundations(
        mocker,
        state,
    )

    response = client.get(
        f"/api/v2/users/me/reminders/decision?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Smart reminders are unavailable"}
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)
    get_notification_prefs.assert_not_awaited()


def test_get_reminder_decision_returns_400_for_foundation_day_value_error(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_nutrition_state, get_notification_prefs, _, _, _ = _patch_reminder_foundations(
        mocker,
        state_side_effect=ValueError("Invalid day key. Expected YYYY-MM-DD."),
    )

    response = client.get(
        "/api/v2/users/me/reminders/decision?day=2026-13-40",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid day key. Expected YYYY-MM-DD."}
    get_nutrition_state.assert_awaited_once_with(
        AUTH_UID,
        day_key="2026-13-40",
    )
    get_notification_prefs.assert_not_awaited()


def test_get_reminder_decision_returns_500_for_foundation_firestore_error(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_nutrition_state, get_notification_prefs, _, _, _ = _patch_reminder_foundations(
        mocker,
        state_side_effect=FirestoreServiceError("state failed"),
    )

    response = client.get(
        f"/api/v2/users/me/reminders/decision?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to compute reminder decision"}
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)
    get_notification_prefs.assert_not_awaited()
