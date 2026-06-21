from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
from typing import Any, cast

import pytest

from app.schemas.known_patterns import (
    KnownPatternCandidateControl,
    KnownPatternCandidateControlRequest,
    KnownPatternReviewDraftRequest,
)
from app.services import known_pattern_service
from app.services.known_pattern_service import (
    KnownPatternNotFoundError,
    evaluate_known_pattern_candidates,
)


class FakeSnapshot:
    def __init__(
        self,
        document_id: str,
        *,
        exists: bool,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.id = document_id
        self.exists = exists
        self._data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class FakeDocumentRef:
    def __init__(self, document_id: str, snapshot: FakeSnapshot | None = None) -> None:
        self.id = document_id
        self.snapshot = snapshot or FakeSnapshot(document_id, exists=False)
        self.set_calls: list[tuple[dict[str, Any], bool | None]] = []

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        return self.snapshot

    def set(self, data: dict[str, Any], merge: bool | None = None) -> None:
        self.set_calls.append((data, merge))
        self.snapshot = FakeSnapshot(self.id, exists=True, data=data)


class FakeCollectionRef:
    def __init__(self, documents: dict[str, FakeDocumentRef] | None = None) -> None:
        self.documents = documents or {}
        self.limit_calls: list[int] = []

    def document(self, document_id: str) -> FakeDocumentRef:
        if document_id not in self.documents:
            self.documents[document_id] = FakeDocumentRef(document_id)
        return self.documents[document_id]

    def stream(self) -> list[FakeSnapshot]:
        return [document.snapshot for document in self.documents.values()]

    def limit(self, count: int) -> "FakeCollectionRef":
        self.limit_calls.append(count)
        return FakeCollectionRef(dict(list(self.documents.items())[:count]))


def _collection_with_documents(
    prefix: str,
    count: int,
    *,
    document_id_field: str,
) -> FakeCollectionRef:
    documents: dict[str, FakeDocumentRef] = {}
    for index in range(count):
        document_id = f"{prefix}-{index}"
        documents[document_id] = FakeDocumentRef(
            document_id,
            FakeSnapshot(
                document_id,
                exists=True,
                data={document_id_field: document_id},
            ),
        )
    return FakeCollectionRef(documents)


class FakeUserRef:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollectionRef] = {}
        self.collection_calls: list[str] = []

    def collection(self, name: str) -> FakeCollectionRef:
        self.collection_calls.append(name)
        self.collections.setdefault(name, FakeCollectionRef())
        return self.collections[name]


class FakeUsersCollection:
    def __init__(self, user_ref: FakeUserRef) -> None:
        self.user_ref = user_ref

    def document(self, document_id: str) -> FakeUserRef:
        return self.user_ref


class FakeClient:
    def __init__(self, user_ref: FakeUserRef) -> None:
        self.user_ref = user_ref

    def collection(self, name: str) -> FakeUsersCollection:
        assert name == "users"
        return FakeUsersCollection(self.user_ref)

    def transaction(self) -> "FakeTransaction":
        return FakeTransaction()


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[FakeDocumentRef, dict[str, Any], bool | None]] = []

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
        document_ref: FakeDocumentRef,
        data: dict[str, Any],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))
        document_ref.set(data, merge=merge)


def _meal(
    meal_id: str,
    *,
    name: str = "Owsianka z owocami",
    day_key: str = "2026-06-01",
    logged_at: str = "2026-06-01T07:30:00.000Z",
    deleted: bool = False,
) -> dict[str, object]:
    return {
        "id": meal_id,
        "type": "breakfast",
        "name": name,
        "dayKey": day_key,
        "loggedAt": logged_at,
        "deleted": deleted,
        "ingredients": [{"name": "private ingredient"}],
        "notes": "private note",
        "totals": {"kcal": 420, "protein": 18, "fat": 12, "carbs": 56},
    }


def test_known_pattern_candidates_require_three_distinct_days() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:30:00Z"),
            _meal("meal-3", day_key="2026-06-02", logged_at="2026-06-02T08:30:00Z"),
        ]
    )

    assert response.items == []
    assert response.queryEcho.returnedCandidates == 0


def test_known_pattern_candidates_return_bounded_repeated_meal_candidate() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:35:00Z"),
            _meal("meal-3", day_key="2026-06-03", logged_at="2026-06-03T07:40:00Z"),
        ],
        now=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert response.queryEcho.returnedCandidates == 1
    candidate = response.items[0]
    assert candidate.candidateType == "repeated_meal_snapshot"
    assert candidate.state == "candidate"
    assert candidate.confidenceBucket == "medium"
    assert candidate.sourceCountBucket == "3_4"
    assert candidate.distinctDayCountBucket == "3_4"
    assert candidate.suggestedAction == "open_review_draft"
    assert candidate.explanation.reasonCode == "repeated_meal_recent_distinct_days"
    assert len(candidate.sourceRefs) == 3

    payload = response.model_dump_json()
    assert "Owsianka" not in payload
    assert "private ingredient" not in payload
    assert "private note" not in payload
    assert "kcal" not in payload
    assert "meal-1" not in payload


