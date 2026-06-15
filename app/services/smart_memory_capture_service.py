"""Pure Smart Memory capture evaluation helpers."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
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

SUPPORTED_PORTION_UNITS: tuple[PortionUnit, ...] = ("g", "ml", "piece", "serving")
REQUIRED_PORTION_OBSERVATIONS = 3
REQUIRED_PORTION_DISTINCT_DAYS = 3
MAX_CANDIDATE_SOURCE_REFS = 3
AMOUNT_CLUSTER_RELATIVE_TOLERANCE = 0.10
AMOUNT_CLUSTER_ABSOLUTE_TOLERANCE = 5.0
THRESHOLD_VERSION = "typical_portion_v1"
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
class SmartMemoryCaptureDecision:
    state: SmartMemoryCaptureDecisionState
    reason_code: SmartMemoryCaptureReasonCode
    memory_type: Literal["typical_portion"]
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


def subject_suppression_key(subject_key: str) -> str:
    return _subject_suppression_key(_subject_payload(subject_key))


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
    memory_type: Literal["typical_portion"] = "typical_portion",
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


def _group_by_subject(
    observations: Sequence[TypicalPortionObservation],
) -> dict[str, list[TypicalPortionObservation]]:
    groups: dict[str, list[TypicalPortionObservation]] = {}
    for observation in observations:
        groups.setdefault(observation.subject_key, []).append(observation)
    return groups


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
        "thresholdVersion": THRESHOLD_VERSION,
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


def _subject_suppression_key(subject: dict[str, Any]) -> str:
    return f"typical_portion:{_stable_hash(subject)}"


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


def _source_ref_key(source_ref: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(source_ref.get("kind") or ""),
            str(source_ref.get("mealId") or ""),
            str(source_ref.get("ingredientId") or ""),
            str(source_ref.get("dayKey") or ""),
        ]
    )
