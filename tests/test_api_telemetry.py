from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.services import telemetry_service
from tests.types import AuthHeaders, LogCaptureFixture


class FakeDocumentRef:
    def __init__(self, storage: dict[str, dict[str, object]], document_id: str) -> None:
        self._storage = storage
        self._document_id = document_id

    def create(self, data: dict[str, object]) -> None:
        if self._document_id in self._storage:
            raise AlreadyExists("duplicate document")
        self._storage[self._document_id] = data


class FakeSnapshot:
    def __init__(self, document_id: str, data: dict[str, object]) -> None:
        self.id = document_id
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return self._data


class FakeQuery:
    def __init__(
        self,
        storage: dict[str, dict[str, object]],
        filters: list[tuple[str, str, object]] | None = None,
    ) -> None:
        self._storage = storage
        self._filters = filters or []

    def where(self, field_path: str, op_string: str, value: object) -> "FakeQuery":
        return FakeQuery(self._storage, [*self._filters, (field_path, op_string, value)])

    def stream(self):
        snapshots: list[FakeSnapshot] = []
        for document_id, payload in self._storage.items():
            if _matches_filters(payload, self._filters):
                snapshots.append(FakeSnapshot(document_id, payload))
        return snapshots


def _matches_filters(
    payload: dict[str, object],
    filters: list[tuple[str, str, object]],
) -> bool:
    def _matches_ordered_filter(actual: object | None, expected: object, operator: str) -> bool:
        if isinstance(actual, str) and isinstance(expected, str):
            if operator == ">=":
                return actual >= expected
            if operator == "<=":
                return actual <= expected
            if operator == "<":
                return actual < expected
            return False

        if (
            isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and isinstance(expected, int | float)
            and not isinstance(expected, bool)
        ):
            actual_number = float(actual)
            expected_number = float(expected)
            if operator == ">=":
                return actual_number >= expected_number
            if operator == "<=":
                return actual_number <= expected_number
            if operator == "<":
                return actual_number < expected_number
            return False

        return False

    for field_path, op_string, expected in filters:
        actual = payload.get(field_path)
        if op_string == "==" and actual != expected:
            return False
        if op_string == ">=" and not _matches_ordered_filter(actual, expected, op_string):
            return False
        if op_string == "<=" and not _matches_ordered_filter(actual, expected, op_string):
            return False
        if op_string == "<" and not _matches_ordered_filter(actual, expected, op_string):
            return False
    return True


class FakeCollectionRef:
    def __init__(self, storage: dict[str, dict[str, object]]) -> None:
        self._storage = storage

    def document(self, document_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self._storage, document_id)

    def where(self, field_path: str, op_string: str, value: object) -> FakeQuery:
        return FakeQuery(self._storage, [(field_path, op_string, value)])


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.storage: dict[str, dict[str, object]] = {}
        self.requested_collections: list[str] = []

    def collection(self, name: str) -> FakeCollectionRef:
        self.requested_collections.append(name)
        return FakeCollectionRef(self.storage)


class FailingDocumentRef:
    def create(self, data: dict[str, object]) -> None:
        from google.api_core.exceptions import GoogleAPICallError

        raise GoogleAPICallError("simulated write failure")


class FailingCollectionRef:
    def document(self, document_id: str) -> FailingDocumentRef:
        return FailingDocumentRef()


class FailingFirestoreClient:
    def collection(self, name: str) -> FailingCollectionRef:
        return FailingCollectionRef()


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")
    return TestClient(app)


def build_event_context(
    *,
    event_id: str = "evt-1",
    session_id: str = "sess-1",
    actor: dict[str, str] | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "eventId": event_id,
        "ts": "2026-03-18T12:00:00Z",
        "occurredAt": "2026-03-18T12:00:00Z",
        "sessionId": session_id,
        "actor": actor or {"anonymousId": "anon-1"},
        "platform": "ios",
        "appVersion": "1.2.3",
        "build": "45",
        "locale": "pl-PL",
        "timezone": "Europe/Warsaw",
        "tzOffsetMin": 60,
        "schemaVersion": 2,
    }
    if request_id is not None:
        context["requestId"] = request_id
    return context


def build_payload(event_overrides: dict[str, Any] | None = None) -> dict[str, object]:
    event: dict[str, Any] = {
        **build_event_context(),
        "name": "meal_logged",
        "props": {
            "mealInputMethod": "photo",
            "ingredientCount": 3,
            "source": "ai",
        },
    }
    if event_overrides:
        event.update(event_overrides)

    return {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [event],
    }


def setup_telemetry_enabled(mocker: MockerFixture, enabled: bool = True) -> None:
    mocker.patch("app.services.telemetry_service.settings.TELEMETRY_ENABLED", enabled)


def reset_telemetry_state() -> None:
    telemetry_service.reset_rate_limit_state()