def test_known_pattern_candidates_apply_shown_and_declined_controls() -> None:
    meals = [
        _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
        _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:35:00Z"),
        _meal("meal-3", day_key="2026-06-03", logged_at="2026-06-03T07:40:00Z"),
    ]
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    candidate = evaluate_known_pattern_candidates(meals, now=now).items[0]

    shown = evaluate_known_pattern_candidates(
        meals,
        now=now,
        controls=[
            {
                "subjectKeyHash": candidate.subjectKeyHash,
                "createdByRuleVersion": candidate.createdByRuleVersion,
                "state": "shown",
                "expiresAt": candidate.expiresAt,
            }
        ],
    )
    assert shown.items[0].state == "shown"

    declined = evaluate_known_pattern_candidates(
        meals,
        now=now,
        controls=[
            {
                "subjectKeyHash": candidate.subjectKeyHash,
                "createdByRuleVersion": candidate.createdByRuleVersion,
                "state": "declined",
                "expiresAt": candidate.expiresAt,
            }
        ],
    )
    assert declined.items == []

    expired_decline = evaluate_known_pattern_candidates(
        meals,
        now=now,
        controls=[
            {
                "subjectKeyHash": candidate.subjectKeyHash,
                "createdByRuleVersion": candidate.createdByRuleVersion,
                "state": "declined",
                "expiresAt": "2026-06-09T00:00:00.000Z",
            }
        ],
    )
    assert expired_decline.items[0].state == "candidate"


def test_known_pattern_candidates_ignore_deleted_and_missing_name_meals() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:35:00Z"),
            _meal(
                "meal-3",
                day_key="2026-06-03",
                logged_at="2026-06-03T07:40:00Z",
                deleted=True,
            ),
            _meal(
                "meal-4",
                name=" ",
                day_key="2026-06-04",
                logged_at="2026-06-04T07:40:00Z",
            ),
        ]
    )

    assert response.items == []


def test_known_pattern_candidates_do_not_return_expired_current_suggestions() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-05-01", logged_at="2026-05-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-05-02", logged_at="2026-05-02T07:35:00Z"),
            _meal("meal-3", day_key="2026-05-03", logged_at="2026-05-03T07:40:00Z"),
        ],
        now=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert response.items == []
    assert response.queryEcho.returnedCandidates == 0


def _three_day_meals() -> list[dict[str, object]]:
    return [
        _meal("meal-1", day_key="2026-06-15", logged_at="2026-06-15T07:30:00Z"),
        _meal("meal-2", day_key="2026-06-16", logged_at="2026-06-16T07:35:00Z"),
        _meal("meal-3", day_key="2026-06-17", logged_at="2026-06-17T07:40:00Z"),
    ]


def _candidate_payload() -> tuple[list[dict[str, object]], str, str, str]:
    meals = _three_day_meals()
    candidate = evaluate_known_pattern_candidates(
        meals,
        now=datetime(2026, 6, 18, tzinfo=timezone.utc),
    ).items[0]
    return (
        meals,
        candidate.candidateId,
        candidate.subjectKeyHash,
        candidate.createdByRuleVersion,
    )


def _patch_known_pattern_storage(
    monkeypatch: pytest.MonkeyPatch,
    meals: list[dict[str, object]],
) -> FakeUserRef:
    async def fake_list_history(
        user_id: str,
        *,
        limit_count: int,
    ) -> tuple[list[dict[str, object]], None]:
        assert user_id == "user-1"
        assert limit_count == known_pattern_service.KNOWN_PATTERN_MAX_HISTORY_ITEMS
        return meals, None

    user_ref = FakeUserRef()
    monkeypatch.setattr(known_pattern_service.meal_service, "list_history", fake_list_history)
    monkeypatch.setattr(known_pattern_service, "get_firestore", lambda: FakeClient(user_ref))
    return user_ref


def test_mark_control_declines_candidate_without_meal_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meals, candidate_id, subject_key_hash, rule_version = _candidate_payload()
    user_ref = _patch_known_pattern_storage(monkeypatch, meals)

    result = asyncio.run(
        known_pattern_service.mark_known_pattern_candidate_control_for_user(
            "user-1",
            candidate_id,
            KnownPatternCandidateControlRequest(
                clientMutationId="mutation-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
                action="declined",
            ),
        )
    )

    assert result["applied"] is True
    KnownPatternCandidateControl.model_validate(result["document"])
    assert result["document"]["state"] == "declined"
    assert result["document"]["candidateId"] == candidate_id
    assert "meals" not in user_ref.collection_calls
    assert "smartMemoryCandidates" not in user_ref.collection_calls

    persisted = json.dumps(result["document"], sort_keys=True)
    assert "Owsianka" not in persisted
    assert "private ingredient" not in persisted
    assert "private note" not in persisted
    assert "kcal" not in persisted

    replay = asyncio.run(
        known_pattern_service.mark_known_pattern_candidate_control_for_user(
            "user-1",
            candidate_id,
            KnownPatternCandidateControlRequest(
                clientMutationId="mutation-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
                action="declined",
            ),
        )
    )
    assert replay["applied"] is False
    assert replay["document"] == result["document"]


