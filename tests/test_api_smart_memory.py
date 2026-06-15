from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.main import app
from app.services.smart_memory_service import (
    SmartMemoryMutationDedupeConflictError,
    SmartMemoryNotFoundError,
)
from tests.types import AuthHeaders

client = TestClient(app)


def _item_payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "memoryItemId": "portion-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "active",
        "stateReason": "threshold_met",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
        "userValue": {"amount": 60, "unit": "g"},
        "evidenceSummary": {"supportingEventCount": 3, "distinctDayCount": 3},
        "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "source-hash-1"}],
        "threshold": {"requiredEventCount": 3},
        "confidence": {"sourceConfidence": "high"},
        "confidenceReasonCodes": ["distinct_days_met"],
        "control": {},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "lastEvaluatedAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 1,
        **(overrides or {}),
    }


def _candidate_payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "candidateId": "candidate-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "candidate",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
        "evidenceSummary": {"supportingEventCount": 1, "distinctDayCount": 1},
        "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "source-hash-1"}],
        "confidenceReasonCodes": ["single_observation"],
        "suppressionChecks": {"deletedSuppressed": False},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-01T10:00:00.000Z",
        "firstSeenAt": "2026-06-01T10:00:00.000Z",
        "lastSeenAt": "2026-06-01T10:00:00.000Z",
        "serverRevision": 1,
        **(overrides or {}),
    }


def test_list_smart_memory_items_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_items = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.list_items",
        return_value=[_item_payload()],
    )

    response = client.get(
        "/api/v2/users/me/smart-memory/items?limit=25",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["memoryItemId"] == "portion-oats"
    list_items.assert_awaited_once_with("route-user-1", limit_count=25)


def test_delete_smart_memory_item_returns_suppressed_state(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    delete_item = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.delete_item",
        return_value={
            "document": _item_payload(
                {
                    "state": "deleted_suppressed",
                    "deletedAt": "2026-06-04T10:00:00.000Z",
                    "evidenceSummary": {},
                    "sourceRefs": [],
                    "userValue": {},
                    "serverRevision": 2,
                }
            ),
            "applied": True,
        },
    )

    response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/delete",
        json={"clientMutationId": "memory-delete-1"},
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["item"]["state"] == "deleted_suppressed"
    assert body["item"]["evidenceSummary"] == {}
    assert body["item"]["sourceRefs"] == []
    delete_item.assert_awaited_once_with(
        "route-user-1",
        "portion-oats",
        client_mutation_id="memory-delete-1",
    )


def test_smart_memory_mutation_conflict_returns_409(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.mute_item",
        side_effect=SmartMemoryMutationDedupeConflictError("clientMutationId conflict"),
    )

    response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/mute",
        json={"clientMutationId": "memory-mute-1"},
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "clientMutationId conflict"}


def test_source_deleted_requires_hash_only_source_ref(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mark_source_deleted = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.mark_source_deleted"
    )

    missing_response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/source-deleted",
        json={"clientMutationId": "source-delete-1"},
        headers=auth_headers("route-user-1"),
    )
    raw_response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/source-deleted",
        json={
            "clientMutationId": "source-delete-2",
            "sourceRef": {"kind": "meal", "mealId": "meal-1"},
        },
        headers=auth_headers("route-user-1"),
    )
    extra_response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/source-deleted",
        json={
            "clientMutationId": "source-delete-3",
            "sourceRef": {
                "kind": "meal_portion_observation",
                "sourceHash": "source-hash-1",
                "extra": "not-allowed",
            },
        },
        headers=auth_headers("route-user-1"),
    )

    assert missing_response.status_code == 422
    assert raw_response.status_code == 422
    assert extra_response.status_code == 422
    mark_source_deleted.assert_not_awaited()


def test_source_deleted_passes_hashed_source_ref_to_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mark_source_deleted = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.mark_source_deleted",
        return_value={
            "document": _item_payload(
                {
                    "state": "source_deleted",
                    "sourceDeletedAt": "2026-06-04T10:00:00.000Z",
                    "sourceRefs": [
                        {
                            "kind": "meal_portion_observation",
                            "sourceHash": "source-hash-1",
                        }
                    ],
                    "serverRevision": 2,
                }
            ),
            "applied": True,
        },
    )

    response = client.post(
        "/api/v2/users/me/smart-memory/items/portion-oats/source-deleted",
        json={
            "clientMutationId": "source-delete-1",
            "sourceRef": {
                "kind": "meal_portion_observation",
                "sourceHash": "source-hash-1",
            },
        },
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["item"]["state"] == "source_deleted"
    mark_source_deleted.assert_awaited_once_with(
        "route-user-1",
        "portion-oats",
        client_mutation_id="source-delete-1",
        source_ref={
            "kind": "meal_portion_observation",
            "sourceHash": "source-hash-1",
        },
    )


def test_get_smart_memory_item_not_found_returns_404(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.get_item",
        side_effect=SmartMemoryNotFoundError("Smart Memory item was not found"),
    )

    response = client.get(
        "/api/v2/users/me/smart-memory/items/missing",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Smart Memory item was not found"}


def test_upsert_candidate_does_not_return_active_memory(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_candidate = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.upsert_candidate",
        return_value={"document": _candidate_payload(), "applied": True},
    )

    response = client.post(
        "/api/v2/users/me/smart-memory/candidates",
        json={
            "clientMutationId": "candidate-upsert-1",
            "candidateId": "candidate-oats",
            "memoryType": "typical_portion",
            "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
            "evidenceSummary": {"supportingEventCount": 1, "distinctDayCount": 1},
            "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "source-hash-1"}],
            "confidenceReasonCodes": ["single_observation"],
            "suppressionChecks": {"deletedSuppressed": False},
            "firstSeenAt": "2026-06-01T10:00:00.000Z",
            "lastSeenAt": "2026-06-01T10:00:00.000Z",
        },
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["candidate"]["state"] == "candidate"
    assert "item" not in response.json()
    upsert_candidate.assert_awaited_once()


def test_disable_smart_memory_settings_uses_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    update_settings = mocker.patch(
        "app.api.routes.smart_memory.smart_memory_service.update_settings",
        return_value={
            "document": {
                "ownerUserId": "route-user-1",
                "enabled": False,
                "disabledAt": "2026-06-04T10:00:00.000Z",
                "updatedAt": "2026-06-04T10:00:00.000Z",
                "serverRevision": 2,
                "clientMutationId": "settings-disable-1",
            },
            "applied": True,
        },
    )

    response = client.patch(
        "/api/v2/users/me/smart-memory/settings",
        json={"clientMutationId": "settings-disable-1", "enabled": False},
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["settings"]["enabled"] is False
    update_settings.assert_awaited_once()