def test_telemetry_batch_accepts_valid_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"actor": {"userId": "user-123"}}),
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 1,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    stored_event = firestore_client.storage["evt-1"]
    assert stored_event["eventId"] == "evt-1"
    assert stored_event["name"] == "meal_logged"
    assert stored_event["sessionId"] == "sess-1"
    assert stored_event["userId"] == "user-123"
    assert stored_event["userHash"] == (
        "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"
    )
    assert stored_event["anonymousId"] is None
    assert stored_event["actorType"] == "user"
    assert stored_event["actorAuthValidation"] == "matched"
    assert stored_event["platform"] == "ios"
    assert stored_event["appVersion"] == "1.2.3"
    assert stored_event["build"] == "45"
    assert stored_event["locale"] == "pl-PL"
    assert stored_event["timezone"] == "Europe/Warsaw"
    assert stored_event["tzOffsetMin"] == 60
    assert stored_event["schemaVersion"] == 2
    assert stored_event["props"] == {
        "mealInputMethod": "photo",
        "ingredientCount": 3,
        "source": "ai",
    }


def test_telemetry_batch_keeps_anonymous_events_distinct_after_login(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"actor": {"anonymousId": "anon-before-login"}}),
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 202
    stored_event = firestore_client.storage["evt-1"]
    assert stored_event["userId"] is None
    assert stored_event["userHash"] is None
    assert stored_event["anonymousId"] == "anon-before-login"
    assert stored_event["actorType"] == "anonymous"
    assert stored_event["actorAuthValidation"] == "anonymous"


def test_telemetry_batch_accepts_anonymous_event_without_auth(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    ingest_now = datetime(2026, 3, 18, 12, 30, tzinfo=timezone.utc)
    mocker.patch("app.services.telemetry_service.utc_now", return_value=ingest_now)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"actor": {"anonymousId": "anon-1"}}),
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 1
    stored_event = firestore_client.storage["evt-1"]
    assert stored_event["userId"] is None
    assert stored_event["userHash"] is None
    assert stored_event["anonymousId"] == "anon-1"
    assert stored_event["actorType"] == "anonymous"
    assert stored_event["actorAuthValidation"] == "anonymous"
    assert stored_event["ingestedAt"] == "2026-03-18T12:30:00Z"
    assert stored_event["expiresAt"] == ingest_now + timedelta(
        days=telemetry_service.TELEMETRY_RETENTION_DAYS
    )


def test_telemetry_batch_rejects_mismatched_authenticated_actor(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = build_payload()
    payload["events"] = [
        {
            **build_event_context(event_id="evt-user-a", actor={"userId": "user-a"}),
            "name": "meal_logged",
            "props": {
                "mealInputMethod": "manual",
                "ingredientCount": 1,
                "source": "manual",
            },
        },
        {
            **build_event_context(event_id="evt-user-b", actor={"userId": "user-b"}),
            "name": "paywall_view",
            "props": {
                "source": "meal_text_limit",
                "trigger_source": "meal_text_limit_modal",
            },
        },
    ]

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=payload,
        headers=auth_headers("user-b"),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 1,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-user-a",
                "name": "meal_logged",
                "reason": "actor_auth_mismatch",
            }
        ],
    }
    assert "evt-user-a" not in firestore_client.storage
    user_b_event = firestore_client.storage["evt-user-b"]
    assert user_b_event["userId"] == "user-b"
    assert user_b_event["userHash"] == telemetry_service.build_user_hash("user-b")
    assert user_b_event["actorAuthValidation"] == "matched"


def test_telemetry_batch_rejects_unauthenticated_user_actor(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"actor": {"userId": "user-123"}}),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-1",
                "name": "meal_logged",
                "reason": "unauthenticated_user_actor",
            }
        ],
    }
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_v1_payload_with_legacy_auth_ownership(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    legacy_payload: dict[str, Any] = {
        "sessionId": "sess-legacy",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-legacy",
                "name": "meal_logged",
                "ts": "2026-03-18T12:00:00Z",
                "props": {
                    "mealInputMethod": "photo",
                    "ingredientCount": 3,
                    "source": "ai",
                },
            }
        ],
    }

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=legacy_payload,
        headers=auth_headers("user-legacy"),
    )

    assert response.status_code == 202
    stored_event = firestore_client.storage["evt-legacy"]
    assert stored_event["schemaVersion"] == 1
    assert stored_event["sessionId"] == "sess-legacy"
    assert stored_event["userId"] == "user-legacy"
    assert stored_event["actorAuthValidation"] == "legacy_authenticated"


