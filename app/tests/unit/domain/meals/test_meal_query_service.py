from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
from google.api_core.exceptions import FailedPrecondition
from pytest import MonkeyPatch

from app.core.exceptions import FirestoreServiceError
from app.domain.meals.services.meal_query_service import MealQueryService


@dataclass
class _FakeSnapshot:
    id: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class _FakeQuery:
    def __init__(
        self,
        *,
        datasets: dict[str, list[_FakeSnapshot]],
        filters: list[Any] | None = None,
        fail_day_key: bool = False,
        fail_logged_at: bool = False,
        unfiltered_stream_calls: list[str] | None = None,
        stream_field_sequences: list[tuple[str, ...]] | None = None,
    ) -> None:
        self._datasets = datasets
        self._filters = list(filters or [])
        self._fail_day_key = fail_day_key
        self._fail_logged_at = fail_logged_at
        self._unfiltered_stream_calls = (
            unfiltered_stream_calls if unfiltered_stream_calls is not None else []
        )
        self._stream_field_sequences = (
            stream_field_sequences if stream_field_sequences is not None else []
        )

    @property
    def unfiltered_stream_count(self) -> int:
        return len(self._unfiltered_stream_calls)

    @property
    def stream_field_sequences(self) -> list[tuple[str, ...]]:
        return list(self._stream_field_sequences)

    def where(self, *, filter: Any) -> "_FakeQuery":
        return _FakeQuery(
            datasets=self._datasets,
            filters=[*self._filters, filter],
            fail_day_key=self._fail_day_key,
            fail_logged_at=self._fail_logged_at,
            unfiltered_stream_calls=self._unfiltered_stream_calls,
            stream_field_sequences=self._stream_field_sequences,
        )

    def stream(self):
        field_paths: list[str] = []
        for item in self._filters:
            field_path = getattr(item, "field_path", None)
            if isinstance(field_path, str) and field_path:
                field_paths.append(field_path)
        field_sequence = tuple(field_paths)
        self._stream_field_sequences.append(field_sequence)

        if field_sequence == ("dayKey", "dayKey"):
            if self._fail_day_key:
                raise FailedPrecondition("missing dayKey index")
            return iter(self._datasets.get("dayKey", []))

        if field_sequence == ("loggedAt", "loggedAt"):
            if self._fail_logged_at:
                raise FailedPrecondition("missing loggedAt index")
            return iter(self._datasets.get("loggedAt", []))

        if field_sequence == ():
            self._unfiltered_stream_calls.append("all")
            return iter(self._datasets.get("all", []))

        raise AssertionError(f"Unexpected query field sequence: {field_sequence!r}")


class _FakeCollection(_FakeQuery):
    pass


def _build_service() -> MealQueryService:
    # Prevent test from touching real Firebase credentials in __init__.
    return MealQueryService(firestore_client=cast(Any, object()))


async def test_get_meals_in_range_includes_logged_at_records_without_day_key(
    monkeypatch: MonkeyPatch,
) -> None:
    service = _build_service()
    collection = _FakeCollection(
        datasets={
            "dayKey": [],
            "loggedAt": [
                _FakeSnapshot(
                    id="meal-1",
                    payload={
                        "loggedAt": "2026-04-23T10:15:00Z",
                        "totals": {"kcal": 520, "protein": 33, "fat": 18, "carbs": 44},
                    },
                )
            ],
            "all": [],
        }
    )

    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    records = await service.get_meals_in_range(
        user_id="user-1",
        start_date="2026-04-21",
        end_date="2026-04-27",
        timezone="Europe/Warsaw",
    )

    assert len(records) == 1
    assert records[0].id == "meal-1"
    assert records[0].day_key == "2026-04-23"
    assert records[0].kcal == 520
    assert collection.stream_field_sequences == [
        ("dayKey", "dayKey"),
        ("loggedAt", "loggedAt"),
    ]
    assert collection.unfiltered_stream_count == 0


async def test_get_meals_in_range_ignores_deleted_records_even_when_field_missing_on_others(
    monkeypatch: MonkeyPatch,
) -> None:
    service = _build_service()
    collection = _FakeCollection(
        datasets={
            "dayKey": [
                _FakeSnapshot(
                    id="meal-deleted",
                    payload={
                        "dayKey": "2026-04-24",
                        "timestamp": "2026-04-24T10:00:00Z",
                        "deleted": True,
                        "totals": {"kcal": 300, "protein": 10, "fat": 10, "carbs": 30},
                    },
                ),
                _FakeSnapshot(
                    id="meal-active",
                    payload={
                        "dayKey": "2026-04-24",
                        "timestamp": "2026-04-24T12:00:00Z",
                        "totals": {"kcal": 640, "protein": 40, "fat": 20, "carbs": 70},
                    },
                ),
            ],
            "loggedAt": [],
            "all": [],
        }
    )

    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    records = await service.get_meals_in_range(
        user_id="user-1",
        start_date="2026-04-21",
        end_date="2026-04-27",
        timezone="Europe/Warsaw",
    )

    assert [record.id for record in records] == ["meal-active"]
    assert collection.stream_field_sequences == [
        ("dayKey", "dayKey"),
        ("loggedAt", "loggedAt"),
    ]
    assert collection.unfiltered_stream_count == 0


async def test_get_meals_in_range_raises_when_range_index_is_missing_without_collection_scan(
    monkeypatch: MonkeyPatch,
) -> None:
    service = _build_service()
    collection = _FakeCollection(
        datasets={
            "dayKey": [],
            "loggedAt": [],
            "all": [
                _FakeSnapshot(
                    id="meal-1",
                    payload={
                        "timestamp": "2026-04-22T08:00:00Z",
                        "totals": {"kcal": 400, "proteinG": 25, "fatG": 10, "carbsG": 45},
                    },
                )
            ],
        },
        fail_day_key=True,
        fail_logged_at=True,
    )

    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    with pytest.raises(FirestoreServiceError):
        await service.get_meals_in_range(
            user_id="user-1",
            start_date="2026-04-21",
            end_date="2026-04-27",
            timezone="Europe/Warsaw",
        )

    assert collection.stream_field_sequences == [("dayKey", "dayKey")]
    assert collection.unfiltered_stream_count == 0


async def test_get_meals_in_range_raises_when_logged_at_index_is_missing_after_day_key_success(
    monkeypatch: MonkeyPatch,
) -> None:
    service = _build_service()
    collection = _FakeCollection(
        datasets={
            "dayKey": [
                _FakeSnapshot(
                    id="meal-day-key",
                    payload={
                        "dayKey": "2026-04-22",
                        "loggedAt": "2026-04-22T08:00:00Z",
                        "totals": {"kcal": 400, "proteinG": 25, "fatG": 10, "carbsG": 45},
                    },
                )
            ],
            "loggedAt": [],
            "all": [
                _FakeSnapshot(
                    id="meal-scan-only",
                    payload={
                        "loggedAt": "2026-04-23T08:00:00Z",
                        "totals": {"kcal": 500, "proteinG": 30, "fatG": 12, "carbsG": 55},
                    },
                )
            ],
        },
        fail_logged_at=True,
    )

    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    with pytest.raises(FirestoreServiceError):
        await service.get_meals_in_range(
            user_id="user-1",
            start_date="2026-04-21",
            end_date="2026-04-27",
            timezone="Europe/Warsaw",
        )

    assert collection.stream_field_sequences == [
        ("dayKey", "dayKey"),
        ("loggedAt", "loggedAt"),
    ]
    assert collection.unfiltered_stream_count == 0
