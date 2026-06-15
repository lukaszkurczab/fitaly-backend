import asyncio
from dataclasses import asdict
from typing import Any, cast

import pytest

from app.schemas.smart_memory import FORBIDDEN_MEMORY_PAYLOAD_KEYS
from app.services.smart_memory_capture_service import (
    SmartMemoryCaptureDecision,
    build_review_correction_signals_from_signal_payloads,
    build_typical_portion_observations_from_meal_snapshots,
    capture_review_correction_candidate_from_signals,
    capture_typical_portion_candidate_from_meal_snapshots,
    evaluate_review_correction_candidate,
    evaluate_typical_portion_candidate,
    review_correction_subject_suppression_key,
    source_hashes_for_review_correction_signals,
    source_hashes_for_typical_portion_meal_snapshot,
    subject_suppression_key,
)


def _meal(
    meal_id: str,
    day_key: str | None,
    *,
    name: str = "Oats",
    amount: float = 60,
    unit: str | None = "g",
    deleted: bool = False,
    ingredient_id: str = "ingredient-1",
) -> dict[str, object]:
    ingredient: dict[str, object] = {
        "id": ingredient_id,
        "name": name,
        "amount": amount,
        "kcal": 200,
        "protein": 6,
        "fat": 4,
        "carbs": 30,
    }
    if unit is not None:
        ingredient["unit"] = unit
    payload: dict[str, object] = {
        "id": meal_id,
        "dayKey": day_key,
        "updatedAt": f"{day_key}T08:00:00.000Z" if day_key else "2026-06-01T08:00:00.000Z",
        "deleted": deleted,
        "ingredients": [ingredient],
        "totals": {"kcal": 200, "protein": 6, "fat": 4, "carbs": 30},
        "aiMeta": {"model": "provider-model"},
        "notes": "raw user note",
    }
    return payload


def _correction_signal(
    signal_id: str,
    day_key: str | None,
    *,
    subject_key: str = "Oats",
    correction_field: str = "amount",
    before_amount: float = 40,
    before_unit: str = "g",
    after_amount: float = 60,
    after_unit: str = "g",
    surface: str = "review",
    deleted: bool = False,
) -> dict[str, object]:
    return {
        "sourceSignalId": signal_id,
        "dayKey": day_key,
        "observedAt": (
            f"{day_key}T08:15:00.000Z"
            if day_key
            else "2026-06-01T08:15:00.000Z"
        ),
        "surface": surface,
        "subjectKey": subject_key,
        "correctionField": correction_field,
        "before": {"amount": before_amount, "unit": before_unit},
        "after": {"amount": after_amount, "unit": after_unit},
        "deleted": deleted,
    }


def _decision_payload(decision: SmartMemoryCaptureDecision) -> dict[str, Any]:
    payload = cast(dict[str, Any], asdict(decision))
    if decision.candidate_request is not None:
        payload["candidate_request"] = decision.candidate_request.model_dump()
    return payload


def _assert_no_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        payload = cast(dict[object, object], value)
        assert not (
            {key for key in payload if isinstance(key, str)}
            & FORBIDDEN_MEMORY_PAYLOAD_KEYS
        )
        for item in payload.values():
            _assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _assert_no_forbidden_keys(item)


def test_typical_portion_candidate_ready_from_three_distinct_days() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-3", "2026-06-03", amount=61, ingredient_id="i-3"),
            _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", amount=59, ingredient_id="i-2"),
        ]
    )

    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.state == "candidate_ready"
    assert decision.reason_code == "threshold_met"
    assert decision.memory_type == "typical_portion"
    assert decision.candidate_request is not None
    assert decision.candidate_request.memoryType == "typical_portion"
    assert decision.candidate_request.subject["kind"] == "ingredient_alias"
    assert "aliasHash" in decision.candidate_request.subject
    assert decision.candidate_request.evidenceSummary["eligibleObservationCount"] == 3
    assert decision.candidate_request.evidenceSummary["distinctDayCount"] == 3
    assert decision.candidate_request.evidenceSummary["proposedValue"] == {
        "amount": 60,
        "unit": "g",
    }
    assert decision.candidate_request.confidenceReasonCodes == ["distinct_days_met"]
    assert len(decision.candidate_request.sourceRefs) == 3


