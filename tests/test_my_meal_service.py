import asyncio
from io import BytesIO
from typing import Any

from pytest_mock import MockerFixture

from app.services import my_meal_service


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


def test_upsert_saved_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    my_meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = my_meals_collection
    my_meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
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
            },
        ),
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()


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


def test_mark_deleted_creates_tombstone_when_saved_meal_is_missing(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    my_meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = my_meals_collection
    my_meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    result = asyncio.run(
        my_meal_service.mark_deleted(
            "user-1",
            "saved-1",
            updated_at="2026-03-03T12:30:00.000Z",
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
    meal_ref.set.assert_called_once_with(
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
        merge=True,
    )


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
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
    )
