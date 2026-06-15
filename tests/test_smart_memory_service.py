import asyncio
from typing import Any, cast

import pytest
from google.cloud import firestore

from app.schemas.smart_memory import (
    SmartMemoryItem,
    SmartMemoryCandidateUpsertRequest,
    SmartMemoryItemPatchRequest,
    SmartMemorySettingsUpdateRequest,
    SmartMemorySourceDeletedRequest,
)
from app.services import smart_memory_service
from app.services.smart_memory_service import SmartMemoryMutationDedupeConflictError


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
    def __init__(self, document_id: str, snapshot: FakeSnapshot) -> None:
        self.id = document_id
        self.snapshot = snapshot
        self.set_calls: list[tuple[dict[str, Any], bool | None]] = []

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        return self.snapshot

    def set(self, data: dict[str, Any], merge: bool | None = None) -> None:
        self.set_calls.append((data, merge))
        self.snapshot = FakeSnapshot(self.id, exists=True, data=data)


class FakeCollectionRef:
    def __init__(self, documents: dict[str, FakeDocumentRef]) -> None:
        self.documents = documents
        self.limit_calls: list[int] = []
        self.where_calls: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.query_refs: list["FakeCollectionRef"] = []

    def document(self, document_id: str) -> FakeDocumentRef:
        if document_id not in self.documents:
            self.documents[document_id] = FakeDocumentRef(
                document_id,
                FakeSnapshot(document_id, exists=False),
            )
        return self.documents[document_id]

    def stream(self) -> list[FakeSnapshot]:
        return [document.snapshot for document in self.documents.values()]

    def limit(self, count: int) -> "FakeCollectionRef":
        self.limit_calls.append(count)
        return FakeCollectionRef(dict(list(self.documents.items())[:count]))

    def where(self, *, filter: Any) -> "FakeCollectionRef":
        field_path = filter.field_path
        op_string = filter.op_string
        value = filter.value
        assert field_path == "sourceRefs"
        assert op_string == "array_contains_any"
        assert isinstance(value, list)
        refs = cast(list[dict[str, Any]], value)
        self.where_calls.append((field_path, op_string, refs))
        matching_documents: dict[str, FakeDocumentRef] = {}
        for document_id, document_ref in self.documents.items():
            document = document_ref.snapshot.to_dict()
            source_refs = document.get("sourceRefs")
            if not isinstance(source_refs, list):
                continue
            checked_source_refs = cast(list[object], source_refs)
            if any(source_ref in refs for source_ref in checked_source_refs):
                matching_documents[document_id] = document_ref
        query_ref = FakeCollectionRef(matching_documents)
        self.query_refs.append(query_ref)
        return query_ref


class FakeUserRef:
    def __init__(self, collections: dict[str, FakeCollectionRef]) -> None:
        self.collections = collections

    def collection(self, name: str) -> FakeCollectionRef:
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
        document_ref.set_calls.append((data, merge))


def _client_for_item(
    item_payload: dict[str, Any],
    *,
    mutation_payload: dict[str, Any] | None = None,
) -> tuple[FakeClient, FakeTransaction, FakeDocumentRef, FakeDocumentRef, FakeDocumentRef]:
    tombstone_id = smart_memory_service._tombstone_id(
        "typical_portion",
        item_payload.get("subject") or {},
        item_payload["memoryItemId"],
    )
    item_ref = FakeDocumentRef(
        item_payload["memoryItemId"],
        FakeSnapshot(item_payload["memoryItemId"], exists=True, data=item_payload),
    )
    mutation_ref = FakeDocumentRef(
        "mutation-1",
        FakeSnapshot("mutation-1", exists=mutation_payload is not None, data=mutation_payload),
    )
    tombstone_ref = FakeDocumentRef(
        tombstone_id,
        FakeSnapshot(tombstone_id, exists=False),
    )
    user_ref = FakeUserRef(
        {
            "smartMemory": FakeCollectionRef({item_payload["memoryItemId"]: item_ref}),
            "smartMemoryMutationDedupe": FakeCollectionRef({"mutation-1": mutation_ref}),
            "smartMemoryTombstones": FakeCollectionRef({tombstone_ref.id: tombstone_ref}),
        }
    )
    return FakeClient(user_ref), FakeTransaction(), item_ref, mutation_ref, tombstone_ref


def _candidate_request() -> SmartMemoryCandidateUpsertRequest:
    return SmartMemoryCandidateUpsertRequest.model_validate(
        {
            "clientMutationId": "mutation-1",
            "candidateId": "candidate-oats",
            "memoryType": "typical_portion",
            "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
            "evidenceSummary": {"supportingEventCount": 1, "distinctDayCount": 1},
            "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "source-hash-1"}],
            "confidenceReasonCodes": ["single_observation"],
            "suppressionChecks": {"deletedSuppressed": False},
        }
    )