def test_candidate_ids_are_stable_for_same_input_in_different_order() -> None:
    meals = [
        _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
        _meal("meal-2", "2026-06-02", amount=60, ingredient_id="i-2"),
        _meal("meal-3", "2026-06-03", amount=60, ingredient_id="i-3"),
    ]
    first = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=build_typical_portion_observations_from_meal_snapshots(meals),
    )
    second = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=build_typical_portion_observations_from_meal_snapshots(
            list(reversed(meals))
        ),
    )

    assert first.candidate_request is not None
    assert second.candidate_request is not None
    assert first.candidate_request.candidateId == second.candidate_request.candidateId
    assert (
        first.candidate_request.clientMutationId
        == second.candidate_request.clientMutationId
    )


def test_candidate_refresh_changes_mutation_id_without_changing_candidate_id() -> None:
    first = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=build_typical_portion_observations_from_meal_snapshots(
            [
                _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
                _meal("meal-2", "2026-06-02", amount=60, ingredient_id="i-2"),
                _meal("meal-3", "2026-06-03", amount=60, ingredient_id="i-3"),
            ]
        ),
    )
    refreshed = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=build_typical_portion_observations_from_meal_snapshots(
            [
                _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
                _meal("meal-2", "2026-06-02", amount=60, ingredient_id="i-2"),
                _meal("meal-3", "2026-06-03", amount=60, ingredient_id="i-3"),
                _meal("meal-4", "2026-06-04", amount=60, ingredient_id="i-4"),
            ]
        ),
    )

    assert first.candidate_request is not None
    assert refreshed.candidate_request is not None
    assert first.candidate_request.candidateId == refreshed.candidate_request.candidateId
    assert (
        first.candidate_request.clientMutationId
        != refreshed.candidate_request.clientMutationId
    )
    assert refreshed.candidate_request.evidenceSummary["eligibleObservationCount"] == 4
    assert len(refreshed.candidate_request.sourceRefs) == 3


def test_same_day_duplicates_do_not_satisfy_distinct_day_threshold() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", ingredient_id="i-1"),
            _meal("meal-2", "2026-06-01", ingredient_id="i-2"),
            _meal("meal-3", "2026-06-02", ingredient_id="i-3"),
        ]
    )

    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.state == "insufficient"
    assert decision.reason_code == "insufficient_distinct_days"
    assert decision.evidence_summary["eligibleObservationCount"] == 3
    assert decision.evidence_summary["distinctDayCount"] == 2
    assert decision.candidate_request is None


def test_deleted_invalid_unsupported_and_missing_refs_are_skipped() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("deleted", "2026-06-01", deleted=True),
            _meal("zero", "2026-06-01", amount=0),
            _meal("unsupported", "2026-06-01", unit="oz"),
            _meal("missing-day", None),
            _meal("valid", "2026-06-02"),
        ],
        source_deleted_refs={"meal:valid"},
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert observations == []
    assert decision.state == "insufficient"
    assert decision.reason_code == "no_eligible_observations"


def test_disabled_memory_returns_suppressed_decision() -> None:
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=[],
        memory_enabled=False,
    )

    assert decision.state == "suppressed"
    assert decision.reason_code == "memory_disabled"
    assert decision.candidate_request is None


def test_tombstoned_subject_key_returns_suppressed_decision() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", ingredient_id="i-2"),
            _meal("meal-3", "2026-06-03", ingredient_id="i-3"),
        ]
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
        suppressed_subject_keys={subject_suppression_key("oats")},
    )

    assert decision.state == "suppressed"
    assert decision.reason_code == "subject_suppressed"
    assert decision.subject["kind"] == "ingredient_alias"
    assert "aliasHash" in decision.subject
    assert decision.candidate_request is None


