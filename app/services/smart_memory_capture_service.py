"""Pure Smart Memory capture evaluation helpers."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from statistics import median
from typing import Any, Literal, cast

from app.schemas.smart_memory import SmartMemoryCandidateUpsertRequest
from app.services import smart_memory_service
from app.services.smart_memory_service import SmartMemoryMutationResult

SmartMemoryCaptureDecisionState = Literal[
    "candidate_ready",
    "insufficient",
    "suppressed",
    "conflict",
]
SmartMemoryCaptureReasonCode = Literal[
    "threshold_met",
    "memory_disabled",
    "subject_suppressed",
    "no_eligible_observations",
    "insufficient_eligible_observations",
    "insufficient_distinct_days",
    "mixed_incompatible_units",
    "conflicting_amount_clusters",
]
PortionUnit = Literal["g", "ml", "piece", "serving"]
ReviewCorrectionField = Literal["amount", "unit"]
ReviewCorrectionSurface = Literal["review", "edit"]
SmartMemoryCaptureMemoryType = Literal["typical_portion", "review_correction"]

SUPPORTED_PORTION_UNITS: tuple[PortionUnit, ...] = ("g", "ml", "piece", "serving")
SUPPORTED_REVIEW_CORRECTION_FIELDS: tuple[ReviewCorrectionField, ...] = (
    "amount",
    "unit",
)
SUPPORTED_REVIEW_CORRECTION_SURFACES: tuple[ReviewCorrectionSurface, ...] = (
    "review",
    "edit",
)
REQUIRED_PORTION_OBSERVATIONS = 3
REQUIRED_PORTION_DISTINCT_DAYS = 3
REVIEW_CORRECTION_CAPTURE_WINDOW_DAYS = 30
MAX_CANDIDATE_SOURCE_REFS = 3
AMOUNT_CLUSTER_RELATIVE_TOLERANCE = 0.10
AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE = 5.0
TYPICAL_PORTION_THRESHOLD_VERSION = "typical_portion_v1"
REVIEW_CORRECTION_THRESHOLD_VERSION = "review_correction_v1"
DELETED_SOURCE_STATES = {"deleted", "deleted_suppressed", "source_deleted"}


@dataclass(frozen=True)
class TypicalPortionObservation:
    subject_key: str
    subject_label: str
    amount: float
    unit: PortionUnit
    day_key: str
    source_ref: dict[str, Any]
    observed_at: str | None = None


@dataclass(frozen=True)
class ReviewCorrectionSignal:
    subject_key: str
    correction_field: ReviewCorrectionField
    before_amount: float
    before_unit: PortionUnit
    after_amount: float
    after_unit: PortionUnit
    day_key: str
    source_ref: dict[str, Any]
    surface: ReviewCorrectionSurface
    observed_at: str | None = None


@dataclass(frozen=True)
class SmartMemoryCaptureDecision:
    state: SmartMemoryCaptureDecisionState
    reason_code: SmartMemoryCaptureReasonCode
    memory_type: SmartMemoryCaptureMemoryType
    subject: dict[str, Any]
    evidence_summary: dict[str, Any]
    source_refs: list[dict[str, Any]]
    candidate_request: SmartMemoryCandidateUpsertRequest | None = None


@dataclass(frozen=True)
class SmartMemoryCaptureUpsertResult:
    decision: SmartMemoryCaptureDecision
    mutation_result: SmartMemoryMutationResult | None = None


def build_typical_portion_observations_from_meal_snapshots(
    meal_snapshots: Sequence[Mapping[str, object]],
    *,
    source_deleted_refs: Collection[str] = (),
) -> list[TypicalPortionObservation]:
    observations: list[TypicalPortionObservation] = []
    seen_source_keys: set[str] = set()
    for snapshot in meal_snapshots:
        if _is_source_deleted(snapshot):
            continue
        meal_id = _coerce_str(
            snapshot.get("id") or snapshot.get("mealId") or snapshot.get("cloudId")
        )
        day_key = _coerce_str(snapshot.get("dayKey"))
        if meal_id is None or day_key is None:
            continue
        meal_source_key = f"meal:{meal_id}"
        if meal_source_key in source_deleted_refs:
            continue

        ingredients = snapshot.get("ingredients")
        if not isinstance(ingredients, list):
            continue
        for raw_ingredient in cast(list[object], ingredients):
            if not isinstance(raw_ingredient, dict):
                continue
            ingredient = cast(dict[object, object], raw_ingredient)
            observation = _observation_from_ingredient(
                ingredient,
                meal_id=meal_id,
                day_key=day_key,
                observed_at=_coerce_str(snapshot.get("updatedAt"))
                or _coerce_str(snapshot.get("loggedAt")),
            )
            if observation is None:
                continue
            source_key = _source_ref_key(observation.source_ref)
            if source_key in seen_source_keys:
                continue
            seen_source_keys.add(source_key)
            observations.append(observation)
    return sorted(observations, key=_observation_sort_key)


def evaluate_typical_portion_candidate(
    *,
    owner_user_id: str,
    observations: Sequence[TypicalPortionObservation],
    memory_enabled: bool = True,
    suppressed_subject_keys: Collection[str] = (),
) -> SmartMemoryCaptureDecision:
    if not memory_enabled:
        return _decision(
            state="suppressed",
            reason_code="memory_disabled",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    eligible = sorted(observations, key=_observation_sort_key)
    if not eligible:
        return _decision(
            state="insufficient",
            reason_code="no_eligible_observations",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    subject_groups = _group_by_subject(eligible)
    suppressed_groups = [
        group
        for subject_key, group in subject_groups.items()
        if _subject_suppression_key(_subject_payload(subject_key)) in suppressed_subject_keys
        or subject_key in suppressed_subject_keys
    ]
    available_groups = [
        group
        for subject_key, group in subject_groups.items()
        if _subject_suppression_key(_subject_payload(subject_key)) not in suppressed_subject_keys
        and subject_key not in suppressed_subject_keys
    ]
    if suppressed_groups and not available_groups:
        group = suppressed_groups[0]
        subject = _subject_payload(group[0].subject_key)
        return _decision(
            state="suppressed",
            reason_code="subject_suppressed",
            subject=subject,
            evidence_summary=_evidence_summary(group, subject=subject),
        )

    ranked_groups = sorted(
        available_groups,
        key=lambda group: (
            -len(_distinct_days(group)),
            -len(group),
            group[0].subject_key,
        ),
    )
    if not ranked_groups:
        return _decision(
            state="insufficient",
            reason_code="no_eligible_observations",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    group = ranked_groups[0]
    subject = _subject_payload(group[0].subject_key)
    units = {observation.unit for observation in group}
    if len(units) > 1:
        return _decision(
            state="conflict",
            reason_code="mixed_incompatible_units",
            subject=subject,
            evidence_summary=_evidence_summary(group, subject=subject),
            source_refs=_bounded_source_refs(group),
        )

    if not _amounts_are_clustered([observation.amount for observation in group]):
        return _decision(
            state="conflict",
            reason_code="conflicting_amount_clusters",
            subject=subject,
            evidence_summary=_evidence_summary(group, subject=subject),
            source_refs=_bounded_source_refs(group),
        )

    evidence_summary = _evidence_summary(group, subject=subject)
    distinct_day_count = cast(int, evidence_summary["distinctDayCount"])
    eligible_count = cast(int, evidence_summary["eligibleObservationCount"])
    if eligible_count < REQUIRED_PORTION_OBSERVATIONS:
        return _decision(
            state="insufficient",
            reason_code="insufficient_eligible_observations",
            subject=subject,
            evidence_summary=evidence_summary,
            source_refs=_bounded_source_refs(group),
        )
    if distinct_day_count < REQUIRED_PORTION_DISTINCT_DAYS:
        return _decision(
            state="insufficient",
            reason_code="insufficient_distinct_days",
            subject=subject,
            evidence_summary=evidence_summary,
            source_refs=_bounded_source_refs(group),
        )

    candidate = _candidate_request(
        owner_user_id=owner_user_id,
        subject=subject,
        group=group,
        evidence_summary=evidence_summary,
    )
    return _decision(
        state="candidate_ready",
        reason_code="threshold_met",
        subject=subject,
        evidence_summary=evidence_summary,
        source_refs=candidate.sourceRefs,
        candidate_request=candidate,
    )


def build_review_correction_signals_from_signal_payloads(
    correction_signals: Sequence[Mapping[str, object]],
    *,
    source_deleted_refs: Collection[str] = (),
) -> list[ReviewCorrectionSignal]:
    signals: list[ReviewCorrectionSignal] = []
    seen_source_keys: set[str] = set()
    for payload in correction_signals:
        if _is_source_deleted(payload):
            continue
        signal = _review_correction_signal_from_payload(payload)
        if signal is None:
            continue
        source_key = _review_correction_source_hash(signal.source_ref)
        if source_key in source_deleted_refs or source_key in seen_source_keys:
            continue
        seen_source_keys.add(source_key)
        signals.append(signal)
    return sorted(signals, key=_review_correction_sort_key)


def evaluate_review_correction_candidate(
    *,
    owner_user_id: str,
    signals: Sequence[ReviewCorrectionSignal],
    memory_enabled: bool = True,
    suppressed_subject_keys: Collection[str] = (),
    reference_day_key: str | None = None,
) -> SmartMemoryCaptureDecision:
    if not memory_enabled:
        return _decision(
            state="suppressed",
            reason_code="memory_disabled",
            memory_type="review_correction",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    eligible = _windowed_review_correction_signals(
        signals,
        reference_day_key=reference_day_key,
    )
    if not eligible:
        return _decision(
            state="insufficient",
            reason_code="no_eligible_observations",
            memory_type="review_correction",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    subject_groups = _group_review_corrections_by_subject_and_field(eligible)
    suppressed_groups = [
        group
        for group_key, group in subject_groups.items()
        if _review_correction_subject_suppression_key(
            _review_correction_subject_payload(group_key[0], group_key[1])
        )
        in suppressed_subject_keys
        or group_key[0] in suppressed_subject_keys
    ]
    available_groups = [
        group
        for group_key, group in subject_groups.items()
        if _review_correction_subject_suppression_key(
            _review_correction_subject_payload(group_key[0], group_key[1])
        )
        not in suppressed_subject_keys
        and group_key[0] not in suppressed_subject_keys
    ]
    if suppressed_groups and not available_groups:
        group = suppressed_groups[0]
        subject = _review_correction_subject_payload(
            group[0].subject_key,
            group[0].correction_field,
        )
        return _decision(
            state="suppressed",
            reason_code="subject_suppressed",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=_review_correction_evidence_summary(group, subject=subject),
        )

    ranked_groups = sorted(
        available_groups,
        key=lambda group: (
            -len(_review_correction_distinct_days(group)),
            -len(group),
            group[0].subject_key,
            group[0].correction_field,
        ),
    )
    if not ranked_groups:
        return _decision(
            state="insufficient",
            reason_code="no_eligible_observations",
            memory_type="review_correction",
            evidence_summary={"eligibleObservationCount": 0, "distinctDayCount": 0},
        )

    group = ranked_groups[0]
    subject = _review_correction_subject_payload(
        group[0].subject_key,
        group[0].correction_field,
    )
    after_units = {signal.after_unit for signal in group}
    if len(after_units) > 1:
        return _decision(
            state="conflict",
            reason_code="mixed_incompatible_units",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=_review_correction_evidence_summary(group, subject=subject),
            source_refs=_bounded_review_correction_source_refs(group),
        )

    before_units = {signal.before_unit for signal in group}
    if len(before_units) > 1:
        return _decision(
            state="conflict",
            reason_code="mixed_incompatible_units",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=_review_correction_evidence_summary(group, subject=subject),
            source_refs=_bounded_review_correction_source_refs(group),
        )

    if not _amounts_are_clustered([signal.before_amount for signal in group]):
        return _decision(
            state="conflict",
            reason_code="conflicting_amount_clusters",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=_review_correction_evidence_summary(group, subject=subject),
            source_refs=_bounded_review_correction_source_refs(group),
        )

    if not _amounts_are_clustered([signal.after_amount for signal in group]):
        return _decision(
            state="conflict",
            reason_code="conflicting_amount_clusters",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=_review_correction_evidence_summary(group, subject=subject),
            source_refs=_bounded_review_correction_source_refs(group),
        )

    evidence_summary = _review_correction_evidence_summary(group, subject=subject)
    distinct_day_count = cast(int, evidence_summary["distinctDayCount"])
    eligible_count = cast(int, evidence_summary["eligibleObservationCount"])
    if eligible_count < REQUIRED_PORTION_OBSERVATIONS:
        return _decision(
            state="insufficient",
            reason_code="insufficient_eligible_observations",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=evidence_summary,
            source_refs=_bounded_review_correction_source_refs(group),
        )
    if distinct_day_count < REQUIRED_PORTION_DISTINCT_DAYS:
        return _decision(
            state="insufficient",
            reason_code="insufficient_distinct_days",
            memory_type="review_correction",
            subject=subject,
            evidence_summary=evidence_summary,
            source_refs=_bounded_review_correction_source_refs(group),
        )

    candidate = _review_correction_candidate_request(
        owner_user_id=owner_user_id,
        subject=subject,
        group=group,
        evidence_summary=evidence_summary,
    )
    return _decision(
        state="candidate_ready",
        reason_code="threshold_met",
        memory_type="review_correction",
        subject=subject,
        evidence_summary=evidence_summary,
        source_refs=candidate.sourceRefs,
        candidate_request=candidate,
    )


async def capture_typical_portion_candidate_from_meal_snapshots(
    *,
    owner_user_id: str,
    meal_snapshots: Sequence[Mapping[str, object]],
    memory_enabled: bool = True,
    suppressed_subject_keys: Collection[str] = (),
    source_deleted_refs: Collection[str] = (),
) -> SmartMemoryCaptureUpsertResult:
    observations = build_typical_portion_observations_from_meal_snapshots(
        meal_snapshots,
        source_deleted_refs=source_deleted_refs,
    )
    decision = evaluate_typical_portion_candidate(
        owner_user_id=owner_user_id,
        observations=observations,
        memory_enabled=memory_enabled,
        suppressed_subject_keys=suppressed_subject_keys,
    )
    if decision.state != "candidate_ready" or decision.candidate_request is None:
        return SmartMemoryCaptureUpsertResult(decision=decision)

    mutation_result = await smart_memory_service.upsert_candidate(
        owner_user_id,
        decision.candidate_request,
    )
    return SmartMemoryCaptureUpsertResult(
        decision=decision,
        mutation_result=mutation_result,
    )


async def capture_review_correction_candidate_from_signals(
    *,
    owner_user_id: str,
    correction_signals: Sequence[Mapping[str, object]],
    memory_enabled: bool = True,
    suppressed_subject_keys: Collection[str] = (),
    source_deleted_refs: Collection[str] = (),
    reference_day_key: str | None = None,
) -> SmartMemoryCaptureUpsertResult:
    signals = build_review_correction_signals_from_signal_payloads(
        correction_signals,
        source_deleted_refs=source_deleted_refs,
    )
    decision = evaluate_review_correction_candidate(
        owner_user_id=owner_user_id,
        signals=signals,
        memory_enabled=memory_enabled,
        suppressed_subject_keys=suppressed_subject_keys,
        reference_day_key=reference_day_key,
    )
    if decision.state != "candidate_ready" or decision.candidate_request is None:
        return SmartMemoryCaptureUpsertResult(decision=decision)

    mutation_result = await smart_memory_service.upsert_candidate(
        owner_user_id,
        decision.candidate_request,
    )
    return SmartMemoryCaptureUpsertResult(
        decision=decision,
        mutation_result=mutation_result,
    )


def subject_suppression_key(subject_key: str) -> str:
    return _subject_suppression_key(_subject_payload(subject_key))


def review_correction_subject_suppression_key(
    subject_key: str,
    correction_field: ReviewCorrectionField,
) -> str:
    return _review_correction_subject_suppression_key(
        _review_correction_subject_payload(subject_key, correction_field)
    )


def source_hashes_for_typical_portion_meal_snapshot(
    meal_snapshot: Mapping[str, object],
) -> list[str]:
    meal_id = _coerce_str(
        meal_snapshot.get("id") or meal_snapshot.get("mealId") or meal_snapshot.get("cloudId")
    )
    day_key = _coerce_str(meal_snapshot.get("dayKey"))
    ingredients = meal_snapshot.get("ingredients")
    if meal_id is None or day_key is None or not isinstance(ingredients, list):
        return []

    source_hashes: list[str] = []
    seen: set[str] = set()
    for raw_ingredient in cast(list[object], ingredients):
        if not isinstance(raw_ingredient, dict):
            continue
        observation = _observation_from_ingredient(
            cast(dict[object, object], raw_ingredient),
            meal_id=meal_id,
            day_key=day_key,
            observed_at=None,
        )
        if observation is None:
            continue
        source_hash = _hashed_source_ref(observation.source_ref)["sourceHash"]
        if source_hash in seen:
            continue
        seen.add(source_hash)
        source_hashes.append(str(source_hash))
    return source_hashes


def source_hashes_for_review_correction_signals(
    correction_signals: Sequence[Mapping[str, object]],
) -> list[str]:
    source_hashes: list[str] = []
    seen: set[str] = set()
    for payload in correction_signals:
        if _is_source_deleted(payload):
            continue
        signal = _review_correction_signal_from_payload(payload)
        if signal is None:
            continue
        source_hash = _review_correction_source_hash(signal.source_ref)
        if source_hash in seen:
            continue
        seen.add(source_hash)
        source_hashes.append(source_hash)
    return source_hashes


def subject_suppression_keys_for_typical_portion_meal_snapshots(
    meal_snapshots: Sequence[Mapping[str, object]],
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for meal_snapshot in meal_snapshots:
        meal_id = _coerce_str(
            meal_snapshot.get("id")
            or meal_snapshot.get("mealId")
            or meal_snapshot.get("cloudId")
        )
        day_key = _coerce_str(meal_snapshot.get("dayKey"))
        ingredients = meal_snapshot.get("ingredients")
        if meal_id is None or day_key is None or not isinstance(ingredients, list):
            continue
        for raw_ingredient in cast(list[object], ingredients):
            if not isinstance(raw_ingredient, dict):
                continue
            observation = _observation_from_ingredient(
                cast(dict[object, object], raw_ingredient),
                meal_id=meal_id,
                day_key=day_key,
                observed_at=None,
            )
            if observation is None:
                continue
            key = subject_suppression_key(observation.subject_key)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _candidate_request(
    *,
    owner_user_id: str,
    subject: dict[str, Any],
    group: list[TypicalPortionObservation],
    evidence_summary: dict[str, Any],
) -> SmartMemoryCandidateUpsertRequest:
    source_refs = _bounded_source_refs(group)
    candidate_id = _candidate_id(owner_user_id, subject, evidence_summary)
    payload: dict[str, Any] = {
        "clientMutationId": _client_mutation_id(
            candidate_id,
            evidence_summary,
            source_refs,
        ),
        "candidateId": candidate_id,
        "memoryType": "typical_portion",
        "subject": subject,
        "evidenceSummary": evidence_summary,
        "sourceRefs": source_refs,
        "confidenceReasonCodes": ["distinct_days_met"],
        "suppressionChecks": {
            "deletedSuppressed": False,
            "sourceDeleted": False,
            "subjectSuppressionKey": _subject_suppression_key(subject),
        },
        "firstSeenAt": cast(str | None, evidence_summary.get("firstSeenAt")),
        "lastSeenAt": cast(str | None, evidence_summary.get("lastSeenAt")),
    }
    return SmartMemoryCandidateUpsertRequest.model_validate(payload)


def _review_correction_candidate_request(
    *,
    owner_user_id: str,
    subject: dict[str, Any],
    group: list[ReviewCorrectionSignal],
    evidence_summary: dict[str, Any],
) -> SmartMemoryCandidateUpsertRequest:
    source_refs = _bounded_review_correction_source_refs(group)
    candidate_id = _review_correction_candidate_id(
        owner_user_id,
        subject,
        evidence_summary,
    )
    payload: dict[str, Any] = {
        "clientMutationId": _client_mutation_id(
            candidate_id,
            evidence_summary,
            source_refs,
        ),
        "candidateId": candidate_id,
        "memoryType": "review_correction",
        "subject": subject,
        "evidenceSummary": evidence_summary,
        "sourceRefs": source_refs,
        "confidenceReasonCodes": ["distinct_days_met", "consistent_user_review"],
        "suppressionChecks": {
            "deletedSuppressed": False,
            "sourceDeleted": False,
            "subjectSuppressionKey": _review_correction_subject_suppression_key(subject),
        },
        "firstSeenAt": cast(str | None, evidence_summary.get("firstSeenAt")),
        "lastSeenAt": cast(str | None, evidence_summary.get("lastSeenAt")),
    }
    return SmartMemoryCandidateUpsertRequest.model_validate(payload)


def _observation_from_ingredient(
    ingredient: Mapping[object, object],
    *,
    meal_id: str,
    day_key: str,
    observed_at: str | None,
) -> TypicalPortionObservation | None:
    label = _coerce_str(ingredient.get("name"))
    subject_key = _normalize_subject_key(label)
    amount = _coerce_positive_float(ingredient.get("amount"))
    unit = _coerce_unit(ingredient.get("unit"))
    ingredient_id = _coerce_str(ingredient.get("id"))
    if subject_key is None or amount is None or unit is None or ingredient_id is None:
        return None
    return TypicalPortionObservation(
        subject_key=subject_key,
        subject_label=label or subject_key,
        amount=amount,
        unit=unit,
        day_key=day_key,
        source_ref={
            "kind": "meal",
            "mealId": meal_id,
            "ingredientId": ingredient_id,
            "dayKey": day_key,
        },
        observed_at=observed_at,
    )


def _decision(
    *,
    state: SmartMemoryCaptureDecisionState,
    reason_code: SmartMemoryCaptureReasonCode,
    memory_type: SmartMemoryCaptureMemoryType = "typical_portion",
    subject: dict[str, Any] | None = None,
    evidence_summary: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    candidate_request: SmartMemoryCandidateUpsertRequest | None = None,
) -> SmartMemoryCaptureDecision:
    return SmartMemoryCaptureDecision(
        state=state,
        reason_code=reason_code,
        memory_type=memory_type,
        subject=subject or {},
        evidence_summary=evidence_summary or {},
        source_refs=source_refs or [],
        candidate_request=candidate_request,
    )


def _review_correction_signal_from_payload(
    payload: Mapping[str, object],
) -> ReviewCorrectionSignal | None:
    source_signal_id = _coerce_str(
        payload.get("sourceSignalId") or payload.get("signalId") or payload.get("id")
    )
    day_key = _coerce_str(payload.get("dayKey"))
    if source_signal_id is None or day_key is None:
        return None

    subject_key = _normalize_subject_key(
        _coerce_str(payload.get("subjectKey"))
        or _coerce_str(payload.get("normalizedSubjectKey"))
    )
    correction_field = _coerce_review_correction_field(payload.get("correctionField"))
    surface = _coerce_review_correction_surface(payload.get("surface"))
    before = payload.get("before")
    after = payload.get("after")
    if (
        subject_key is None
        or correction_field is None
        or surface is None
        or not isinstance(before, Mapping)
        or not isinstance(after, Mapping)
    ):
        return None

    before_payload = cast(Mapping[object, object], before)
    after_payload = cast(Mapping[object, object], after)
    before_amount = _coerce_positive_float(before_payload.get("amount"))
    before_unit = _coerce_unit(before_payload.get("unit"))
    after_amount = _coerce_positive_float(after_payload.get("amount"))
    after_unit = _coerce_unit(after_payload.get("unit"))
    if (
        before_amount is None
        or before_unit is None
        or after_amount is None
        or after_unit is None
    ):
        return None

    if correction_field == "amount":
        if before_unit != after_unit or before_amount == after_amount:
            return None
    elif before_unit == after_unit:
        return None

    return ReviewCorrectionSignal(
        subject_key=subject_key,
        correction_field=correction_field,
        before_amount=before_amount,
        before_unit=before_unit,
        after_amount=after_amount,
        after_unit=after_unit,
        day_key=day_key,
        source_ref={
            "kind": "review_correction_signal",
            "sourceSignalId": source_signal_id,
            "dayKey": day_key,
            "surface": surface,
            "correctionField": correction_field,
        },
        surface=surface,
        observed_at=_coerce_str(payload.get("observedAt"))
        or _coerce_str(payload.get("updatedAt")),
    )


def _group_by_subject(
    observations: Sequence[TypicalPortionObservation],
) -> dict[str, list[TypicalPortionObservation]]:
    groups: dict[str, list[TypicalPortionObservation]] = {}
    for observation in observations:
        groups.setdefault(observation.subject_key, []).append(observation)
    return groups


def _group_review_corrections_by_subject_and_field(
    signals: Sequence[ReviewCorrectionSignal],
) -> dict[tuple[str, ReviewCorrectionField], list[ReviewCorrectionSignal]]:
    groups: dict[tuple[str, ReviewCorrectionField], list[ReviewCorrectionSignal]] = {}
    for signal in signals:
        groups.setdefault((signal.subject_key, signal.correction_field), []).append(signal)
    return groups


def _windowed_review_correction_signals(
    signals: Sequence[ReviewCorrectionSignal],
    *,
    reference_day_key: str | None,
) -> list[ReviewCorrectionSignal]:
    parsed_signals: list[tuple[date, ReviewCorrectionSignal]] = []
    for signal in signals:
        parsed_day_key = _parse_day_key(signal.day_key)
        if parsed_day_key is None:
            continue
        parsed_signals.append((parsed_day_key, signal))

    if not parsed_signals:
        return []

    if reference_day_key is None:
        reference_day = max(parsed_day for parsed_day, _signal in parsed_signals)
    else:
        reference_day = _parse_day_key(reference_day_key)
        if reference_day is None:
            return []

    start_day = reference_day - timedelta(
        days=REVIEW_CORRECTION_CAPTURE_WINDOW_DAYS - 1
    )
    return sorted(
        [
            signal
            for parsed_day, signal in parsed_signals
            if start_day <= parsed_day <= reference_day
        ],
        key=_review_correction_sort_key,
    )


def _evidence_summary(
    group: list[TypicalPortionObservation],
    *,
    subject: dict[str, Any],
) -> dict[str, Any]:
    ordered = sorted(group, key=_observation_sort_key)
    amounts = [observation.amount for observation in ordered]
    distinct_days = sorted(_distinct_days(ordered))
    first_seen_at = _first_seen_at(ordered)
    last_seen_at = _last_seen_at(ordered)
    proposed_amount = _bounded_amount(median(amounts))
    unit = ordered[0].unit
    return {
        "thresholdVersion": TYPICAL_PORTION_THRESHOLD_VERSION,
        "requiredObservationCount": REQUIRED_PORTION_OBSERVATIONS,
        "requiredDistinctDayCount": REQUIRED_PORTION_DISTINCT_DAYS,
        "eligibleObservationCount": len(ordered),
        "distinctDayCount": len(distinct_days),
        "firstDayKey": distinct_days[0] if distinct_days else None,
        "lastDayKey": distinct_days[-1] if distinct_days else None,
        "firstSeenAt": first_seen_at,
        "lastSeenAt": last_seen_at,
        "subjectHash": subject["aliasHash"],
        "unit": unit,
        "amountCluster": {
            "strategy": "median_with_fixed_tolerance",
            "amount": proposed_amount,
            "unit": unit,
            "absoluteTolerance": AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE,
            "relativeTolerance": AMOUNT_CLUSTER_RELATIVE_TOLERANCE,
        },
        "proposedValue": {"amount": proposed_amount, "unit": unit},
        "sourceRefCount": len(_bounded_source_refs(ordered)),
    }


def _review_correction_evidence_summary(
    group: list[ReviewCorrectionSignal],
    *,
    subject: dict[str, Any],
) -> dict[str, Any]:
    ordered = sorted(group, key=_review_correction_sort_key)
    before_amounts = [signal.before_amount for signal in ordered]
    after_amounts = [signal.after_amount for signal in ordered]
    distinct_days = sorted(_review_correction_distinct_days(ordered))
    first_seen_at = _first_seen_at_for_review_corrections(ordered)
    last_seen_at = _last_seen_at_for_review_corrections(ordered)
    before_amount = _bounded_amount(median(before_amounts))
    proposed_amount = _bounded_amount(median(after_amounts))
    before_unit = ordered[0].before_unit
    unit = ordered[0].after_unit
    correction_field = ordered[0].correction_field
    surfaces = sorted({signal.surface for signal in ordered})
    return {
        "thresholdVersion": REVIEW_CORRECTION_THRESHOLD_VERSION,
        "requiredObservationCount": REQUIRED_PORTION_OBSERVATIONS,
        "requiredDistinctDayCount": REQUIRED_PORTION_DISTINCT_DAYS,
        "eligibleObservationCount": len(ordered),
        "distinctDayCount": len(distinct_days),
        "firstSeenAt": first_seen_at,
        "lastSeenAt": last_seen_at,
        "subjectHash": subject["aliasHash"],
        "correctionField": correction_field,
        "surfaces": surfaces,
        "unit": unit,
        "beforeValueCluster": {
            "strategy": "median_with_fixed_tolerance",
            "amount": before_amount,
            "unit": before_unit,
            "absoluteTolerance": AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE,
            "relativeTolerance": AMOUNT_CLUSTER_RELATIVE_TOLERANCE,
        },
        "afterValueCluster": {
            "strategy": "median_with_fixed_tolerance",
            "amount": proposed_amount,
            "unit": unit,
            "absoluteTolerance": AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE,
            "relativeTolerance": AMOUNT_CLUSTER_RELATIVE_TOLERANCE,
        },
        "proposedValue": {
            "amount": proposed_amount,
            "unit": unit,
            "reasonCode": "user_corrected",
        },
        "sourceRefCount": len(_bounded_review_correction_source_refs(ordered)),
        "reasonCodes": [
            "explicit_review_correction_signal",
            "bounded_correction_fields",
        ],
    }


def _bounded_source_refs(
    observations: Sequence[TypicalPortionObservation],
) -> list[dict[str, Any]]:
    by_day: dict[str, TypicalPortionObservation] = {}
    for observation in sorted(observations, key=_observation_sort_key):
        by_day.setdefault(observation.day_key, observation)
    return [
        _hashed_source_ref(observation.source_ref)
        for observation in sorted(by_day.values(), key=_observation_sort_key)[
            :MAX_CANDIDATE_SOURCE_REFS
        ]
    ]


def _bounded_review_correction_source_refs(
    signals: Sequence[ReviewCorrectionSignal],
) -> list[dict[str, Any]]:
    by_day: dict[str, ReviewCorrectionSignal] = {}
    for signal in sorted(signals, key=_review_correction_sort_key):
        by_day.setdefault(signal.day_key, signal)
    return [
        _hashed_review_correction_source_ref(signal.source_ref)
        for signal in sorted(by_day.values(), key=_review_correction_sort_key)[
            :MAX_CANDIDATE_SOURCE_REFS
        ]
    ]


def _amounts_are_clustered(amounts: Sequence[float]) -> bool:
    if not amounts:
        return False
    center = float(median(amounts))
    tolerance = max(
        AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE,
        abs(center) * AMOUNT_CLUSTER_RELATIVE_TOLERANCE,
    )
    return all(abs(amount - center) <= tolerance for amount in amounts)


def _distinct_days(observations: Sequence[TypicalPortionObservation]) -> set[str]:
    return {observation.day_key for observation in observations}


def _review_correction_distinct_days(
    signals: Sequence[ReviewCorrectionSignal],
) -> set[str]:
    return {signal.day_key for signal in signals}


def _first_seen_at(observations: Sequence[TypicalPortionObservation]) -> str | None:
    values = sorted(
        observed_at
        for observed_at in (observation.observed_at for observation in observations)
        if observed_at
    )
    return values[0] if values else None


def _last_seen_at(observations: Sequence[TypicalPortionObservation]) -> str | None:
    values = sorted(
        observed_at
        for observed_at in (observation.observed_at for observation in observations)
        if observed_at
    )
    return values[-1] if values else None


def _first_seen_at_for_review_corrections(
    signals: Sequence[ReviewCorrectionSignal],
) -> str | None:
    values = sorted(
        observed_at
        for observed_at in (signal.observed_at for signal in signals)
        if observed_at
    )
    return values[0] if values else None


def _last_seen_at_for_review_corrections(
    signals: Sequence[ReviewCorrectionSignal],
) -> str | None:
    values = sorted(
        observed_at
        for observed_at in (signal.observed_at for signal in signals)
        if observed_at
    )
    return values[-1] if values else None


def _candidate_id(
    owner_user_id: str,
    subject: dict[str, Any],
    evidence_summary: dict[str, Any],
) -> str:
    stable_parts: dict[str, Any] = {
        "memoryType": "typical_portion",
        "ownerUserId": owner_user_id,
        "subject": subject,
        "unit": evidence_summary.get("unit"),
    }
    digest = _stable_hash(stable_parts)[:24]
    return f"typical-portion-{digest}"


def _review_correction_candidate_id(
    owner_user_id: str,
    subject: dict[str, Any],
    evidence_summary: dict[str, Any],
) -> str:
    stable_parts: dict[str, Any] = {
        "memoryType": "review_correction",
        "ownerUserId": owner_user_id,
        "subject": subject,
        "correctionField": evidence_summary.get("correctionField"),
        "unit": evidence_summary.get("unit"),
    }
    digest = _stable_hash(stable_parts)[:24]
    return f"review-correction-{digest}"


def _client_mutation_id(
    candidate_id: str,
    evidence_summary: dict[str, Any],
    source_refs: list[dict[str, Any]],
) -> str:
    digest = _stable_hash(
        {
            "candidateId": candidate_id,
            "evidenceSummary": evidence_summary,
            "sourceRefs": source_refs,
        }
    )[:24]
    return f"capture-{digest}"


def _subject_payload(subject_key: str) -> dict[str, Any]:
    return {"kind": "ingredient_alias", "aliasHash": _stable_hash({"key": subject_key})}


def _review_correction_subject_payload(
    subject_key: str,
    correction_field: ReviewCorrectionField,
) -> dict[str, Any]:
    normalized_subject_key = _normalize_subject_key(subject_key) or subject_key
    return {
        "kind": "ingredient_alias",
        "aliasHash": _stable_hash({"key": normalized_subject_key}),
        "correctionField": correction_field,
    }


def _subject_suppression_key(subject: dict[str, Any]) -> str:
    return f"typical_portion:{_stable_hash(subject)}"


def _review_correction_subject_suppression_key(subject: dict[str, Any]) -> str:
    return f"review_correction:{_stable_hash(subject)}"


def _hashed_source_ref(source_ref: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "meal_portion_observation",
        "sourceHash": _stable_hash(
            {
                "kind": source_ref.get("kind"),
                "mealId": source_ref.get("mealId"),
                "ingredientId": source_ref.get("ingredientId"),
                "dayKey": source_ref.get("dayKey"),
            }
        ),
    }


def _hashed_review_correction_source_ref(source_ref: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "review_correction_signal",
        "sourceHash": _review_correction_source_hash(source_ref),
    }


def _review_correction_source_hash(source_ref: Mapping[str, Any]) -> str:
    return _stable_hash(
        {
            "kind": source_ref.get("kind"),
            "sourceSignalId": source_ref.get("sourceSignalId"),
            "dayKey": source_ref.get("dayKey"),
            "surface": source_ref.get("surface"),
            "correctionField": source_ref.get("correctionField"),
        }
    )


def _stable_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_subject_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(
        "".join(character.casefold() if character.isalnum() else " " for character in value).split()
    )
    return normalized or None


def _bounded_amount(value: float) -> int | float:
    rounded = round(value, 1)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _coerce_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_day_key(value: str) -> date | None:
    if len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _coerce_positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    amount = float(value)
    if amount <= 0:
        return None
    return amount


def _coerce_unit(value: object) -> PortionUnit | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized in SUPPORTED_PORTION_UNITS:
        return cast(PortionUnit, normalized)
    return None


def _coerce_review_correction_field(value: object) -> ReviewCorrectionField | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized in SUPPORTED_REVIEW_CORRECTION_FIELDS:
        return cast(ReviewCorrectionField, normalized)
    return None


def _coerce_review_correction_surface(value: object) -> ReviewCorrectionSurface | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized in SUPPORTED_REVIEW_CORRECTION_SURFACES:
        return cast(ReviewCorrectionSurface, normalized)
    return None


def _is_source_deleted(snapshot: Mapping[str, object]) -> bool:
    if snapshot.get("deleted") is True or snapshot.get("sourceDeleted") is True:
        return True
    state = _coerce_str(snapshot.get("state"))
    return state in DELETED_SOURCE_STATES


def _observation_sort_key(
    observation: TypicalPortionObservation,
) -> tuple[str, str, float, str]:
    return (
        observation.subject_key,
        observation.day_key,
        observation.amount,
        _source_ref_key(observation.source_ref),
    )


def _review_correction_sort_key(
    signal: ReviewCorrectionSignal,
) -> tuple[str, str, str, float, str]:
    return (
        signal.subject_key,
        signal.correction_field,
        signal.day_key,
        signal.after_amount,
        _review_correction_source_ref_key(signal.source_ref),
    )


def _source_ref_key(source_ref: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(source_ref.get("kind") or ""),
            str(source_ref.get("mealId") or ""),
            str(source_ref.get("ingredientId") or ""),
            str(source_ref.get("dayKey") or ""),
        ]
    )


def _review_correction_source_ref_key(source_ref: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(source_ref.get("kind") or ""),
            str(source_ref.get("sourceSignalId") or ""),
            str(source_ref.get("dayKey") or ""),
            str(source_ref.get("surface") or ""),
            str(source_ref.get("correctionField") or ""),
        ]
    )