def _client_for_candidate(
    payload: SmartMemoryCandidateUpsertRequest,
    *,
    existing_candidate: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    tombstone_exists: bool = False,
) -> tuple[FakeClient, FakeTransaction, FakeDocumentRef, FakeDocumentRef]:
    candidate_ref = FakeDocumentRef(
        payload.candidateId,
        FakeSnapshot(
            payload.candidateId,
            exists=existing_candidate is not None,
            data=existing_candidate,
        ),
    )
    mutation_ref = FakeDocumentRef("mutation-1", FakeSnapshot("mutation-1", exists=False))
    settings_ref = FakeDocumentRef(
        "default",
        FakeSnapshot("default", exists=settings is not None, data=settings),
    )
    tombstone_id = smart_memory_service._tombstone_id(
        payload.memoryType,
        payload.subject,
        payload.candidateId,
    )
    tombstone_ref = FakeDocumentRef(
        tombstone_id,
        FakeSnapshot(tombstone_id, exists=tombstone_exists, data={"id": tombstone_id}),
    )
    user_ref = FakeUserRef(
        {
            "smartMemoryCandidates": FakeCollectionRef({payload.candidateId: candidate_ref}),
            "smartMemoryMutationDedupe": FakeCollectionRef({"mutation-1": mutation_ref}),
            "smartMemorySettings": FakeCollectionRef({"default": settings_ref}),
            "smartMemoryTombstones": FakeCollectionRef({tombstone_id: tombstone_ref}),
        }
    )
    return FakeClient(user_ref), FakeTransaction(), candidate_ref, mutation_ref


def _promotable_candidate() -> dict[str, Any]:
    return {
        "candidateId": "candidate-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "candidate",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
        "evidenceSummary": {
            "thresholdVersion": "typical_portion_v1",
            "requiredObservationCount": 3,
            "requiredDistinctDayCount": 3,
            "eligibleObservationCount": 3,
            "distinctDayCount": 3,
            "proposedValue": {"amount": 60, "unit": "g"},
        },
        "sourceRefs": [
            {"kind": "meal_portion_observation", "sourceHash": "source-hash-1"},
            {"kind": "meal_portion_observation", "sourceHash": "source-hash-2"},
            {"kind": "meal_portion_observation", "sourceHash": "source-hash-3"},
        ],
        "confidenceReasonCodes": ["distinct_days_met"],
        "suppressionChecks": {
            "deletedSuppressed": False,
            "sourceDeleted": False,
            "subjectSuppressionKey": "typical_portion:hash",
        },
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "firstSeenAt": "2026-06-01T10:00:00.000Z",
        "lastSeenAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 1,
    }


def _client_for_promotion(
    candidate: dict[str, Any],
    *,
    mutation_payload: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    tombstone_exists: bool = False,
    item_exists: bool = False,
) -> tuple[FakeClient, FakeTransaction, FakeDocumentRef, FakeDocumentRef, FakeDocumentRef]:
    candidate_ref = FakeDocumentRef(
        candidate["candidateId"],
        FakeSnapshot(candidate["candidateId"], exists=True, data=candidate),
    )
    item_payload = _active_item()
    item_payload["memoryItemId"] = "portion-oats-promoted"
    item_ref = FakeDocumentRef(
        "portion-oats-promoted",
        FakeSnapshot("portion-oats-promoted", exists=item_exists, data=item_payload),
    )
    mutation_ref = FakeDocumentRef(
        "mutation-promote-1",
        FakeSnapshot(
            "mutation-promote-1",
            exists=mutation_payload is not None,
            data=mutation_payload,
        ),
    )
    settings_ref = FakeDocumentRef(
        "default",
        FakeSnapshot("default", exists=settings is not None, data=settings),
    )
    tombstone_id = smart_memory_service._tombstone_id(
        candidate["memoryType"],
        candidate.get("subject") or {},
        candidate["candidateId"],
    )
    tombstone_ref = FakeDocumentRef(
        tombstone_id,
        FakeSnapshot(tombstone_id, exists=tombstone_exists, data={"id": tombstone_id}),
    )
    user_ref = FakeUserRef(
        {
            "smartMemory": FakeCollectionRef({item_ref.id: item_ref}),
            "smartMemoryCandidates": FakeCollectionRef({candidate_ref.id: candidate_ref}),
            "smartMemoryMutationDedupe": FakeCollectionRef(
                {"mutation-promote-1": mutation_ref}
            ),
            "smartMemorySettings": FakeCollectionRef({"default": settings_ref}),
            "smartMemoryTombstones": FakeCollectionRef({tombstone_ref.id: tombstone_ref}),
        }
    )
    return FakeClient(user_ref), FakeTransaction(), item_ref, candidate_ref, mutation_ref


def _active_item() -> dict[str, Any]:
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
    }


def _ingredient_selection_item() -> dict[str, Any]:
    item = _active_item()
    item["memoryItemId"] = "ingredient-product-oats"
    item["memoryType"] = "ingredient_product_selection"
    item["userValue"] = {"displayLabel": "Oats"}
    return item


def _review_correction_item() -> dict[str, Any]:
    item = _active_item()
    item["memoryItemId"] = "correction-oats"
    item["memoryType"] = "review_correction"
    item["userValue"] = {"amount": 60, "unit": "g", "reasonCode": "user_corrected"}
    return item


def test_delete_item_transaction_suppresses_evidence_and_writes_tombstone() -> None:
    client, transaction, item_ref, mutation_ref, tombstone_ref = _client_for_item(_active_item())

    result = smart_memory_service._mutate_item_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        memory_item_id="portion-oats",
        kind="item_delete",
        client_mutation_id="mutation-1",
        payload_hash="hash-1",
        patch_payload={},
    )

    assert result["applied"] is True
    assert result["document"]["state"] == "deleted_suppressed"
    assert result["document"]["evidenceSummary"] == {}
    assert result["document"]["sourceRefs"] == []
    assert result["document"]["userValue"] == {}
    assert result["document"]["subject"] == {}
    assert result["document"]["threshold"] == {}
    assert result["document"]["confidence"] == {}
    assert result["document"]["confidenceReasonCodes"] == []
    assert item_ref.set_calls[0][0]["control"]["suggestionsSuppressed"] is True
    assert tombstone_ref.set_calls[0][0]["reasonCode"] == "user_deleted"
    assert "oats" not in tombstone_ref.set_calls[0][0]["subjectKey"]
    assert mutation_ref.set_calls[0][0]["kind"] == "item_delete"