def test_telemetry_batch_accepts_launch_kpi_events(mocker: MockerFixture) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload: dict[str, Any] = {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-onboarding",
                "name": "onboarding_completed",
                "ts": "2026-03-18T12:00:00Z",
                "props": {"mode": "first"},
            },
            {
                "eventId": "evt-review",
                "name": "ai_meal_review_saved",
                "ts": "2026-03-18T12:00:10Z",
                "props": {
                    "inputMethod": "photo",
                    "corrected": True,
                    "ingredientCount": 4,
                    "requestId": "run-1",
                },
            },
            {
                "eventId": "evt-paywall",
                "name": "paywall_view",
                "ts": "2026-03-18T12:00:20Z",
                "props": {
                    "source": "meal_text_limit",
                    "trigger_source": "meal_text_limit_modal",
                },
            },
            {
                "eventId": "evt-purchase",
                "name": "purchase_started",
                "ts": "2026-03-18T12:00:30Z",
                "props": {"source": "manage_subscription"},
            },
            {
                "eventId": "evt-purchase-success",
                "name": "purchase_succeeded",
                "ts": "2026-03-18T12:00:35Z",
                "props": {"source": "manage_subscription"},
            },
            {
                "eventId": "evt-entitlement",
                "name": "entitlement_confirmed",
                "ts": "2026-03-18T12:00:40Z",
                "props": {"source": "purchase", "tier": "premium"},
            },
            {
                "eventId": "evt-weekly",
                "name": "weekly_report_opened",
                "ts": "2026-03-18T12:00:50Z",
                "props": {
                    "reportStatus": "ready",
                    "insightCount": 2,
                    "priorityCount": 2,
                },
            },
            {
                "eventId": "evt-notification",
                "name": "notification_opened",
                "ts": "2026-03-18T12:01:00Z",
                "props": {
                    "notificationType": "meal_reminder",
                    "origin": "system_notifications",
                },
            },
        ],
    }
    events = cast(list[dict[str, Any]], payload["events"])
    for event in events:
        request_id: str | None = None
        props = event.get("props")
        if isinstance(props, dict):
            props_map = cast(dict[str, object], props)
            request_id_value = props_map.get("requestId")
            if isinstance(request_id_value, str):
                request_id = request_id_value
        event.update(
            build_event_context(
                event_id=str(event["eventId"]),
                actor={"userId": "user-123"},
                request_id=request_id,
            )
        )

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=payload,
        headers={"Authorization": "Bearer user-123"},
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 8
    assert response.json()["rejectedCount"] == 0


def test_telemetry_batch_accepts_session_start_app_boot_origin(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    props: dict[str, object] = {"origin": "app_boot"}

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"name": "session_start", "props": props}),
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 1
    assert firestore_client.storage["evt-1"]["props"] == props


def test_telemetry_batch_rejects_unbounded_session_start_origin(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "session_start",
                "props": {"origin": "private launch note"},
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_notification_opened_mobile_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    props: dict[str, object] = {
        "notificationType": "meal_reminder",
        "origin": "system_notifications",
        "actionIdentifier": "default",
        "openedFromBackground": True,
    }

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"name": "notification_opened", "props": props}),
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 1
    assert firestore_client.storage["evt-1"]["props"] == props


