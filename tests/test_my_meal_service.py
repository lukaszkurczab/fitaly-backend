import asyncio
from io import BytesIO
from typing import Any

import pytest
from pytest_mock import MockerFixture

from app.services import meal_service, my_meal_service


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []

    def _begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _commit(self) -> list[object]:
        return []

    def _rollback(self) -> None:
        return None

    def _clean_up(self) -> None:
        return None

    def set(
        self,
        document_ref: object,
        data: dict[str, object],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str((data or {}).get("cloudId") or (data or {}).get("mealId") or "saved-1")
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _wire_saved_meal_firestore_refs(
    mocker: MockerFixture,
    *,
    meal_snapshot: object,
    mutation_snapshot: object | None = None,
):
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    my_meals_collection = mocker.Mock()
    mutations_collection = mocker.Mock()
    meal_ref = mocker.Mock()
    mutation_ref = mocker.Mock()
    transaction = FakeTransaction()

    client.collection.return_value = users_collection
    client.transaction.return_value = transaction
    users_collection.document.return_value = user_ref

    def collection_for_name(name: str) -> object:
        if name == "mealTemplates":
            return my_meals_collection
        if name == "mealMutationDedupe":
            return mutations_collection
        return mocker.Mock()

    user_ref.collection.side_effect = collection_for_name
    my_meals_collection.document.return_value = meal_ref
    mutations_collection.document.return_value = mutation_ref
    meal_ref.get.return_value = meal_snapshot
    mutation_ref.get.return_value = mutation_snapshot or _build_snapshot(mocker, exists=False)
    return client, meal_ref, mutation_ref, transaction


def _base_saved_meal_payload(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "cloudId": "saved-1",
        "mealId": "saved-1",
        "timestamp": "2026-03-03T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "source": "saved",
        "deleted": False,
        "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        **(overrides or {}),
    }


def test_normalize_saved_meal_preserves_upload_shaped_storage_path() -> None:
    _meal_id, document = my_meal_service._normalize_saved_meal_document(
        "user-1",
        _base_saved_meal_payload(
            {
                "imageRef": {
                    "imageId": "image-1",
                    "storagePath": "mealTemplates/user-1/saved-1-image-1.png",
                    "downloadUrl": "https://cdn/saved.jpg",
                },
            }
        ),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
        "storagePath": "mealTemplates/user-1/saved-1-image-1.png",
        "downloadUrl": "https://cdn/saved.jpg",
    }


@pytest.mark.parametrize(
    "storage_path",
    [
        "myMeals/user-1/saved-1-image-1.png",
        "myMeals/unknown/image-1.jpg",
        "myMeals/other-user/image-1.jpg",
        "mealTemplates/unknown/image-1.jpg",
        "mealTemplates/other-user/image-1.jpg",
        "mealTemplates/user-1/custom-image.jpg",
        "mealTemplates/user-1/other-saved-1-image-1.jpg",
        "mealTemplates/user-1/saved-1-other-image-1.jpg",
        "mealTemplates/user-1/saved-1-image-1",
        "mealTemplates/user-1/nested/saved-1-image-1.jpg",
        "meals/user-1/image-1.jpg",
        "images/image-1.jpg",
    ],
)
def test_normalize_saved_meal_omits_unsafe_storage_path(
    storage_path: str,
) -> None:
    _meal_id, document = my_meal_service._normalize_saved_meal_document(
        "user-1",
        _base_saved_meal_payload(
            {
                "imageRef": {
                    "imageId": "image-1",
                    "storagePath": storage_path,
                    "downloadUrl": "https://cdn/saved.jpg",
                },
            }
        ),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
        "downloadUrl": "https://cdn/saved.jpg",
    }


def test_normalize_saved_meal_does_not_fabricate_missing_storage_path() -> None:
    _meal_id, document = my_meal_service._normalize_saved_meal_document(
        "user-1",
        _base_saved_meal_payload({"imageRef": {"imageId": "image-1"}}),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
    }


def test_upsert_saved_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "saved-1",
                "mealId": "saved-1",
                "userUid": "user-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00.000Z",
                "source": "saved",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    result = asyncio.run(
        my_meal_service.upsert_saved_meal(
            "user-1",
            {
                "cloudId": "saved-1",
                "mealId": "saved-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "clientMutationId": "mutation-saved-upsert-stale",
            },
        ),
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [mutation_ref]


def test_upsert_saved_meal_duplicate_replay_uses_dedupe_record_without_second_write(
    mocker: MockerFixture,
) -> None:
    first_client, meal_ref, mutation_ref, first_transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.my_meal_service.get_firestore",
        return_value=first_client,
    )
    payload: dict[str, object] = {
        "cloudId": "saved-1",
        "mealId": "saved-1",
        "timestamp": "2026-03-03T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "deleted": False,
        "clientMutationId": "mutation-saved-upsert-replay",
    }

    first_result = asyncio.run(my_meal_service.upsert_saved_meal("user-1", payload))
    mutation_record = first_transaction.set_calls[1][1]

    second_client, _second_meal_ref, _second_mutation_ref, second_transaction = (
        _wire_saved_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(my_meal_service.upsert_saved_meal("user-1", payload))

    assert first_result == second_result
    assert [call[0] for call in first_transaction.set_calls] == [meal_ref, mutation_ref]
    assert second_transaction.set_calls == []


def test_upsert_saved_meal_rejects_reused_client_mutation_id_for_different_payload(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        mutation_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "clientMutationId": "mutation-saved-reused",
                "kind": "saved_meal_upsert",
                "mealId": "saved-1",
                "payloadHash": "different-payload",
                "resultMeal": {"id": "saved-1"},
            },
        ),
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    with pytest.raises(meal_service.MealMutationDedupeConflictError):
        asyncio.run(
            my_meal_service.upsert_saved_meal(
                "user-1",
                {
                    "cloudId": "saved-1",
                    "mealId": "saved-1",
                    "timestamp": "2026-03-03T12:00:00.000Z",
                    "type": "lunch",
                    "ingredients": [],
                    "createdAt": "2026-03-03T12:00:00.000Z",
                    "updatedAt": "2026-03-03T12:30:00.000Z",
                    "deleted": False,
                    "clientMutationId": "mutation-saved-reused",
                },
            )
        )

    assert transaction.set_calls == []


class _FakeChangesSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any]) -> None:
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeChangesQuery:
    def __init__(self, snapshots: list[_FakeChangesSnapshot]) -> None:
        self._snapshots = snapshots
        self._limit: int | None = None
        self._start_after: list[str] | None = None
        self.stream_called = False

    def order_by(self, field: str, direction: object | None = None):
        del field, direction
        return self

    def start_after(self, cursor: list[str]):
        self._start_after = cursor
        return self

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def stream(self):
        self.stream_called = True
        items = sorted(
            self._snapshots,
            key=lambda snapshot: (str(snapshot.to_dict().get("updatedAt") or ""), snapshot.id),
        )
        if self._start_after:
            cursor_key = tuple(self._start_after)
            items = [
                snapshot
                for snapshot in items
                if (str(snapshot.to_dict().get("updatedAt") or ""), snapshot.id) > cursor_key
            ]
        if self._limit is not None:
            items = items[: self._limit]
        return iter(items)