def test_source_deleted_transaction_suppresses_suggestions() -> None:
    client, transaction, item_ref, _mutation_ref, tombstone_ref = _client_for_item(_active_item())

    result = smart_memory_service._mutate_item_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        memory_item_id="portion-oats",
        kind="item_source_deleted",
        client_mutation_id="mutation-1",
        payload_hash="hash-1",
        patch_payload={"sourceRef": {"kind": "meal_portion_observation", "sourceHash": "source-hash-1"}},
    )

    assert result["document"]["state"] == "source_deleted"
    assert result["document"]["control"]["suggestionsSuppressed"] is True
    assert item_ref.set_calls[0][0]["sourceDeletedAt"] is not None
    assert tombstone_ref.set_calls[0][0]["reasonCode"] == "source_deleted"


def test_delete_source_deleted_item_forgets_memory_and_writes_user_tombstone() -> None:
    item = _active_item()
    item["state"] = "source_deleted"
    item["stateReason"] = "source_deleted"
    item["sourceDeletedAt"] = "2026-06-04T10:00:00.000Z"
    client, transaction, item_ref, mutation_ref, tombstone_ref = _client_for_item(item)

    result = smart_memory_service._mutate_item_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        memory_item_id="portion-oats",
        kind="item_delete",
        client_mutation_id="mutation-1",
        payload_hash="hash-1",
        patch_payload={},
    )

    assert result["applied"] is True
    assert result["document"]["state"] == "deleted_suppressed"
    assert result["document"]["stateReason"] == "user_deleted"
    assert result["document"]["sourceDeletedAt"] is None
    assert result["document"]["sourceRefs"] == []
    assert result["document"]["subject"] == {}
    assert result["document"]["userValue"] == {}
    assert item_ref.set_calls[0][0]["control"]["suggestionsSuppressed"] is True
    assert tombstone_ref.set_calls[0][0]["reasonCode"] == "user_deleted"
    assert mutation_ref.set_calls[0][0]["kind"] == "item_delete"


def test_existing_mutation_with_different_payload_raises_conflict() -> None:
    item = _active_item()
    client, transaction, _item_ref, _mutation_ref, _tombstone_ref = _client_for_item(
        item,
        mutation_payload={
            "clientMutationId": "mutation-1",
            "kind": "item_delete",
            "targetId": "portion-oats",
            "payloadHash": "different-hash",
            "resultDocument": item,
        },
    )

    with pytest.raises(SmartMemoryMutationDedupeConflictError):
        smart_memory_service._mutate_item_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            memory_item_id="portion-oats",
            kind="item_delete",
            client_mutation_id="mutation-1",
            payload_hash="hash-1",
            patch_payload={},
        )


def test_promote_candidate_transaction_creates_active_item_and_consumes_candidate() -> None:
    candidate = _promotable_candidate()
    client, transaction, item_ref, candidate_ref, mutation_ref = _client_for_promotion(
        candidate
    )

    result = smart_memory_service._promote_candidate_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        candidate_id=candidate["candidateId"],
        memory_item_id="portion-oats-promoted",
        client_mutation_id="mutation-promote-1",
        payload_hash="hash-promote-1",
    )

    assert result["applied"] is True
    assert result["document"]["memoryItemId"] == "portion-oats-promoted"
    assert result["document"]["state"] == "active"
    assert result["document"]["stateReason"] == "threshold_met"
    assert result["document"]["subject"] == candidate["subject"]
    assert result["document"]["userValue"] == {"amount": 60, "unit": "g"}
    assert result["document"]["sourceRefs"] == candidate["sourceRefs"]
    assert result["document"]["evidenceSummary"] == candidate["evidenceSummary"]
    assert result["document"]["threshold"] == {
        "requiredObservationCount": 3,
        "requiredDistinctDayCount": 3,
        "eligibleObservationCount": 3,
        "distinctDayCount": 3,
        "thresholdVersion": "typical_portion_v1",
    }
    assert result["document"]["confidence"]["strategy"] == "deterministic_threshold"
    assert result["document"]["control"]["sourceCandidateId"] == candidate["candidateId"]
    assert item_ref.set_calls[0][0] == result["document"]
    assert candidate_ref.set_calls[0][0]["state"] == "activated"
    assert (
        candidate_ref.set_calls[0][0]["suppressionChecks"]["promotedToMemoryItemId"]
        == "portion-oats-promoted"
    )
    assert (
        candidate_ref.set_calls[0][0]["suppressionChecks"].get("deletedSuppressed")
        is not True
    )
    assert mutation_ref.set_calls[0][0]["kind"] == "candidate_promote"
    SmartMemoryItem.model_validate(result["document"])