def test_telemetry_batch_accepts_user_and_system_notification_types(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    day_fill_props: dict[str, object] = {
        "notificationType": "day_fill",
        "origin": "user_notifications",
    }
    weekly_summary_props: dict[str, object] = {
        "notificationType": "stats_weekly_summary",
        "origin": "system_notifications",
        "actionIdentifier": "open_chat",
    }
    motivation_props: dict[str, object] = {
        "notificationType": "motivation_dont_give_up",
        "origin": "system_notifications",
    }
    payload = build_payload()
    payload["events"] = [
        {
            **build_event_context(event_id="evt-day-fill"),
            "name": "notification_opened",
            "props": day_fill_props,
        },
        {
            **build_event_context(event_id="evt-weekly-summary"),
            "name": "notification_opened",
            "props": weekly_summary_props,
        },
        {
            **build_event_context(event_id="evt-motivation"),
            "name": "notification_opened",
            "props": motivation_props,
        },
    ]

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 3
    assert firestore_client.storage["evt-day-fill"]["props"] == day_fill_props
    assert firestore_client.storage["evt-weekly-summary"]["props"] == weekly_summary_props
    assert firestore_client.storage["evt-motivation"]["props"] == motivation_props


def test_telemetry_batch_accepts_manage_subscription_entitlement_and_restore_events(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    entitlement_confirmed_props: dict[str, object] = {
        "source": "manage_subscription",
        "tier": "premium",
    }
    entitlement_failed_props: dict[str, object] = {
        "source": "manage_subscription",
        "reason": "access_unknown_degraded",
    }
    restore_succeeded_props: dict[str, object] = {
        "source": "manage_subscription",
        "confirmed": True,
    }
    restore_failed_props: dict[str, object] = {
        "source": "manage_subscription",
        "reason": "network",
    }
    payload = build_payload()
    payload["events"] = [
        {
            **build_event_context(event_id="evt-entitlement-confirmed"),
            "name": "entitlement_confirmed",
            "props": entitlement_confirmed_props,
        },
        {
            **build_event_context(event_id="evt-entitlement-failed"),
            "name": "entitlement_confirmation_failed",
            "props": entitlement_failed_props,
        },
        {
            **build_event_context(event_id="evt-restore-succeeded"),
            "name": "restore_succeeded",
            "props": restore_succeeded_props,
        },
        {
            **build_event_context(event_id="evt-restore-failed"),
            "name": "restore_failed",
            "props": restore_failed_props,
        },
    ]

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 4
    assert (
        firestore_client.storage["evt-entitlement-confirmed"]["props"]
        == entitlement_confirmed_props
    )
    assert (
        firestore_client.storage["evt-entitlement-failed"]["props"]
        == entitlement_failed_props
    )
    assert (
        firestore_client.storage["evt-restore-succeeded"]["props"]
        == restore_succeeded_props
    )
    assert firestore_client.storage["evt-restore-failed"]["props"] == restore_failed_props


def test_telemetry_batch_rejects_unbounded_premium_event_sources(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    event_cases: tuple[tuple[str, dict[str, object]], ...] = (
        (
            "entitlement_confirmed",
            {"source": "private support note", "tier": "premium"},
        ),
        (
            "entitlement_confirmation_failed",
            {
                "source": "private support note",
                "reason": "access_unknown_degraded",
            },
        ),
        (
            "restore_succeeded",
            {"source": "private support note", "confirmed": True},
        ),
        (
            "restore_failed",
            {"source": "private support note", "reason": "network"},
        ),
    )

    for index, (event_name, props) in enumerate(event_cases):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(
                {
                    "eventId": f"evt-premium-source-{index}",
                    "name": event_name,
                    "props": props,
                }
            ),
        )

        assert response.status_code == 422

    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_unbounded_notification_type(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "notification_opened",
                "props": {
                    "notificationType": "private custom reminder note",
                    "origin": "user_notifications",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_unbounded_notification_action_identifier(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "notification_opened",
                "props": {
                    "notificationType": "meal_reminder",
                    "origin": "system_notifications",
                    "actionIdentifier": "private action label",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_weekly_report_opened_with_bounded_access_reason(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    props: dict[str, object] = {
        "reportStatus": "unavailable",
        "insightCount": 0,
        "priorityCount": 0,
        "source": "fallback",
        "accessState": "degraded",
        "accessReason": "degraded",
    }

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"name": "weekly_report_opened", "props": props}),
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 1
    assert firestore_client.storage["evt-1"]["props"] == props


def test_telemetry_batch_accepts_weekly_locked_and_blocked_bounded_reasons(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    locked_props: dict[str, object] = {
        "source": "remote",
        "accessState": "locked",
        "accessReason": "premium_required",
    }
    blocked_props: dict[str, object] = {
        "source": "disabled",
        "accessState": "degraded",
        "accessReason": "feature_disabled",
    }
    payload = build_payload()
    payload["events"] = [
        {
            **build_event_context(event_id="evt-weekly-locked"),
            "name": "weekly_report_locked_viewed",
            "props": locked_props,
        },
        {
            **build_event_context(event_id="evt-weekly-blocked"),
            "name": "weekly_report_access_blocked",
            "props": blocked_props,
        },
    ]

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 2
    assert firestore_client.storage["evt-weekly-locked"]["props"] == locked_props
    assert firestore_client.storage["evt-weekly-blocked"]["props"] == blocked_props


def test_telemetry_batch_rejects_unbounded_weekly_access_reason(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "weekly_report_locked_viewed",
                "props": {
                    "source": "remote",
                    "accessState": "locked",
                    "accessReason": "my private paywall note",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_weekly_user_authored_content_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    for prop_key in ("title", "body", "summary", "reasonCodes", "text", "message"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(
                {
                    "eventId": f"evt-weekly-{prop_key}",
                    "name": "weekly_report_opened",
                    "props": {
                        "reportStatus": "ready",
                        "source": "remote",
                        "accessState": "premium",
                        prop_key: "raw weekly report content",
                    },
                }
            ),
        )

        assert response.status_code == 422

    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_smart_reminder_events(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload: dict[str, Any] = {
        "sessionId": "sess-1",
        "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
        "device": {"locale": "pl-PL", "tzOffsetMin": 60},
        "events": [
            {
                "eventId": "evt-reminder-1",
                "name": "smart_reminder_suppressed",
                "ts": "2026-03-18T12:00:00Z",
                "props": {
                    "decision": "suppress",
                    "suppressionReason": "quiet_hours",
                    "confidenceBucket": "high",
                },
            },
            {
                "eventId": "evt-reminder-2",
                "name": "smart_reminder_scheduled",
                "ts": "2026-03-18T12:00:10Z",
                "props": {
                    "reminderKind": "complete_day",
                    "decision": "send",
                    "confidenceBucket": "medium",
                    "scheduledWindow": "evening",
                },
            },
            {
                "eventId": "evt-reminder-3",
                "name": "smart_reminder_schedule_failed",
                "ts": "2026-03-18T12:00:20Z",
                "props": {
                    "reminderKind": "log_next_meal",
                    "decision": "send",
                    "confidenceBucket": "high",
                    "failureReason": "channel_unavailable",
                },
            },
        ],
    }
    for event in payload["events"]:
        event.update(build_event_context(event_id=str(event["eventId"])))

    response = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 3,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }


def test_telemetry_batch_drops_disallowed_event_names(mocker: MockerFixture) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"name": "unexpected_event"}),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-1",
                "name": "unexpected_event",
                "reason": "event_not_allowed",
            }
        ],
    }


def test_telemetry_batch_is_idempotent_for_duplicate_event_ids(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    payload = build_payload()

    first = client.post("/api/v2/telemetry/events/batch", json=payload)
    second = client.post("/api/v2/telemetry/events/batch", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == {
        "acceptedCount": 0,
        "duplicateCount": 1,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }


def test_telemetry_batch_rejects_unknown_props_for_event(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"props": {"mealInputMethod": "photo", "screen": "home"}}),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_privacy_sensitive_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"props": {"message": "raw user content"}}),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_raw_provider_payload_props(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    raw_provider_props: tuple[tuple[str, object], ...] = (
        ("rawPrompt", "secret-provider-prompt"),
        ("rawResponse", "secret-provider-response"),
        ("providerMessages", ["secret-provider-prompt"]),
        ("fullPayload", "secret-full-payload"),
        ("rawImage", "secret-raw-image"),
        ("rawToolOutput", "secret-tool-dump"),
        ("debug", "secret-debug-log"),
        ("logs", "secret-debug-log"),
    )

    for prop_key, prop_value in raw_provider_props:
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(
                {
                    "eventId": f"evt-raw-provider-{prop_key}",
                    "name": "ai_meal_review_saved",
                    "props": {
                        "inputMethod": "photo",
                        "corrected": False,
                        "ingredientCount": 3,
                        "requestId": "run-1",
                        prop_key: prop_value,
                    },
                }
            ),
        )

        assert response.status_code == 422

    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_invalid_enum_values(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(
            {
                "name": "paywall_view",
                "props": {
                    "source": "unsupported_source",
                    "trigger_source": "meal_text_limit_modal",
                },
            }
        ),
    )

    assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_accepts_autocomplete_search_outcome(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        headers=auth_headers("user-123"),
        json=build_payload(
            {
                "name": "autocomplete_search_outcome",
                "props": {
                    "surface": "manual_ingredient_sheet",
                    "outcome": "results",
                    "queryLengthBucket": "4_8",
                    "resultCountBucket": "4_6",
                    "sourceClass": "remote",
                    "latencyBucket": "250_750_ms",
                    "warningReason": "profile_unknown",
                },
                "actor": {"userId": "user-123"},
            }
        ),
    )

    assert response.status_code == 202
    assert len(firestore_client.storage) == 1


def test_telemetry_batch_accepts_ingredient_product_create_outcomes(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = build_payload({"actor": {"userId": "user-123"}})
    payload["events"] = [
        {
            **build_event_context(
                event_id=f"evt-create-{outcome}",
                actor={"userId": "user-123"},
            ),
            "name": "ingredient_product_create_outcome",
            "props": {
                "surface": "manual_ingredient_sheet",
                "outcome": outcome,
            },
        }
        for outcome in ("synced", "queued", "failed")
    ]

    response = client.post(
        "/api/v2/telemetry/events/batch",
        headers=auth_headers("user-123"),
        json=payload,
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 3
    assert firestore_client.storage["evt-create-synced"]["props"] == {
        "surface": "manual_ingredient_sheet",
        "outcome": "synced",
    }
    assert firestore_client.storage["evt-create-queued"]["props"] == {
        "surface": "manual_ingredient_sheet",
        "outcome": "queued",
    }
    assert firestore_client.storage["evt-create-failed"]["props"] == {
        "surface": "manual_ingredient_sheet",
        "outcome": "failed",
    }


def test_telemetry_batch_accepts_home_next_action_events(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    payload = build_payload({"actor": {"userId": "user-123"}})
    payload["events"] = [
        {
            **build_event_context(
                event_id="evt-home-next-action-shown",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_shown",
            "props": {
                "actionType": "continue_review",
                "state": "eligible",
                "reasonCode": "review_draft_available",
                "sourceDomain": "review_draft",
            },
        },
        {
            **build_event_context(
                event_id="evt-home-next-action-started",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_started",
            "props": {
                "actionType": "continue_review",
                "ownerFlow": "ReviewMeal",
                "state": "eligible",
            },
        },
        {
            **build_event_context(
                event_id="evt-home-next-action-dismissed",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_dismissed",
            "props": {
                "actionType": "continue_review",
                "reasonCode": "review_draft_available",
                "cooldownBucket": "24h",
            },
        },
        {
            **build_event_context(
                event_id="evt-home-next-action-planned-shown",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_shown",
            "props": {
                "actionType": "continue_planned_item",
                "state": "eligible",
                "reasonCode": "planned_item_due",
                "sourceDomain": "planned_meal",
            },
        },
        {
            **build_event_context(
                event_id="evt-home-next-action-planned-started",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_started",
            "props": {
                "actionType": "continue_planned_item",
                "ownerFlow": "Planning",
                "state": "eligible",
            },
        },
        {
            **build_event_context(
                event_id="evt-home-next-action-planned-dismissed",
                actor={"userId": "user-123"},
            ),
            "name": "home_next_action_dismissed",
            "props": {
                "actionType": "continue_planned_item",
                "reasonCode": "planned_item_due",
                "cooldownBucket": "24h",
            },
        },
    ]

    response = client.post(
        "/api/v2/telemetry/events/batch",
        headers=auth_headers("user-123"),
        json=payload,
    )

    assert response.status_code == 202
    assert response.json()["acceptedCount"] == 6
    assert firestore_client.requested_collections == ["telemetry_events"]
    assert firestore_client.storage["evt-home-next-action-shown"]["props"] == {
        "actionType": "continue_review",
        "state": "eligible",
        "reasonCode": "review_draft_available",
        "sourceDomain": "review_draft",
    }
    assert firestore_client.storage["evt-home-next-action-started"]["props"] == {
        "actionType": "continue_review",
        "ownerFlow": "ReviewMeal",
        "state": "eligible",
    }
    assert firestore_client.storage["evt-home-next-action-dismissed"]["props"] == {
        "actionType": "continue_review",
        "reasonCode": "review_draft_available",
        "cooldownBucket": "24h",
    }
    assert firestore_client.storage["evt-home-next-action-planned-shown"]["props"] == {
        "actionType": "continue_planned_item",
        "state": "eligible",
        "reasonCode": "planned_item_due",
        "sourceDomain": "planned_meal",
    }
    assert firestore_client.storage["evt-home-next-action-planned-started"]["props"] == {
        "actionType": "continue_planned_item",
        "ownerFlow": "Planning",
        "state": "eligible",
    }
    assert firestore_client.storage["evt-home-next-action-planned-dismissed"]["props"] == {
        "actionType": "continue_planned_item",
        "reasonCode": "planned_item_due",
        "cooldownBucket": "24h",
    }


def test_telemetry_batch_rejects_home_next_action_unbounded_enums(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    invalid_events: tuple[dict[str, object], ...] = (
        {
            "eventId": "evt-home-invalid-action",
            "name": "home_next_action_shown",
            "props": {
                "actionType": "inspect_memory",
                "state": "eligible",
                "reasonCode": "review_draft_available",
                "sourceDomain": "review_draft",
            },
        },
        {
            "eventId": "evt-home-invalid-state",
            "name": "home_next_action_started",
            "props": {
                "actionType": "continue_review",
                "ownerFlow": "ReviewMeal",
                "state": "pending",
            },
        },
        {
            "eventId": "evt-home-invalid-source-domain",
            "name": "home_next_action_shown",
            "props": {
                "actionType": "continue_planned_item",
                "state": "eligible",
                "reasonCode": "planned_item_due",
                "sourceDomain": "planning_note",
            },
        },
        {
            "eventId": "evt-home-invalid-owner-flow",
            "name": "home_next_action_started",
            "props": {
                "actionType": "continue_planned_item",
                "ownerFlow": "PlanningDetail",
                "state": "eligible",
            },
        },
        {
            "eventId": "evt-home-invalid-reason-code",
            "name": "home_next_action_dismissed",
            "props": {
                "actionType": "continue_planned_item",
                "reasonCode": "raw-user-reason",
                "cooldownBucket": "24h",
            },
        },
        {
            "eventId": "evt-home-invalid-cooldown",
            "name": "home_next_action_dismissed",
            "props": {
                "actionType": "continue_review",
                "reasonCode": "review_draft_available",
                "cooldownBucket": "raw-unbounded",
            },
        },
    )

    for event in invalid_events:
        response = client.post(
            "/api/v2/telemetry/events/batch",
            headers=auth_headers("user-123"),
            json=build_payload({**event, "actor": {"userId": "user-123"}}),
        )

        assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_disallowed_manual_product_created_event(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        headers=auth_headers("user-123"),
        json=build_payload(
            {
                "eventId": "evt-disallowed-manual-product-created",
                "name": "manual_product_created",
                "props": {
                    "surface": "manual_ingredient_sheet",
                    "outcome": "synced",
                },
                "actor": {"userId": "user-123"},
            }
        ),
    )

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 1,
        "rejectedEvents": [
            {
                "eventId": "evt-disallowed-manual-product-created",
                "name": "manual_product_created",
                "reason": "event_not_allowed",
            }
        ],
    }
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_home_next_action_sensitive_props(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    forbidden_props: tuple[tuple[str, object], ...] = (
        ("suggestionText", "Finish oats review"),
        ("rawSuggestionText", "Finish oats review"),
        ("mealText", "oats and yogurt"),
        ("recipeName", "Oats breakfast"),
        ("productName", "Oats"),
        ("ingredientName", "Oats"),
        ("candidateId", "review-draft:local"),
        ("mealId", "draft-1"),
        ("userId", "user-123"),
        ("anonymousId", "anon-1"),
        ("barcode", "5901234123457"),
        ("kcal", 389),
        ("calories", 389),
        ("macros", "20/30/40"),
        ("protein", 16.9),
        ("carbs", 66.3),
        ("fat", 6.9),
        ("sourceRef", "source-ref-1"),
        ("memoryId", "memory-1"),
        ("patternId", "pattern-1"),
        ("profileHealth", "health free text"),
        ("profileFreeText", "profile note"),
        ("healthConditions", "health free text"),
        ("rawPrompt", "provider prompt"),
        ("rawResponse", "provider response"),
        ("providerPayload", "provider payload"),
        ("rawProviderPayload", "provider payload"),
    )

    for prop_key, prop_value in forbidden_props:
        response = client.post(
            "/api/v2/telemetry/events/batch",
            headers=auth_headers("user-123"),
            json=build_payload(
                {
                    "eventId": f"evt-home-forbidden-{prop_key}",
                    "name": "home_next_action_shown",
                    "props": {
                        "actionType": "continue_review",
                        "state": "eligible",
                        "reasonCode": "review_draft_available",
                        "sourceDomain": "review_draft",
                        prop_key: prop_value,
                    },
                    "actor": {"userId": "user-123"},
                }
            ),
        )

        assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_autocomplete_raw_food_props(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    forbidden_props: tuple[tuple[str, object], ...] = (
        ("query", "owies"),
        ("displayName", "Owies lokalny"),
        ("ingredientProductId", "ingredient-product-1"),
        ("nutritionPer100", 100),
        ("kcal", 389),
        ("protein", 16.9),
        ("barcode", "5901234123457"),
    )

    for prop_key, prop_value in forbidden_props:
        response = client.post(
            "/api/v2/telemetry/events/batch",
            headers=auth_headers("user-123"),
            json=build_payload(
                {
                    "eventId": f"evt-autocomplete-forbidden-{prop_key}",
                    "name": "autocomplete_search_outcome",
                    "props": {
                        "surface": "manual_ingredient_sheet",
                        "outcome": "results",
                        "queryLengthBucket": "4_8",
                        "resultCountBucket": "4_6",
                        "sourceClass": "remote",
                        "latencyBucket": "250_750_ms",
                        prop_key: prop_value,
                    },
                    "actor": {"userId": "user-123"},
                }
            ),
        )

        assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_ingredient_product_create_raw_food_props(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    forbidden_props: tuple[tuple[str, object], ...] = (
        ("query", "owies"),
        ("rawQuery", "owies"),
        ("normalizedQuery", "owies"),
        ("displayName", "Owies lokalny"),
        ("ingredientName", "Owies"),
        ("productName", "Owies lokalny"),
        ("ingredientProductId", "ingredient-product-1"),
        ("productId", "product-1"),
        ("barcode", "5901234123457"),
        ("nutritionPer100", {"kcal": 389}),
        ("kcal", 389),
        ("protein", 16.9),
        ("carbs", 66.3),
        ("fat", 6.9),
        ("sourceRef", "source-ref-1"),
        ("memoryId", "memory-1"),
    )

    for prop_key, prop_value in forbidden_props:
        response = client.post(
            "/api/v2/telemetry/events/batch",
            headers=auth_headers("user-123"),
            json=build_payload(
                {
                    "eventId": f"evt-create-forbidden-{prop_key}",
                    "name": "ingredient_product_create_outcome",
                    "props": {
                        "surface": "manual_ingredient_sheet",
                        "outcome": "failed",
                        prop_key: prop_value,
                    },
                    "actor": {"userId": "user-123"},
                }
            ),
        )

        assert response.status_code == 422
    assert firestore_client.storage == {}


def test_telemetry_batch_rejects_payload_or_batch_that_is_too_large(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()
    oversized_events = [
        {
            "eventId": f"evt-{index}",
            "name": "meal_logged",
            "ts": "2026-03-18T12:00:00Z",
        }
        for index in range(51)
    ]

    batch_too_large = client.post(
        "/api/v2/telemetry/events/batch",
        json={
            "sessionId": "sess-1",
            "app": {"platform": "ios", "appVersion": "1.2.3", "build": "45"},
            "device": {"locale": "pl-PL", "tzOffsetMin": 60},
            "events": oversized_events,
        },
    )

    assert batch_too_large.status_code == 422


def test_telemetry_batch_returns_413_when_serialized_batch_payload_is_too_large(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "MAX_BATCH_PAYLOAD_BYTES", 256)
    client = create_test_client()

    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload(),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Telemetry payload is too large"}


def test_telemetry_batch_is_noop_when_feature_flag_is_disabled(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=False)
    get_firestore = mocker.patch("app.services.telemetry_service.get_firestore")
    client = create_test_client()

    response = client.post("/api/v2/telemetry/events/batch", json=build_payload())

    assert response.status_code == 202
    assert response.json() == {
        "acceptedCount": 0,
        "duplicateCount": 0,
        "rejectedCount": 0,
        "rejectedEvents": [],
    }
    get_firestore.assert_not_called()


def test_telemetry_daily_summary_returns_grouped_counts(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    mocker.patch(
        "app.services.telemetry_service.utc_now",
        return_value=telemetry_service.datetime(2026, 3, 18, 23, 59, 59),
    )
    firestore_client = FakeFirestoreClient()
    firestore_client.storage.update(
        {
            "evt-1": {
                "eventId": "evt-1",
                "name": "meal_logged",
                "ts": "2026-03-18T09:00:00Z",
                "userHash": telemetry_service.build_user_hash("user-123"),
            },
            "evt-2": {
                "eventId": "evt-2",
                "name": "onboarding_completed",
                "ts": "2026-03-18T10:00:00Z",
                "userHash": telemetry_service.build_user_hash("user-123"),
            },
            "evt-3": {
                "eventId": "evt-3",
                "name": "meal_logged",
                "ts": "2026-03-17T10:00:00Z",
                "userHash": telemetry_service.build_user_hash("user-123"),
            },
            "evt-4": {
                "eventId": "evt-4",
                "name": "meal_logged",
                "ts": "2026-03-18T10:00:00Z",
                "userHash": telemetry_service.build_user_hash("other-user"),
            },
        }
    )
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    response = client.get(
        "/api/v2/telemetry/events/summary/daily?days=7",
        headers=auth_headers("user-123"),
    )

    assert response.status_code == 200
    assert response.json()["days"] == 7
    assert response.json()["buckets"] == [
        {
            "day": "2026-03-17",
            "totalEvents": 1,
            "eventCounts": [{"name": "meal_logged", "count": 1}],
        },
        {
            "day": "2026-03-18",
            "totalEvents": 2,
            "eventCounts": [
                {"name": "meal_logged", "count": 1},
                {"name": "onboarding_completed", "count": 1},
            ],
        },
    ]


def test_telemetry_batch_returns_429_when_rate_limit_is_exceeded(
    mocker: MockerFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "RATE_LIMIT_MAX_REQUESTS", 2)
    client = create_test_client()
    payload = build_payload()

    assert client.post("/api/v2/telemetry/events/batch", json=payload).status_code == 202
    assert (
        client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"eventId": "evt-2"}),
        ).status_code
        == 202
    )
    response = client.post(
        "/api/v2/telemetry/events/batch",
        json=build_payload({"eventId": "evt-3"}),
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many telemetry requests"}


def test_successful_ingest_logs_batch_summary(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    caplog: LogCaptureFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    with caplog.at_level(logging.INFO, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(),
            headers=auth_headers("user-obs-1"),
        )

    assert response.status_code == 202
    assert any("telemetry.ingest.ok" in record.message for record in caplog.records)


def test_rejected_event_logs_warning_per_event(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    client = create_test_client()

    with caplog.at_level(logging.WARNING, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"name": "bad_event"}),
        )

    assert response.status_code == 202
    rejected_records = [r for r in caplog.records if "telemetry.ingest.rejected" in r.message]
    assert len(rejected_records) == 1


def test_rate_limit_hit_logs_warning(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    firestore_client = FakeFirestoreClient()
    mocker.patch("app.services.telemetry_service.get_firestore", return_value=firestore_client)
    mocker.patch.object(telemetry_service, "RATE_LIMIT_MAX_REQUESTS", 1)
    client = create_test_client()

    client.post("/api/v2/telemetry/events/batch", json=build_payload())

    with caplog.at_level(logging.WARNING, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload({"eventId": "evt-2"}),
        )

    assert response.status_code == 429
    rate_records = [r for r in caplog.records if "telemetry.ingest.rate_limited" in r.message]
    assert len(rate_records) == 1


def test_firestore_failure_logs_error_and_returns_500(
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
) -> None:
    reset_telemetry_state()
    setup_telemetry_enabled(mocker, enabled=True)
    mocker.patch(
        "app.services.telemetry_service.get_firestore",
        return_value=FailingFirestoreClient(),
    )
    client = create_test_client()

    with caplog.at_level(logging.ERROR, logger="app.services.telemetry_service"):
        response = client.post(
            "/api/v2/telemetry/events/batch",
            json=build_payload(),
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to ingest telemetry batch"}
    assert any(
        "telemetry.ingest.firestore_error" in record.message for record in caplog.records
    )
