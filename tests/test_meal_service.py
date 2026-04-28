import asyncio
from typing import Any

from google.api_core.exceptions import FailedPrecondition
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import meal_service


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str((data or {}).get("cloudId") or (data or {}).get("mealId") or "meal-1")
    snapshot.to_dict.return_value = data or {}
    return snapshot


def test_upsert_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T13:00:00.000Z",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        )
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()
    sync_streak.assert_not_called()


def test_upsert_meal_compares_non_canonical_updated_at_as_utc(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T13:00:00+01:00",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        )
    )

    assert result["updatedAt"] == "2026-03-03T12:30:00.000Z"
    meal_ref.set.assert_called_once()
    written_document = meal_ref.set.call_args.args[0]
    assert written_document["updatedAt"] == "2026-03-03T12:30:00.000Z"
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_mark_deleted_creates_tombstone_when_meal_is_missing(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
        )
    )

    assert result["id"] == "meal-1"
    assert result["mealId"] == "meal-1"
    assert result["cloudId"] == "meal-1"
    assert result["loggedAt"] == "2026-03-03T12:30:00.000Z"
    assert result["timestamp"] == "2026-03-03T12:30:00.000Z"
    assert result["dayKey"] == "2026-03-03"
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
            "source": None,
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
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_mark_deleted_compares_non_canonical_updated_at_as_utc(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T13:00:00+01:00",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
        )
    )

    assert result["updatedAt"] == "2026-03-03T12:30:00.000Z"
    assert result["deleted"] is True
    meal_ref.set.assert_called_once()
    written_document = meal_ref.set.call_args.args[0]
    assert written_document["updatedAt"] == "2026-03-03T12:30:00.000Z"
    assert written_document["deleted"] is True
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_upsert_meal_persists_input_method_and_ai_meta(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "inputMethod": "photo",
                "aiMeta": {
                    "model": "gpt-4o-mini",
                    "runId": "run-1",
                    "confidence": 0.84,
                    "warnings": ["partial_totals"],
                },
            },
        )
    )

    assert result["inputMethod"] == "photo"
    assert result["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": "run-1",
        "confidence": 0.84,
        "warnings": ["partial_totals"],
    }
    meal_ref.set.assert_called_once_with(
        {
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "lunch",
            "name": None,
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:30:00.000Z",
            "source": None,
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.84,
                "warnings": ["partial_totals"],
            },
            "imageRef": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
        },
        merge=True,
    )
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_upload_photo_returns_storage_download_url(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    upload = mocker.Mock()
    upload.filename = "meal.jpg"
    upload.content_type = "image/jpeg"
    upload.file = mocker.Mock()
    mocker.patch("app.services.meal_storage.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(meal_service.upload_photo("user-1", upload))

    bucket.blob.assert_called_once()
    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_called_once_with()
    upload.file.seek.assert_called_once_with(0)
    upload.file.close.assert_called_once_with()
    assert payload["imageId"]
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/meals%2Fuser-1%2F"
    )


def test_resolve_photo_uses_meal_document_photo_url(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }


class _FakeHistorySnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any]) -> None:
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeHistoryQuery:
    def __init__(
        self,
        *,
        snapshots: list[_FakeHistorySnapshot],
        failure: BaseException | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._failure = failure
        self._limit: int | None = None
        self._start_after: list[str] | None = None

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self

    def order_by(self, field: str, direction: object | None = None):
        del field, direction
        return self

    def start_after(self, cursor: list[str]):
        self._start_after = cursor
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def stream(self):
        if self._failure is not None:
            raise self._failure
        items = self._snapshots
        if self._start_after and len(self._start_after) == 3:
            cursor_key = tuple(self._start_after)

            def sort_key(snapshot: _FakeHistorySnapshot) -> tuple[str, str, str]:
                data = snapshot.to_dict()
                logged_at = str(data.get("loggedAt") or data.get("timestamp") or "")
                return (
                    str(data.get("dayKey") or logged_at[:10]),
                    logged_at,
                    snapshot.id,
                )

            items = [snapshot for snapshot in items if sort_key(snapshot) < cursor_key]
        if self._limit is not None:
            items = items[: self._limit]
        return iter(items)


class _FakeHistoryMealsCollection:
    def __init__(
        self,
        *,
        indexed_query: _FakeHistoryQuery,
        degraded_query: _FakeHistoryQuery,
    ) -> None:
        self._indexed_query = indexed_query
        self._degraded_query = degraded_query

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self._indexed_query

    def order_by(self, field: str, direction: object | None = None):
        del field, direction
        return self._degraded_query


def _history_doc(
    *,
    meal_id: str,
    timestamp: str,
    deleted: bool,
    day_key: str | None = None,
) -> dict[str, Any]:
    return {
        "loggedAt": timestamp,
        "timestamp": timestamp,
        "dayKey": day_key or timestamp[:10],
        "type": "lunch",
        "ingredients": [],
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "deleted": deleted,
        "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
    }


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
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)