def test_promote_candidate_transaction_is_idempotent_by_client_mutation_id() -> None:
    candidate = _promotable_candidate()
    result_document: dict[str, Any] = {
        **_active_item(),
        "memoryItemId": "portion-oats-promoted",
        "subject": candidate["subject"],
    }
    payload_hash = smart_memory_service._stable_payload_hash(
        {
            "kind": "candidate_promote",
            "targetId": "portion-oats-promoted",
            "candidateId": candidate["candidateId"],
        }
    )
    client, transaction, item_ref, candidate_ref, _mutation_ref = _client_for_promotion(
        candidate,
        mutation_payload={
            "clientMutationId": "mutation-promote-1",
            "kind": "candidate_promote",
            "targetId": "portion-oats-promoted",
            "payloadHash": payload_hash,
            "resultDocument": result_document,
        },
    )

    result = smart_memory_service._promote_candidate_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        candidate_id=candidate["candidateId"],
        memory_item_id="portion-oats-promoted",
        client_mutation_id="mutation-promote-1",
        payload_hash=payload_hash,
    )

    assert result == {"document": result_document, "applied": False}
    assert item_ref.set_calls == []
    assert candidate_ref.set_calls == []


def test_promote_candidate_rejects_disabled_settings() -> None:
    candidate = _promotable_candidate()
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate,
        settings={"enabled": False},
    )

    with pytest.raises(ValueError, match="disabled"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


def test_promote_candidate_rejects_tombstoned_subject() -> None:
    candidate = _promotable_candidate()
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate,
        tombstone_exists=True,
    )

    with pytest.raises(ValueError, match="user delete"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


@pytest.mark.parametrize("state", ["activated", "source_deleted", "deleted_suppressed"])
def test_promote_candidate_rejects_suppressed_candidate(state: str) -> None:
    candidate = _promotable_candidate()
    candidate["state"] = state
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate
    )

    with pytest.raises(ValueError, match="suppressed"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


def test_promote_candidate_rejects_deleted_source_refs() -> None:
    candidate = _promotable_candidate()
    candidate["sourceRefs"] = [
        {
            "kind": "meal_portion_observation",
            "sourceHash": "source-hash-1",
            "state": "deleted",
        }
    ]
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate
    )

    with pytest.raises(ValueError, match="sourceRef is unavailable"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


def test_promote_candidate_rejects_missing_or_insufficient_evidence() -> None:
    candidate = _promotable_candidate()
    candidate["evidenceSummary"] = {
        "requiredObservationCount": 3,
        "requiredDistinctDayCount": 3,
        "eligibleObservationCount": 2,
        "distinctDayCount": 2,
        "proposedValue": {"amount": 60, "unit": "g"},
    }
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate
    )

    with pytest.raises(ValueError, match="threshold"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


def test_promote_candidate_rejects_raw_payload_leakage() -> None:
    candidate = _promotable_candidate()
    candidate["evidenceSummary"] = {
        **candidate["evidenceSummary"],
        "providerPayload": {"mealName": "Rice bowl"},
    }
    client, transaction, _item_ref, _candidate_ref, _mutation_ref = _client_for_promotion(
        candidate
    )

    with pytest.raises(ValueError, match="providerPayload"):
        smart_memory_service._promote_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=candidate["candidateId"],
            memory_item_id="portion-oats-promoted",
            client_mutation_id="mutation-promote-1",
            payload_hash="hash-promote-1",
        )


def test_candidate_upsert_rejects_disabled_settings() -> None:
    payload = _candidate_request()
    client, transaction, _candidate_ref, _mutation_ref = _client_for_candidate(
        payload,
        settings={"enabled": False},
    )

    with pytest.raises(ValueError, match="disabled"):
        smart_memory_service._upsert_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=payload.candidateId,
            payload=payload,
            client_mutation_id=payload.clientMutationId,
            payload_hash="hash-1",
        )


def test_candidate_upsert_rejects_existing_suppressed_candidate() -> None:
    payload = _candidate_request()
    client, transaction, _candidate_ref, _mutation_ref = _client_for_candidate(
        payload,
        existing_candidate={
            "candidateId": payload.candidateId,
            "state": "source_deleted",
        },
    )

    with pytest.raises(ValueError, match="suppressed"):
        smart_memory_service._upsert_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=payload.candidateId,
            payload=payload,
            client_mutation_id=payload.clientMutationId,
            payload_hash="hash-1",
        )


def test_candidate_upsert_returns_existing_activated_candidate_without_rewrite() -> None:
    payload = _candidate_request()
    existing_candidate: dict[str, Any] = {
        "candidateId": payload.candidateId,
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": payload.memoryType,
        "state": "activated",
        "subject": payload.subject,
        "evidenceSummary": payload.evidenceSummary,
        "sourceRefs": payload.sourceRefs,
        "confidenceReasonCodes": payload.confidenceReasonCodes,
        "suppressionChecks": {"promotedToMemoryItemId": "memory-candidate-oats"},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 2,
    }
    client, transaction, candidate_ref, mutation_ref = _client_for_candidate(
        payload,
        existing_candidate=existing_candidate,
    )

    result = smart_memory_service._upsert_candidate_transaction(
        cast(firestore.Transaction, transaction),
        client=client,  # type: ignore[arg-type]
        user_id="user-1",
        candidate_id=payload.candidateId,
        payload=payload,
        client_mutation_id=payload.clientMutationId,
        payload_hash="hash-1",
    )

    assert result == {"document": existing_candidate, "applied": False}
    assert candidate_ref.set_calls == []
    assert mutation_ref.set_calls == []