def _changes_doc(
    *,
    timestamp: str,
    updated_at: str,
    deleted: bool = False,
) -> dict[str, Any]:
    return {
        "loggedAt": timestamp,
        "timestamp": timestamp,
        "dayKey": timestamp[:10],
        "type": "lunch",
        "ingredients": [],
        "createdAt": timestamp,
        "updatedAt": updated_at,
        "source": "saved",
        "deleted": deleted,
        "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
    }


def _wire_changes_collection(
    mocker: MockerFixture,
    query: _FakeChangesQuery,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = query
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)


def test_list_changes_paginates_same_updated_at_without_duplicates_or_gaps(
    mocker: MockerFixture,
) -> None:
    same_updated_at = "2026-04-20T10:00:00.000Z"
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "saved-c",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "saved-a",
                _changes_doc(
                    timestamp="2026-04-20T07:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "saved-b",
                _changes_doc(
                    timestamp="2026-04-20T08:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "saved-d",
                _changes_doc(
                    timestamp="2026-04-20T11:00:00.000Z",
                    updated_at="2026-04-20T11:00:00.000Z",
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    page1, cursor1 = asyncio.run(my_meal_service.list_changes("user-1", limit_count=2))
    page2, cursor2 = asyncio.run(
        my_meal_service.list_changes("user-1", limit_count=2, after_cursor=cursor1)
    )

    ids = [item["cloudId"] for item in [*page1, *page2]]
    assert ids == ["saved-a", "saved-b", "saved-c", "saved-d"]
    assert len(ids) == len(set(ids))
    assert cursor1 == "2026-04-20T10:00:00.000Z|saved-b"
    assert cursor2 == "2026-04-20T11:00:00.000Z|saved-d"


def test_list_changes_returns_deleted_tombstones(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "saved-deleted",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at="2026-04-20T10:00:00.000Z",
                    deleted=True,
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    items, next_cursor = asyncio.run(my_meal_service.list_changes("user-1", limit_count=10))

    assert [item["cloudId"] for item in items] == ["saved-deleted"]
    assert items[0]["deleted"] is True
    assert items[0]["source"] == "saved"
    assert next_cursor is None


def test_list_changes_rejects_timestamp_only_cursor_without_streaming(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery([])
    _wire_changes_collection(mocker, query)

    with pytest.raises(ValueError, match="Invalid cursor"):
        asyncio.run(
            my_meal_service.list_changes(
                "user-1",
                limit_count=2,
                after_cursor="2026-04-20T10:00:00.000Z",
            )
        )

    assert query.stream_called is False


def test_mark_deleted_creates_tombstone_when_saved_meal_is_missing(
    mocker: MockerFixture,
) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    result = asyncio.run(
        my_meal_service.mark_deleted(
            "user-1",
            "saved-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-saved-delete-1",
        )
    )

    assert result["id"] == "saved-1"
    assert result["mealId"] == "saved-1"
    assert result["cloudId"] == "saved-1"
    assert result["loggedAt"] == "2026-03-03T12:30:00.000Z"
    assert result["timestamp"] == "2026-03-03T12:30:00.000Z"
    assert result["dayKey"] == "2026-03-03"
    assert result["source"] == "saved"
    assert result["deleted"] is True
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
    assert transaction.set_calls[0] == (
        meal_ref,
        {
            "loggedAt": "2026-03-03T12:30:00.000Z",
            "dayKey": "2026-03-03",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "other",
            "name": None,
            "ingredients": [],
            "createdAt": "2026-03-03T12:30:00.000Z",
            "updatedAt": "2026-03-03T12:30:00.000Z",
            "source": "saved",
            "inputMethod": None,
            "aiMeta": None,
            "imageRef": None,
            "notes": None,
            "tags": [],
            "deleted": True,
            "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
        },
        True,
    )


def test_mark_deleted_keeps_newer_remote_saved_meal(mocker: MockerFixture) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "saved-1",
                "mealId": "saved-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00.000Z",
                "source": "saved",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    result = asyncio.run(
        my_meal_service.mark_deleted(
            "user-1",
            "saved-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-saved-delete-stale",
        )
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    assert result["deleted"] is False
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [mutation_ref]


def test_mark_deleted_duplicate_replay_uses_dedupe_record_without_second_write(
    mocker: MockerFixture,
) -> None:
    first_client, meal_ref, mutation_ref, first_transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.my_meal_service.get_firestore",
        return_value=first_client,
    )

    first_result = asyncio.run(
        my_meal_service.mark_deleted(
            "user-1",
            "saved-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-saved-delete-replay",
        )
    )
    mutation_record = first_transaction.set_calls[1][1]

    second_client, _second_meal_ref, _second_mutation_ref, second_transaction = (
        _wire_saved_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(
        my_meal_service.mark_deleted(
            "user-1",
            "saved-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-saved-delete-replay",
        )
    )

    assert first_result == second_result
    assert [call[0] for call in first_transaction.set_calls] == [meal_ref, mutation_ref]
    assert second_transaction.set_calls == []


def test_mark_deleted_rejects_reused_client_mutation_id_for_different_kind(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, transaction = _wire_saved_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        mutation_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "clientMutationId": "mutation-saved-kind-reused",
                "kind": "saved_meal_upsert",
                "mealId": "saved-1",
                "payloadHash": "different-payload",
                "resultMeal": {"id": "saved-1"},
            },
        ),
    )
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    with pytest.raises(meal_service.MealMutationDedupeConflictError):
        asyncio.run(
            my_meal_service.mark_deleted(
                "user-1",
                "saved-1",
                updated_at="2026-03-03T12:30:00.000Z",
                client_mutation_id="mutation-saved-kind-reused",
            )
        )

    assert transaction.set_calls == []


def test_upload_photo_returns_storage_download_url(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    blob = mocker.Mock()
    bucket.name = "demo.appspot.com"
    bucket.blob.return_value = blob
    mocker.patch("app.services.meal_storage.get_storage_bucket", return_value=bucket)

    upload = mocker.Mock()
    upload.filename = "saved.jpg"
    upload.content_type = "image/jpeg"
    upload.file = BytesIO(b"jpeg-bytes")

    payload = asyncio.run(my_meal_service.upload_photo("user-1", "saved-1", upload))

    bucket.blob.assert_called_once()
    blob.upload_from_file.assert_called_once()
    blob.patch.assert_called_once_with()
    assert payload["mealId"] == "saved-1"
    assert payload["imageId"]
    assert payload["storagePath"] == bucket.blob.call_args.args[0]
    assert payload["storagePath"].startswith("mealTemplates/user-1/saved-1-")
    assert payload["storagePath"].endswith(f"{payload['imageId']}.jpg")
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
    )