def test_list_changes_paginates_same_updated_at_without_duplicates_or_gaps(
    mocker: MockerFixture,
) -> None:
    same_updated_at = "2026-04-20T10:00:00.000Z"
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "meal-c",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-a",
                _changes_doc(
                    timestamp="2026-04-20T07:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-b",
                _changes_doc(
                    timestamp="2026-04-20T08:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-d",
                _changes_doc(
                    timestamp="2026-04-20T11:00:00.000Z",
                    updated_at="2026-04-20T11:00:00.000Z",
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    page1, cursor1 = asyncio.run(meal_service.list_changes("user-1", limit_count=2))
    page2, cursor2 = asyncio.run(
        meal_service.list_changes("user-1", limit_count=2, after_cursor=cursor1)
    )

    ids = [item["cloudId"] for item in [*page1, *page2]]
    assert ids == ["meal-a", "meal-b", "meal-c", "meal-d"]
    assert len(ids) == len(set(ids))
    assert cursor1 == "2026-04-20T10:00:00.000Z|meal-b"
    assert cursor2 == "2026-04-20T11:00:00.000Z|meal-d"


def test_list_changes_returns_deleted_tombstones(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "meal-deleted",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at="2026-04-20T10:00:00.000Z",
                    deleted=True,
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    items, next_cursor = asyncio.run(meal_service.list_changes("user-1", limit_count=10))

    assert [item["cloudId"] for item in items] == ["meal-deleted"]
    assert items[0]["deleted"] is True
    assert next_cursor is None


def test_list_changes_rejects_invalid_cursor_without_fallback(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery([])
    _wire_changes_collection(mocker, query)

    with pytest.raises(ValueError, match="Invalid cursor"):
        asyncio.run(
            meal_service.list_changes(
                "user-1",
                limit_count=2,
                after_cursor="2026-04-20T10:00:00.000Z",
            )
        )

    assert query.stream_called is False


def test_list_history_falls_back_on_missing_index_and_still_excludes_deleted(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[],
        failure=FailedPrecondition("The query requires an index."),
    )
    degraded_query = _FakeHistoryQuery(
        snapshots=[
            _FakeHistorySnapshot(
                "meal-deleted",
                _history_doc(
                    meal_id="meal-deleted",
                    timestamp="2026-04-18T09:00:00.000Z",
                    deleted=True,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-2",
                _history_doc(
                    meal_id="meal-2",
                    timestamp="2026-04-18T08:00:00.000Z",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-1",
                _history_doc(
                    meal_id="meal-1",
                    timestamp="2026-04-18T07:00:00.000Z",
                    deleted=False,
                ),
            ),
        ]
    )
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        degraded_query=degraded_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    items, next_cursor = asyncio.run(meal_service.list_history("user-1", limit_count=2))

    assert [item["cloudId"] for item in items] == ["meal-2", "meal-1"]
    assert all(item["deleted"] is False for item in items)
    assert next_cursor == "2026-04-18|2026-04-18T07:00:00.000Z|meal-1"


def test_list_history_filters_by_day_key_without_logged_at_day_fallback(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[
            _FakeHistorySnapshot(
                "meal-in-range",
                _history_doc(
                    meal_id="meal-in-range",
                    timestamp="2026-04-17T22:30:00.000Z",
                    day_key="2026-04-18",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-outside-range",
                _history_doc(
                    meal_id="meal-outside-range",
                    timestamp="2026-04-18T12:00:00.000Z",
                    day_key="2026-04-17",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-missing-day-key",
                {
                    "loggedAt": "2026-04-18T14:00:00.000Z",
                    "timestamp": "2026-04-18T14:00:00.000Z",
                    "type": "lunch",
                    "ingredients": [],
                    "createdAt": "2026-04-18T14:00:00.000Z",
                    "updatedAt": "2026-04-18T14:00:00.000Z",
                    "deleted": False,
                    "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
                },
            ),
            _FakeHistorySnapshot(
                "meal-deleted",
                _history_doc(
                    meal_id="meal-deleted",
                    timestamp="2026-04-18T13:00:00.000Z",
                    day_key="2026-04-18",
                    deleted=True,
                ),
            ),
        ],
    )
    degraded_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        degraded_query=degraded_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    items, next_cursor = asyncio.run(
        meal_service.list_history(
            "user-1",
            limit_count=10,
            day_key_start="2026-04-18",
            day_key_end="2026-04-18",
        )
    )

    assert [item["cloudId"] for item in items] == ["meal-in-range"]
    assert items[0]["loggedAt"] == "2026-04-17T22:30:00.000Z"
    assert next_cursor is None


def test_upsert_meal_requires_explicit_day_key(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    with pytest.raises(ValueError, match="Missing dayKey"):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "mealId": "meal-1",
                    "loggedAt": "2026-04-18T01:30:00.000Z",
                    "type": "snack",
                    "ingredients": [],
                },
            )
        )

    meal_ref.set.assert_not_called()


def test_upsert_meal_rejects_invalid_day_key(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(ValueError, match="dayKey must use YYYY-MM-DD format"):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "mealId": "meal-1",
                    "loggedAt": "2026-04-18T01:30:00.000Z",
                    "dayKey": "2026/04/18",
                    "type": "snack",
                    "ingredients": [],
                },
            )
        )

    meal_ref.set.assert_not_called()


def test_list_history_paginates_composite_cursor_without_duplicates_or_gaps(
    mocker: MockerFixture,
) -> None:
    snapshots = [
        _FakeHistorySnapshot(
            "meal-e",
            _history_doc(
                meal_id="meal-e",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-d",
            _history_doc(
                meal_id="meal-d",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-c",
            _history_doc(
                meal_id="meal-c",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-b",
            _history_doc(
                meal_id="meal-b",
                timestamp="2026-04-18T09:00:00.000Z",
                day_key="2026-04-18",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-a",
            _history_doc(
                meal_id="meal-a",
                timestamp="2026-04-17T09:00:00.000Z",
                day_key="2026-04-17",
                deleted=False,
            ),
        ),
    ]
    indexed_query = _FakeHistoryQuery(snapshots=snapshots)
    degraded_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        degraded_query=degraded_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    page1, cursor1 = asyncio.run(meal_service.list_history("user-1", limit_count=2))
    page2, cursor2 = asyncio.run(
        meal_service.list_history("user-1", limit_count=2, before_cursor=cursor1)
    )
    page3, cursor3 = asyncio.run(
        meal_service.list_history("user-1", limit_count=2, before_cursor=cursor2)
    )

    ids = [item["cloudId"] for item in [*page1, *page2, *page3]]
    assert ids == ["meal-e", "meal-d", "meal-c", "meal-b", "meal-a"]
    assert len(ids) == len(set(ids))
    assert cursor1 == "2026-04-19|2026-04-19T12:00:00.000Z|meal-d"
    assert cursor2 == "2026-04-18|2026-04-18T09:00:00.000Z|meal-b"
    assert cursor3 is None


def test_list_history_raises_firestore_error_on_non_index_failed_precondition(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[],
        failure=FailedPrecondition("Some other precondition failure."),
    )
    degraded_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        degraded_query=degraded_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(meal_service.list_history("user-1", limit_count=5))