def test_candidate_upsert_rejects_tombstoned_subject() -> None:
    payload = _candidate_request()
    client, transaction, _candidate_ref, _mutation_ref = _client_for_candidate(
        payload,
        tombstone_exists=True,
    )

    with pytest.raises(ValueError, match="user delete"):
        smart_memory_service._upsert_candidate_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            candidate_id=payload.candidateId,
            payload=payload,
            client_mutation_id=payload.clientMutationId,
            payload_hash="hash-1",
        )


def test_list_tombstone_subject_keys_filters_by_memory_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tombstones_collection = FakeCollectionRef(
        {
            "tombstone-1": FakeDocumentRef(
                "tombstone-1",
                FakeSnapshot(
                    "tombstone-1",
                    exists=True,
                    data={
                        "memoryType": "typical_portion",
                        "subjectKey": "typical_portion:subject-a",
                    },
                ),
            ),
            "tombstone-2": FakeDocumentRef(
                "tombstone-2",
                FakeSnapshot(
                    "tombstone-2",
                    exists=True,
                    data={
                        "memoryType": "review_correction",
                        "subjectKey": "review_correction:subject-b",
                    },
                ),
            ),
        }
    )
    user_ref = FakeUserRef(
        {
            "smartMemoryTombstones": tombstones_collection,
        }
    )
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    result = asyncio.run(
        smart_memory_service.list_tombstone_subject_keys(
            "user-1",
            memory_type="typical_portion",
        )
    )

    assert result == ["typical_portion:subject-a"]
    assert tombstones_collection.limit_calls == [smart_memory_service.MAX_CAPTURE_CONTROL_DOCS]


def test_list_suppressed_subject_keys_includes_source_deleted_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_deleted_item = _active_item()
    source_deleted_item["state"] = "source_deleted"
    muted_item = _active_item()
    muted_item["memoryItemId"] = "muted-oats"
    muted_item["state"] = "muted"
    source_deleted_candidate: dict[str, Any] = {
        "candidateId": "candidate-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "source_deleted",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash"},
        "evidenceSummary": {"supportingEventCount": 3, "distinctDayCount": 3},
        "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}],
        "confidenceReasonCodes": ["distinct_days_met"],
        "suppressionChecks": {"deletedSuppressed": False, "sourceDeleted": True},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 1,
    }
    tombstones_collection = FakeCollectionRef(
        {
            "tombstone-1": FakeDocumentRef(
                "tombstone-1",
                FakeSnapshot(
                    "tombstone-1",
                    exists=True,
                    data={
                        "memoryType": "typical_portion",
                        "subjectKey": "typical_portion:tombstoned",
                    },
                ),
            )
        }
    )
    items_collection = FakeCollectionRef(
        {
            source_deleted_item["memoryItemId"]: FakeDocumentRef(
                source_deleted_item["memoryItemId"],
                FakeSnapshot(
                    source_deleted_item["memoryItemId"],
                    exists=True,
                    data=source_deleted_item,
                ),
            ),
            muted_item["memoryItemId"]: FakeDocumentRef(
                muted_item["memoryItemId"],
                FakeSnapshot(muted_item["memoryItemId"], exists=True, data=muted_item),
            ),
        }
    )
    candidates_collection = FakeCollectionRef(
        {
            source_deleted_candidate["candidateId"]: FakeDocumentRef(
                source_deleted_candidate["candidateId"],
                FakeSnapshot(
                    source_deleted_candidate["candidateId"],
                    exists=True,
                    data=source_deleted_candidate,
                ),
            )
        }
    )
    user_ref = FakeUserRef(
        {
            "smartMemoryTombstones": tombstones_collection,
            "smartMemory": items_collection,
            "smartMemoryCandidates": candidates_collection,
        }
    )
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    result = asyncio.run(
        smart_memory_service.list_suppressed_subject_keys(
            "user-1",
            memory_type="typical_portion",
        )
    )

    assert "typical_portion:tombstoned" in result
    assert smart_memory_service._subject_key(
        "typical_portion",
        source_deleted_item["subject"],
        source_deleted_item["memoryItemId"],
    ) in result
    assert smart_memory_service._subject_key(
        "typical_portion",
        source_deleted_candidate["subject"],
        source_deleted_candidate["candidateId"],
    ) in result
    assert tombstones_collection.limit_calls == [smart_memory_service.MAX_CAPTURE_CONTROL_DOCS]
    assert items_collection.limit_calls == [smart_memory_service.MAX_CAPTURE_CONTROL_DOCS]
    assert candidates_collection.limit_calls == [smart_memory_service.MAX_CAPTURE_CONTROL_DOCS]


def test_upsert_candidate_rejects_source_deleted_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _candidate_request()
    tombstone_id = smart_memory_service._tombstone_id(
        payload.memoryType,
        payload.subject,
        payload.candidateId,
    )
    user_ref = FakeUserRef(
        {
            "smartMemoryCandidates": FakeCollectionRef({}),
            "smartMemoryMutationDedupe": FakeCollectionRef({}),
            "smartMemorySettings": FakeCollectionRef({}),
            "smartMemoryTombstones": FakeCollectionRef(
                {
                    tombstone_id: FakeDocumentRef(
                        tombstone_id,
                        FakeSnapshot(
                            tombstone_id,
                            exists=True,
                            data={"tombstoneId": tombstone_id},
                        ),
                    )
                }
            ),
        }
    )
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    with pytest.raises(ValueError, match="user delete"):
        asyncio.run(smart_memory_service.upsert_candidate("user-1", payload))


