from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.main import app
from app.schemas.nutrition_state import NutritionStateResponse
from tests.types import AuthHeaders

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"
AUTH_UID = "coach-route-user-42"
DAY_KEY = "2026-03-18"

client = TestClient(app)

JsonObject = dict[str, object]


class JsonResponse(Protocol):
    def json(self) -> object: ...


def _load_state_fixture() -> NutritionStateResponse:
    payload: object = json.loads(
        (FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8")
    )
    return NutritionStateResponse.model_validate(payload)


def _response_json(response: JsonResponse) -> JsonObject:
    return cast(JsonObject, response.json())


def _patch_foundation(
    mocker: MockerFixture,
    state: NutritionStateResponse | None = None,
    *,
    side_effect: Exception | None = None,
) -> AsyncMock:
    return cast(
        AsyncMock,
        mocker.patch(
            "app.services.coach_service.get_nutrition_state",
            return_value=state,
            side_effect=side_effect,
        ),
    )


def test_get_coach_route_uses_service_and_returns_rules_insight(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state = _load_state_fixture()
    state.habits.topRisk = "under_logging"
    state.habits.behavior.validLoggingDays7 = 2
    get_nutrition_state = _patch_foundation(mocker, state)

    response = client.get(
        f"/api/v2/users/me/coach?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    body = _response_json(response)
    top_insight = cast(JsonObject, body["topInsight"])
    meta = cast(JsonObject, body["meta"])

    assert response.status_code == 200
    assert body["dayKey"] == DAY_KEY
    assert body["source"] == "rules"
    assert top_insight["type"] == "under_logging"
    assert top_insight["source"] == "rules"
    assert meta["available"] is True
    assert meta["isDegraded"] is False
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)


def test_get_coach_route_keeps_non_critical_degradation_as_200(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state = _load_state_fixture()
    state.meta.componentStatus.streak = "error"
    get_nutrition_state = _patch_foundation(mocker, state)

    response = client.get(
        f"/api/v2/users/me/coach?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    body = _response_json(response)
    meta = cast(JsonObject, body["meta"])

    assert response.status_code == 200
    assert meta["isDegraded"] is True
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)


def test_get_coach_route_returns_503_when_habits_foundation_is_unavailable(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state = _load_state_fixture()
    state.habits.available = False
    state.meta.componentStatus.habits = "error"
    get_nutrition_state = _patch_foundation(mocker, state)

    response = client.get(
        f"/api/v2/users/me/coach?day={DAY_KEY}",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Coach insights are unavailable"}
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key=DAY_KEY)


def test_get_coach_route_returns_400_for_foundation_day_value_error(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_nutrition_state = _patch_foundation(
        mocker,
        side_effect=ValueError("Invalid day key. Expected YYYY-MM-DD."),
    )

    response = client.get(
        "/api/v2/users/me/coach?day=2026-13-40",
        headers=auth_headers(AUTH_UID),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid day key. Expected YYYY-MM-DD."}
    get_nutrition_state.assert_awaited_once_with(AUTH_UID, day_key="2026-13-40")