def test_mark_control_replays_when_candidate_is_no_longer_derivable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meals, candidate_id, subject_key_hash, rule_version = _candidate_payload()
    _patch_known_pattern_storage(monkeypatch, meals)

    result = asyncio.run(
        known_pattern_service.mark_known_pattern_candidate_control_for_user(
            "user-1",
            candidate_id,
            KnownPatternCandidateControlRequest(
                clientMutationId="mutation-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
                action="declined",
            ),
        )
    )

    async def fail_list_history(
        user_id: str,
        *,
        limit_count: int,
    ) -> tuple[list[dict[str, object]], None]:
        raise AssertionError("idempotent replay must not re-read meal history")

    monkeypatch.setattr(known_pattern_service.meal_service, "list_history", fail_list_history)

    replay = asyncio.run(
        known_pattern_service.mark_known_pattern_candidate_control_for_user(
            "user-1",
            candidate_id,
            KnownPatternCandidateControlRequest(
                clientMutationId="mutation-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
                action="declined",
            ),
        )
    )

    assert replay["applied"] is False
    assert replay["document"] == result["document"]


def test_open_review_draft_marks_shown_and_returns_explicit_draft_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meals, candidate_id, subject_key_hash, rule_version = _candidate_payload()
    user_ref = _patch_known_pattern_storage(monkeypatch, meals)

    result = asyncio.run(
        known_pattern_service.open_known_pattern_review_draft_for_user(
            "user-1",
            candidate_id,
            KnownPatternReviewDraftRequest(
                clientMutationId="mutation-review-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
            ),
        )
    )

    assert result["applied"] is True
    assert result["control"]["state"] == "shown"
    assert result["draft"].name == "Owsianka z owocami"
    assert result["draft"].type == "breakfast"
    assert result["draft"].ingredients[0].name == "private ingredient"
    assert result["draft"].notes is None
    assert result["draft"].tags == []
    assert "meals" not in user_ref.collection_calls


def test_open_review_draft_replays_when_candidate_is_no_longer_derivable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meals, candidate_id, subject_key_hash, rule_version = _candidate_payload()
    _patch_known_pattern_storage(monkeypatch, meals)

    result = asyncio.run(
        known_pattern_service.open_known_pattern_review_draft_for_user(
            "user-1",
            candidate_id,
            KnownPatternReviewDraftRequest(
                clientMutationId="mutation-review-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
            ),
        )
    )

    async def fail_list_history(
        user_id: str,
        *,
        limit_count: int,
    ) -> tuple[list[dict[str, object]], None]:
        raise AssertionError("idempotent replay must not re-read meal history")

    monkeypatch.setattr(known_pattern_service.meal_service, "list_history", fail_list_history)

    replay = asyncio.run(
        known_pattern_service.open_known_pattern_review_draft_for_user(
            "user-1",
            candidate_id,
            KnownPatternReviewDraftRequest(
                clientMutationId="mutation-review-1",
                subjectKeyHash=subject_key_hash,
                createdByRuleVersion=rule_version,
            ),
        )
    )

    assert replay["applied"] is False
    assert replay["control"] == result["control"]
    assert replay["draft"] == result["draft"]


def test_known_pattern_control_rejects_unknown_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meals, _candidate_id, subject_key_hash, rule_version = _candidate_payload()
    _patch_known_pattern_storage(monkeypatch, meals)

    with pytest.raises(KnownPatternNotFoundError):
        asyncio.run(
            known_pattern_service.mark_known_pattern_candidate_control_for_user(
                "user-1",
                "missing-candidate",
                KnownPatternCandidateControlRequest(
                    clientMutationId="mutation-1",
                    subjectKeyHash=subject_key_hash,
                    createdByRuleVersion=rule_version,
                    action="declined",
                ),
            )
        )


def test_known_pattern_service_stays_deterministic_and_read_only() -> None:
    service_source = inspect.getsource(known_pattern_service)

    assert "openai" not in service_source.casefold()
    assert "upsert_meal" not in service_source
    assert "mark_deleted" not in service_source
    assert "MEALS_SUBCOLLECTION" not in service_source
    assert "SMART_MEMORY_CANDIDATES_SUBCOLLECTION" not in service_source
    assert ".update(" not in service_source
    assert ".delete(" not in service_source


@pytest.mark.parametrize("document_count", [0, 1, 249, 250, 251, 501])
def test_read_export_streams_all_controls_and_dedupe_without_limit(
    document_count: int,
) -> None:
    controls_collection = _collection_with_documents(
        "control",
        document_count,
        document_id_field="controlId",
    )
    mutation_dedupe_collection = _collection_with_documents(
        "mutation",
        document_count,
        document_id_field="id",
    )
    user_ref = FakeUserRef()
    user_ref.collections["knownPatternControls"] = controls_collection
    user_ref.collections["knownPatternMutationDedupe"] = mutation_dedupe_collection

    export = known_pattern_service.read_export(cast(Any, user_ref))

    assert len(export["controls"]) == document_count
    assert len(export["mutationDedupe"]) == document_count
    assert controls_collection.limit_calls == []
    assert mutation_dedupe_collection.limit_calls == []