def test_mark_sources_deleted_by_source_hashes_updates_items_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _active_item()
    item["sourceRefs"] = [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}]
    candidate: dict[str, Any] = {
        "candidateId": "candidate-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "candidate",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash"},
        "evidenceSummary": {"supportingEventCount": 3, "distinctDayCount": 3},
        "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}],
        "confidenceReasonCodes": ["distinct_days_met"],
        "suppressionChecks": {"deletedSuppressed": False, "sourceDeleted": False},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "firstSeenAt": "2026-06-01T10:00:00.000Z",
        "lastSeenAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 1,
    }
    item_ref = FakeDocumentRef(
        item["memoryItemId"],
        FakeSnapshot(item["memoryItemId"], exists=True, data=item),
    )
    candidate_ref = FakeDocumentRef(
        candidate["candidateId"],
        FakeSnapshot(candidate["candidateId"], exists=True, data=candidate),
    )
    items_collection = FakeCollectionRef({item_ref.id: item_ref})
    candidates_collection = FakeCollectionRef({candidate_ref.id: candidate_ref})
    user_ref = FakeUserRef(
        {
            "smartMemory": items_collection,
            "smartMemoryCandidates": candidates_collection,
            "smartMemoryTombstones": FakeCollectionRef({}),
        }
    )
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    subject_key = smart_memory_service._subject_key(
        "typical_portion",
        item["subject"],
        item["memoryItemId"],
    )
    updated_count = asyncio.run(
        smart_memory_service.mark_sources_deleted_by_source_hashes(
            "user-1",
            ["hash-1"],
            subject_keys=[subject_key],
        )
    )

    assert updated_count == 3
    assert item_ref.set_calls[0][0]["state"] == "source_deleted"
    assert item_ref.set_calls[0][0]["control"]["suggestionsSuppressed"] is True
    assert candidate_ref.set_calls[0][0]["state"] == "source_deleted"
    assert candidate_ref.set_calls[0][0]["suppressionChecks"]["sourceDeleted"] is True
    tombstone_id = smart_memory_service._tombstone_id_from_subject_key(subject_key)
    assert (
        user_ref.collections["smartMemoryTombstones"]
        .documents[tombstone_id]
        .set_calls[0][0]["reasonCode"]
        == "source_deleted"
    )
    assert items_collection.where_calls == [
        (
            "sourceRefs",
            "array_contains_any",
            [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}],
        )
    ]
    assert candidates_collection.where_calls == [
        (
            "sourceRefs",
            "array_contains_any",
            [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}],
        )
    ]
    assert items_collection.query_refs[0].limit_calls == [
        smart_memory_service.MAX_SOURCE_HASH_QUERY_DOCS
    ]
    assert candidates_collection.query_refs[0].limit_calls == [
        smart_memory_service.MAX_SOURCE_HASH_QUERY_DOCS
    ]


def test_mark_sources_deleted_by_source_hashes_queries_matching_docs_beyond_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filler_documents: dict[str, FakeDocumentRef] = {
        f"filler-{index}": FakeDocumentRef(
            f"filler-{index}",
            FakeSnapshot(
                f"filler-{index}",
                exists=True,
                data={
                    **_active_item(),
                    "memoryItemId": f"filler-{index}",
                    "sourceRefs": [
                        {
                            "kind": "meal_portion_observation",
                            "sourceHash": f"other-{index}",
                        }
                    ],
                },
            ),
        )
        for index in range(smart_memory_service.MAX_CAPTURE_CONTROL_DOCS + 1)
    }
    matching_item = _active_item()
    matching_item["memoryItemId"] = "late-match"
    matching_item["sourceRefs"] = [
        {"kind": "meal_portion_observation", "sourceHash": "hash-late"}
    ]
    matching_ref = FakeDocumentRef(
        "late-match",
        FakeSnapshot("late-match", exists=True, data=matching_item),
    )
    filler_documents[matching_ref.id] = matching_ref
    items_collection = FakeCollectionRef(filler_documents)
    candidates_collection = FakeCollectionRef({})
    user_ref = FakeUserRef(
        {
            "smartMemory": items_collection,
            "smartMemoryCandidates": candidates_collection,
            "smartMemoryTombstones": FakeCollectionRef({}),
        }
    )
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    updated_count = asyncio.run(
        smart_memory_service.mark_sources_deleted_by_source_hashes(
            "user-1",
            ["hash-late"],
        )
    )

    assert updated_count == 1
    assert matching_ref.set_calls[0][0]["state"] == "source_deleted"
    assert user_ref.collections["smartMemoryTombstones"].documents
    assert items_collection.limit_calls == []
    assert items_collection.where_calls == [
        (
            "sourceRefs",
            "array_contains_any",
            [{"kind": "meal_portion_observation", "sourceHash": "hash-late"}],
        )
    ]


def test_candidate_request_rejects_deleted_source_ref() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemoryCandidateUpsertRequest.model_validate(
            {
                "clientMutationId": "candidate-1",
                "candidateId": "candidate-oats",
                "memoryType": "typical_portion",
                "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
                "sourceRefs": [
                    {
                        "kind": "meal_portion_observation",
                        "sourceHash": "source-hash-1",
                        "deleted": True,
                    }
                ],
            }
        )