def test_mixed_incompatible_units_do_not_produce_candidate() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", unit="g", ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", unit="ml", ingredient_id="i-2"),
            _meal("meal-3", "2026-06-03", unit="g", ingredient_id="i-3"),
        ]
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.state == "conflict"
    assert decision.reason_code == "mixed_incompatible_units"
    assert decision.candidate_request is None


def test_conflicting_amount_clusters_do_not_produce_candidate() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", amount=120, ingredient_id="i-2"),
            _meal("meal-3", "2026-06-03", amount=60, ingredient_id="i-3"),
        ]
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.state == "conflict"
    assert decision.reason_code == "conflicting_amount_clusters"
    assert decision.candidate_request is None


def test_candidate_payload_is_bounded_and_excludes_raw_meal_data() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", ingredient_id="i-2"),
            _meal("meal-3", "2026-06-03", ingredient_id="i-3"),
            _meal("meal-4", "2026-06-04", ingredient_id="i-4"),
        ]
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.candidate_request is not None
    payload = _decision_payload(decision)
    _assert_no_forbidden_keys(payload)
    payload_text = str(payload)
    assert "kcal" not in payload_text
    assert "protein" not in payload_text
    assert "provider-model" not in payload_text
    assert "raw user note" not in payload_text
    assert "meal-1" not in payload_text
    assert "ingredientId" not in payload_text
    assert "oats" not in payload_text
    assert "review_correction" not in payload_text
    assert len(decision.candidate_request.sourceRefs) == 3
    assert all(
        set(source_ref) == {"kind", "sourceHash"}
        for source_ref in decision.candidate_request.sourceRefs
    )


def test_final_saved_snapshots_never_create_review_correction_memory() -> None:
    observations = build_typical_portion_observations_from_meal_snapshots(
        [
            _meal("meal-1", "2026-06-01", ingredient_id="i-1"),
            _meal("meal-2", "2026-06-02", ingredient_id="i-2"),
            _meal("meal-3", "2026-06-03", ingredient_id="i-3"),
        ]
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id="user-1",
        observations=observations,
    )

    assert decision.candidate_request is not None
    dumped: dict[str, Any] = decision.candidate_request.model_dump()
    assert dumped["memoryType"] == "typical_portion"
    assert dumped["memoryType"] != "review_correction"


def test_review_correction_candidate_ready_from_explicit_bounded_signals() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-3", "2026-06-03", after_amount=61),
            _correction_signal("signal-1", "2026-06-01", after_amount=60),
            _correction_signal("signal-2", "2026-06-02", after_amount=59),
        ]
    )

    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "candidate_ready"
    assert decision.reason_code == "threshold_met"
    assert decision.memory_type == "review_correction"
    assert decision.candidate_request is not None
    assert decision.candidate_request.memoryType == "review_correction"
    assert decision.candidate_request.subject == decision.subject
    assert decision.candidate_request.subject["kind"] == "ingredient_alias"
    assert "aliasHash" in decision.candidate_request.subject
    assert decision.candidate_request.subject["correctionField"] == "amount"
    assert decision.candidate_request.evidenceSummary["thresholdVersion"] == (
        "review_correction_v1"
    )
    assert decision.candidate_request.evidenceSummary["surfaces"] == ["review"]
    assert decision.candidate_request.evidenceSummary["eligibleObservationCount"] == 3
    assert decision.candidate_request.evidenceSummary["distinctDayCount"] == 3
    assert decision.candidate_request.evidenceSummary["proposedValue"] == {
        "amount": 60,
        "unit": "g",
        "reasonCode": "user_corrected",
    }
    assert decision.candidate_request.evidenceSummary["beforeValueCluster"] == {
        "strategy": "median_with_fixed_tolerance",
        "amount": 40,
        "unit": "g",
        "absoluteTolerance": 5.0,
        "relativeTolerance": 0.1,
    }
    assert decision.candidate_request.confidenceReasonCodes == [
        "distinct_days_met",
        "consistent_user_review",
    ]
    assert len(decision.candidate_request.sourceRefs) == 3
    assert all(
        set(source_ref) == {"kind", "sourceHash"}
        for source_ref in decision.candidate_request.sourceRefs
    )