def test_candidate_request_rejects_raw_subject_and_source_refs() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemoryCandidateUpsertRequest.model_validate(
            {
                "clientMutationId": "candidate-1",
                "candidateId": "candidate-oats",
                "memoryType": "typical_portion",
                "subject": {"kind": "ingredient", "key": "oats"},
                "sourceRefs": [{"kind": "meal", "mealId": "meal-1"}],
            }
        )


def test_item_rejects_raw_subject_and_source_refs() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemoryItem.model_validate(
            {
                **_active_item(),
                "subject": {"kind": "ingredient", "key": "oats"},
            }
        )
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemoryItem.model_validate(
            {
                **_active_item(),
                "sourceRefs": [{"kind": "meal", "mealId": "meal-1"}],
            }
        )


def test_source_deleted_request_requires_hash_only_source_ref() -> None:
    with pytest.raises(ValueError):
        SmartMemorySourceDeletedRequest.model_validate(
            {
                "clientMutationId": "source-delete-1",
            }
        )
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemorySourceDeletedRequest.model_validate(
            {
                "clientMutationId": "source-delete-1",
                "sourceRef": {"kind": "meal", "mealId": "meal-1"},
            }
        )
    request = SmartMemorySourceDeletedRequest.model_validate(
        {
            "clientMutationId": "source-delete-1",
            "sourceRef": {
                "kind": "meal_portion_observation",
                "sourceHash": "source-hash-1",
            },
        }
    )

    assert request.sourceRef == {
        "kind": "meal_portion_observation",
        "sourceHash": "source-hash-1",
    }


def test_source_deleted_request_rejects_extra_hashed_source_ref_fields() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemorySourceDeletedRequest.model_validate(
            {
                "clientMutationId": "source-delete-1",
                "sourceRef": {
                    "kind": "meal_portion_observation",
                    "sourceHash": "source-hash-1",
                    "extra": "not-allowed",
                },
            }
        )


def test_source_deleted_request_rejects_raw_source_ref() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemorySourceDeletedRequest.model_validate(
            {
                "clientMutationId": "source-delete-1",
                "sourceRef": {"kind": "meal", "mealId": "meal-1"},
            }
        )


def test_list_items_uses_bounded_collection_query(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _active_item()
    items_collection = FakeCollectionRef(
        {
            item["memoryItemId"]: FakeDocumentRef(
                item["memoryItemId"],
                FakeSnapshot(item["memoryItemId"], exists=True, data=item),
            )
        }
    )
    user_ref = FakeUserRef({"smartMemory": items_collection})
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    result = asyncio.run(smart_memory_service.list_items("user-1", limit_count=7))

    assert result[0]["memoryItemId"] == item["memoryItemId"]
    assert items_collection.limit_calls == [7]


def test_list_candidates_uses_bounded_collection_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate: dict[str, Any] = {
        "candidateId": "candidate-oats",
        "ownerUserId": "user-1",
        "schemaVersion": 1,
        "memoryType": "typical_portion",
        "state": "candidate",
        "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash"},
        "evidenceSummary": {"supportingEventCount": 3, "distinctDayCount": 3},
        "sourceRefs": [{"kind": "meal_portion_observation", "sourceHash": "hash-1"}],
        "confidenceReasonCodes": ["distinct_days_met"],
        "suppressionChecks": {"deletedSuppressed": False, "sourceDeleted": False},
        "createdAt": "2026-06-01T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:00:00.000Z",
        "serverRevision": 1,
    }
    candidates_collection = FakeCollectionRef(
        {
            candidate["candidateId"]: FakeDocumentRef(
                candidate["candidateId"],
                FakeSnapshot(candidate["candidateId"], exists=True, data=candidate),
            )
        }
    )
    user_ref = FakeUserRef({"smartMemoryCandidates": candidates_collection})
    monkeypatch.setattr(smart_memory_service, "get_firestore", lambda: FakeClient(user_ref))

    result = asyncio.run(smart_memory_service.list_candidates("user-1", limit_count=9))

    assert result[0]["candidateId"] == candidate["candidateId"]
    assert candidates_collection.limit_calls == [9]


def test_item_patch_rejects_unsupported_user_value_field() -> None:
    item = _active_item()
    client, transaction, _item_ref, _mutation_ref, _tombstone_ref = _client_for_item(item)

    with pytest.raises(ValueError, match="unsupported fields"):
        smart_memory_service._mutate_item_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            memory_item_id="portion-oats",
            kind="item_patch",
            client_mutation_id="mutation-1",
            payload_hash="hash-1",
            patch_payload={"userValue": {"amount": 60, "unit": "g", "notes": "raw"}},
        )


def test_item_patch_rejects_nested_value_under_allowed_product_key() -> None:
    item = _ingredient_selection_item()
    client, transaction, _item_ref, _mutation_ref, _tombstone_ref = _client_for_item(item)

    with pytest.raises(ValueError, match="fields must be strings"):
        smart_memory_service._mutate_item_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            memory_item_id=item["memoryItemId"],
            kind="item_patch",
            client_mutation_id="mutation-1",
            payload_hash="hash-1",
            patch_payload={
                "userValue": {
                    "displayLabel": "Oats",
                    "alias": {"raw": "nested"},
                }
            },
        )