def test_review_correction_payload_is_bounded_and_excludes_raw_signal_data() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01", subject_key="Oats"),
            _correction_signal("signal-2", "2026-06-02", subject_key="Oats"),
            _correction_signal("signal-3", "2026-06-03", subject_key="Oats"),
            _correction_signal("signal-4", "2026-06-04", subject_key="Oats"),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.candidate_request is not None
    payload = _decision_payload(decision)
    _assert_no_forbidden_keys(payload)
    payload_text = str(payload)
    assert "signal-1" not in payload_text
    assert "dayKey" not in payload_text
    assert "Oats" not in payload_text
    assert "oats" not in payload_text
    assert "'before':" not in payload_text
    assert '"before":' not in payload_text
    assert "rawDiff" not in payload_text
    assert "mealId" not in payload_text
    assert "ingredientId" not in payload_text
    assert len(decision.candidate_request.sourceRefs) == 3


def test_review_correction_requires_explicit_before_after_signal_data() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            {
                "sourceSignalId": "signal-final-snapshot-only",
                "dayKey": "2026-06-01",
                "subjectKey": "Oats",
                "correctionField": "amount",
                "after": {"amount": 60, "unit": "g"},
            },
            _meal("meal-final", "2026-06-02"),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert signals == []
    assert decision.state == "insufficient"
    assert decision.reason_code == "no_eligible_observations"
    assert decision.candidate_request is None


def test_review_correction_disabled_memory_returns_suppressed_decision() -> None:
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=[],
        memory_enabled=False,
    )

    assert decision.state == "suppressed"
    assert decision.reason_code == "memory_disabled"
    assert decision.memory_type == "review_correction"
    assert decision.candidate_request is None


def test_review_correction_tombstoned_subject_key_returns_suppressed_decision() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01"),
            _correction_signal("signal-2", "2026-06-02"),
            _correction_signal("signal-3", "2026-06-03"),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
        suppressed_subject_keys={review_correction_subject_suppression_key("oats", "amount")},
    )

    assert decision.state == "suppressed"
    assert decision.reason_code == "subject_suppressed"
    assert decision.memory_type == "review_correction"
    assert decision.subject["kind"] == "ingredient_alias"
    assert "aliasHash" in decision.subject
    assert decision.candidate_request is None


def test_review_correction_same_day_duplicates_do_not_satisfy_threshold() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01"),
            _correction_signal("signal-2", "2026-06-01"),
            _correction_signal("signal-3", "2026-06-02"),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "insufficient"
    assert decision.reason_code == "insufficient_distinct_days"
    assert decision.evidence_summary["eligibleObservationCount"] == 3
    assert decision.evidence_summary["distinctDayCount"] == 2
    assert decision.candidate_request is None


def test_review_correction_incompatible_units_do_not_produce_candidate() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01", after_unit="g"),
            _correction_signal(
                "signal-2",
                "2026-06-02",
                before_unit="ml",
                after_unit="ml",
            ),
            _correction_signal("signal-3", "2026-06-03", after_unit="g"),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "conflict"
    assert decision.reason_code == "mixed_incompatible_units"
    assert decision.evidence_summary["eligibleObservationCount"] == 3
    assert decision.candidate_request is None


def test_review_correction_conflicting_after_clusters_do_not_produce_candidate() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01", after_amount=60),
            _correction_signal("signal-2", "2026-06-02", after_amount=120),
            _correction_signal("signal-3", "2026-06-03", after_amount=60),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "conflict"
    assert decision.reason_code == "conflicting_amount_clusters"
    assert decision.evidence_summary["eligibleObservationCount"] == 3
    assert decision.candidate_request is None


def test_review_correction_conflicting_before_clusters_do_not_produce_candidate() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-1", "2026-06-01", before_amount=40),
            _correction_signal("signal-2", "2026-06-02", before_amount=120),
            _correction_signal("signal-3", "2026-06-03", before_amount=40),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "conflict"
    assert decision.reason_code == "conflicting_amount_clusters"
    assert decision.evidence_summary["eligibleObservationCount"] == 3
    assert decision.candidate_request is None


def test_review_correction_unit_edit_signals_are_traceable_and_candidate_ready() -> None:
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal(
                "signal-1",
                "2026-06-01",
                correction_field="unit",
                before_amount=1,
                before_unit="piece",
                after_amount=60,
                after_unit="g",
                surface="edit",
            ),
            _correction_signal(
                "signal-2",
                "2026-06-02",
                correction_field="unit",
                before_amount=1,
                before_unit="piece",
                after_amount=59,
                after_unit="g",
                surface="edit",
            ),
            _correction_signal(
                "signal-3",
                "2026-06-03",
                correction_field="unit",
                before_amount=1,
                before_unit="piece",
                after_amount=61,
                after_unit="g",
                surface="edit",
            ),
        ]
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert decision.state == "candidate_ready"
    assert decision.candidate_request is not None
    assert decision.candidate_request.subject["correctionField"] == "unit"
    assert decision.candidate_request.evidenceSummary["surfaces"] == ["edit"]
    assert decision.candidate_request.evidenceSummary["beforeValueCluster"] == {
        "strategy": "median_with_fixed_tolerance",
        "amount": 1,
        "unit": "piece",
        "absoluteTolerance": 5.0,
        "relativeTolerance": 0.1,
    }
    assert decision.candidate_request.evidenceSummary["proposedValue"] == {
        "amount": 60,
        "unit": "g",
        "reasonCode": "user_corrected",
    }


def test_review_correction_deleted_and_source_deleted_refs_are_skipped() -> None:
    skipped_source = _correction_signal("signal-skipped", "2026-06-01")
    source_deleted_hashes = source_hashes_for_review_correction_signals([skipped_source])
    signals = build_review_correction_signals_from_signal_payloads(
        [
            _correction_signal("signal-deleted", "2026-06-01", deleted=True),
            skipped_source,
            _correction_signal("signal-1", "2026-06-01"),
            _correction_signal("signal-2", "2026-06-02"),
            _correction_signal("signal-3", "2026-06-03"),
        ],
        source_deleted_refs=set(source_deleted_hashes),
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id="user-1",
        signals=signals,
    )

    assert len(source_deleted_hashes) == 1
    assert "signal-skipped" not in source_deleted_hashes[0]
    assert "2026-06-01" not in source_deleted_hashes[0]
    assert len(signals) == 3
    assert all(signal.source_ref["sourceSignalId"] != "signal-skipped" for signal in signals)
    assert decision.state == "candidate_ready"
    assert decision.candidate_request is not None


def test_source_hashes_for_meal_snapshot_are_bounded_and_private() -> None:
    source_hashes = source_hashes_for_typical_portion_meal_snapshot(
        _meal("meal-1", "2026-06-01", ingredient_id="ingredient-1")
    )

    assert len(source_hashes) == 1
    assert source_hashes[0]
    assert "meal-1" not in source_hashes[0]
    assert "ingredient-1" not in source_hashes[0]