def test_item_patch_rejects_non_string_reason_code() -> None:
    item = _review_correction_item()
    client, transaction, _item_ref, _mutation_ref, _tombstone_ref = _client_for_item(item)

    with pytest.raises(ValueError, match="reasonCode must be a string"):
        smart_memory_service._mutate_item_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            memory_item_id=item["memoryItemId"],
            kind="item_patch",
            client_mutation_id="mutation-1",
            payload_hash="hash-1",
            patch_payload={
                "userValue": {
                    "amount": 60,
                    "unit": "g",
                    "reasonCode": {"raw": "nested"},
                }
            },
        )


def test_item_patch_rejects_unknown_reason_code() -> None:
    item = _review_correction_item()
    client, transaction, _item_ref, _mutation_ref, _tombstone_ref = _client_for_item(item)

    with pytest.raises(ValueError, match="reasonCode is unsupported"):
        smart_memory_service._mutate_item_transaction(
            cast(firestore.Transaction, transaction),
            client=client,  # type: ignore[arg-type]
            user_id="user-1",
            memory_item_id=item["memoryItemId"],
            kind="item_patch",
            client_mutation_id="mutation-1",
            payload_hash="hash-1",
            patch_payload={
                "userValue": {
                    "amount": 60,
                    "unit": "g",
                    "reasonCode": "unknown_reason",
                }
            },
        )


def test_read_export_filters_deleted_items_and_suppressed_candidates() -> None:
    active_item_ref = FakeDocumentRef(
        "active-1",
        FakeSnapshot("active-1", exists=True, data={"id": "active-1", "state": "active"}),
    )
    deleted_item_ref = FakeDocumentRef(
        "deleted-1",
        FakeSnapshot(
            "deleted-1",
            exists=True,
            data={"id": "deleted-1", "state": "deleted_suppressed", "subject": {"raw": True}},
        ),
    )
    candidate_ref = FakeDocumentRef(
        "candidate-1",
        FakeSnapshot("candidate-1", exists=True, data={"id": "candidate-1", "state": "candidate"}),
    )
    source_deleted_candidate_ref = FakeDocumentRef(
        "candidate-2",
        FakeSnapshot(
            "candidate-2",
            exists=True,
            data={"id": "candidate-2", "state": "source_deleted"},
        ),
    )
    items_collection = FakeCollectionRef(
        {"active-1": active_item_ref, "deleted-1": deleted_item_ref}
    )
    candidates_collection = FakeCollectionRef(
        {
            "candidate-1": candidate_ref,
            "candidate-2": source_deleted_candidate_ref,
        }
    )
    settings_collection = FakeCollectionRef({})
    tombstones_collection = FakeCollectionRef({})
    mutation_dedupe_collection = FakeCollectionRef({})
    user_ref = FakeUserRef(
        {
            "smartMemory": items_collection,
            "smartMemoryCandidates": candidates_collection,
            "smartMemorySettings": settings_collection,
            "smartMemoryTombstones": tombstones_collection,
            "smartMemoryMutationDedupe": mutation_dedupe_collection,
        }
    )

    export = smart_memory_service.read_export(cast(firestore.DocumentReference, user_ref))

    assert [item["id"] for item in export["items"]] == ["active-1"]
    assert [candidate["id"] for candidate in export["candidates"]] == ["candidate-1"]
    assert items_collection.limit_calls == [smart_memory_service.MAX_EXPORT_COLLECTION_DOCS]
    assert candidates_collection.limit_calls == [
        smart_memory_service.MAX_EXPORT_COLLECTION_DOCS
    ]
    assert settings_collection.limit_calls == [smart_memory_service.MAX_EXPORT_COLLECTION_DOCS]
    assert tombstones_collection.limit_calls == [
        smart_memory_service.MAX_EXPORT_COLLECTION_DOCS
    ]
    assert mutation_dedupe_collection.limit_calls == [
        smart_memory_service.MAX_EXPORT_COLLECTION_DOCS
    ]


def test_candidate_upsert_request_rejects_raw_provider_payload() -> None:
    with pytest.raises(ValueError, match="providerPayload"):
        SmartMemoryCandidateUpsertRequest.model_validate(
            {
                "clientMutationId": "candidate-1",
                "candidateId": "candidate-oats",
                "memoryType": "typical_portion",
                "subject": {"kind": "ingredient", "key": "oats"},
                "evidenceSummary": {"providerPayload": {"raw": True}},
            }
        )


def test_candidate_upsert_request_rejects_extra_source_ref_fields() -> None:
    with pytest.raises(ValueError, match="hashed identifiers"):
        SmartMemoryCandidateUpsertRequest.model_validate(
            {
                "clientMutationId": "candidate-1",
                "candidateId": "candidate-oats",
                "memoryType": "typical_portion",
                "subject": {"kind": "ingredient_alias", "aliasHash": "alias-hash-oats"},
                "sourceRefs": [
                    {
                        "kind": "meal_portion_observation",
                        "sourceHash": "source-hash-1",
                        "extra": "not-allowed",
                    }
                ],
            }
        )


def test_item_patch_request_rejects_raw_review_diff() -> None:
    with pytest.raises(ValueError, match="rawReviewDiff"):
        SmartMemoryItemPatchRequest.model_validate(
            {
                "clientMutationId": "patch-1",
                "userValue": {"rawReviewDiff": {"before": 1, "after": 2}},
            }
        )


def test_settings_update_request_strips_client_mutation_id() -> None:
    payload = SmartMemorySettingsUpdateRequest.model_validate(
        {"clientMutationId": " settings-1 ", "enabled": False}
    )

    assert payload.clientMutationId == "settings-1"