def test_capture_ready_path_upserts_candidate_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    mutation_result: dict[str, Any] = {
        "document": {"candidateId": "candidate-from-capture"},
        "applied": True,
    }

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        return mutation_result

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    result = asyncio.run(
        capture_typical_portion_candidate_from_meal_snapshots(
            owner_user_id="user-1",
            meal_snapshots=[
                _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
                _meal("meal-2", "2026-06-02", amount=59, ingredient_id="i-2"),
                _meal("meal-3", "2026-06-03", amount=61, ingredient_id="i-3"),
            ],
        )
    )

    assert result.decision.state == "candidate_ready"
    assert result.decision.candidate_request is not None
    assert result.mutation_result == mutation_result
    assert calls == [("user-1", result.decision.candidate_request)]


def test_capture_non_ready_decision_does_not_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        return {"document": {}, "applied": True}

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    result = asyncio.run(
        capture_typical_portion_candidate_from_meal_snapshots(
            owner_user_id="user-1",
            meal_snapshots=[
                _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
                _meal("meal-2", "2026-06-02", amount=59, ingredient_id="i-2"),
            ],
        )
    )

    assert result.decision.state == "insufficient"
    assert result.decision.candidate_request is None
    assert result.mutation_result is None
    assert calls == []


def test_capture_upsert_errors_propagate_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        raise RuntimeError("canonical upsert failed")

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    with pytest.raises(RuntimeError, match="canonical upsert failed"):
        asyncio.run(
            capture_typical_portion_candidate_from_meal_snapshots(
                owner_user_id="user-1",
                meal_snapshots=[
                    _meal("meal-1", "2026-06-01", amount=60, ingredient_id="i-1"),
                    _meal("meal-2", "2026-06-02", amount=59, ingredient_id="i-2"),
                    _meal("meal-3", "2026-06-03", amount=61, ingredient_id="i-3"),
                ],
            )
        )

    assert len(calls) == 1


def test_capture_review_correction_ready_path_upserts_candidate_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    mutation_result: dict[str, Any] = {
        "document": {"candidateId": "candidate-from-correction-capture"},
        "applied": True,
    }

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        return mutation_result

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    result = asyncio.run(
        capture_review_correction_candidate_from_signals(
            owner_user_id="user-1",
            correction_signals=[
                _correction_signal("signal-1", "2026-06-01", after_amount=60),
                _correction_signal("signal-2", "2026-06-02", after_amount=59),
                _correction_signal("signal-3", "2026-06-03", after_amount=61),
            ],
        )
    )

    assert result.decision.state == "candidate_ready"
    assert result.decision.candidate_request is not None
    assert result.decision.candidate_request.memoryType == "review_correction"
    assert result.mutation_result == mutation_result
    assert calls == [("user-1", result.decision.candidate_request)]


def test_capture_review_correction_non_ready_decision_does_not_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        return {"document": {}, "applied": True}

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    result = asyncio.run(
        capture_review_correction_candidate_from_signals(
            owner_user_id="user-1",
            correction_signals=[
                _correction_signal("signal-1", "2026-06-01", after_amount=60),
                _correction_signal("signal-2", "2026-06-02", after_amount=59),
            ],
        )
    )

    assert result.decision.state == "insufficient"
    assert result.decision.candidate_request is None
    assert result.mutation_result is None
    assert calls == []


def test_capture_review_correction_upsert_errors_propagate_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_upsert_candidate(user_id: str, payload: Any) -> dict[str, Any]:
        calls.append((user_id, payload))
        raise RuntimeError("canonical correction upsert failed")

    monkeypatch.setattr(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        fake_upsert_candidate,
    )

    with pytest.raises(RuntimeError, match="canonical correction upsert failed"):
        asyncio.run(
            capture_review_correction_candidate_from_signals(
                owner_user_id="user-1",
                correction_signals=[
                    _correction_signal("signal-1", "2026-06-01", after_amount=60),
                    _correction_signal("signal-2", "2026-06-02", after_amount=59),
                    _correction_signal("signal-3", "2026-06-03", after_amount=61),
                ],
            )
        )

    assert len(calls) == 1
