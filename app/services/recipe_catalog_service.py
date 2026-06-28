from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas.recipes import (
    RecipeCatalogAllergenFlag,
    RecipeCatalogDietaryFlag,
    RecipeCatalogFilterQueryEcho,
    RecipeCatalogFilterReason,
    RecipeCatalogFilterRequest,
    RecipeCatalogFilterResponse,
    RecipeCatalogFilterResult,
    RecipeCatalogRecord,
    RecipeCatalogSoftPreferenceStatus,
)
from app.schemas.user_account import AllergyValue, PreferenceValue


@dataclass(frozen=True, slots=True)
class RecipeCatalogCoverageCase:
    case_id: str
    name: str
    request: RecipeCatalogFilterRequest
    catalog: Sequence[RecipeCatalogRecord] | None = None
    expected_empty_catalog: bool = False
    expected_unknown_reveal: bool = False


@dataclass(frozen=True, slots=True)
class RecipeCatalogCoverageResult:
    case_id: str
    name: str
    request: RecipeCatalogFilterRequest
    query_echo: RecipeCatalogFilterQueryEcho
    total_catalog_count: int
    returned_item_count: int
    visible_count: int
    hidden_hard_exclusion_count: int
    unknown_reveal_required_count: int
    revealed_unknown_count: int
    threshold: int
    low_results: bool
    empty_catalog: bool
    low_results_state_required: bool
    passes_coverage_gate: bool


ALLERGY_FLAG_BY_PROFILE_VALUE: dict[str, RecipeCatalogAllergenFlag] = {
    "peanuts": "peanuts",
    "gluten": "gluten",
    "lactose": "lactose",
}
RESTRICTION_FLAG_BY_PROFILE_VALUE: dict[str, RecipeCatalogDietaryFlag] = {
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "pescatarian": "pescatarian",
    "glutenFree": "gluten_free",
    "dairyFree": "dairy_free",
}
SOFT_PREFERENCES: set[PreferenceValue] = {
    "lowCarb",
    "keto",
    "highProtein",
    "highCarb",
    "lowFat",
    "balanced",
    "mediterranean",
    "paleo",
}


def evaluate_default_recipe_catalog_coverage(
    cases: Sequence[RecipeCatalogCoverageCase] | None = None,
) -> tuple[RecipeCatalogCoverageResult, ...]:
    coverage_cases = list(_default_coverage_cases() if cases is None else cases)
    return tuple(_evaluate_coverage_case(case) for case in coverage_cases)


def evaluate_recipe_catalog(
    request: RecipeCatalogFilterRequest,
    *,
    catalog: Sequence[RecipeCatalogRecord] | None = None,
) -> RecipeCatalogFilterResponse:
    records = list(catalog if catalog is not None else ())
    active_allergies = _active_allergies(request.allergies)
    active_restrictions = _active_restrictions(request.preferences)
    active_soft_preferences = _active_soft_preferences(request.preferences)

    evaluated = [
        _evaluate_record(
            record,
            active_allergies=active_allergies,
            active_restrictions=active_restrictions,
            active_soft_preferences=active_soft_preferences,
        )
        for record in records
        if record.lifecycleState == "active"
    ]
    evaluated.sort(key=_result_sort_key)

    items = [
        result
        for result in evaluated
        if result.status == "visible"
        or (result.status == "hidden_hard_exclusion" and request.showHidden)
        or (result.status == "unknown_reveal_required" and request.revealUnknown)
    ]
    visible_count = sum(1 for result in evaluated if result.status == "visible")
    hidden_count = sum(
        1 for result in evaluated if result.status == "hidden_hard_exclusion"
    )
    unknown_count = sum(
        1 for result in evaluated if result.status == "unknown_reveal_required"
    )
    threshold = _low_results_threshold(
        active_allergies=active_allergies,
        active_restrictions=active_restrictions,
        active_soft_preferences=active_soft_preferences,
        catalog_count=len(records),
    )

    return RecipeCatalogFilterResponse(
        items=items,
        queryEcho=RecipeCatalogFilterQueryEcho(
            activeAllergies=active_allergies,
            activeRestrictions=active_restrictions,
            activeSoftPreferences=active_soft_preferences,
            ignoredChronicDiseases=request.chronicDiseases,
            ignoredAllergiesOtherPresent=bool((request.allergiesOther or "").strip()),
            ignoredLifestylePresent=bool((request.lifestyle or "").strip()),
            showHidden=request.showHidden,
            revealUnknown=request.revealUnknown,
            lowResultsThreshold=threshold,
        ),
        totalCatalogCount=len(records),
        visibleCount=visible_count,
        hiddenHardExclusionCount=hidden_count,
        unknownRevealRequiredCount=unknown_count,
        lowResults=bool(threshold and visible_count < threshold),
        emptyCatalog=len(records) == 0,
    )


def _evaluate_coverage_case(
    case: RecipeCatalogCoverageCase,
) -> RecipeCatalogCoverageResult:
    response = evaluate_recipe_catalog(case.request, catalog=case.catalog)
    revealed_unknown_count = sum(
        1 for result in response.items if result.status == "unknown_reveal_required"
    )
    return RecipeCatalogCoverageResult(
        case_id=case.case_id,
        name=case.name,
        request=case.request,
        query_echo=response.queryEcho,
        total_catalog_count=response.totalCatalogCount,
        returned_item_count=len(response.items),
        visible_count=response.visibleCount,
        hidden_hard_exclusion_count=response.hiddenHardExclusionCount,
        unknown_reveal_required_count=response.unknownRevealRequiredCount,
        revealed_unknown_count=revealed_unknown_count,
        threshold=response.queryEcho.lowResultsThreshold,
        low_results=response.lowResults,
        empty_catalog=response.emptyCatalog,
        low_results_state_required=response.lowResults and not response.emptyCatalog,
        passes_coverage_gate=_passes_coverage_gate(
            case=case,
            response=response,
            revealed_unknown_count=revealed_unknown_count,
        ),
    )


def _passes_coverage_gate(
    *,
    case: RecipeCatalogCoverageCase,
    response: RecipeCatalogFilterResponse,
    revealed_unknown_count: int,
) -> bool:
    if case.expected_empty_catalog:
        return response.emptyCatalog and not response.lowResults

    if case.expected_unknown_reveal and (
        not response.queryEcho.revealUnknown
        or response.unknownRevealRequiredCount <= 0
        or revealed_unknown_count <= 0
    ):
        return False

    if response.emptyCatalog:
        return False

    has_active_filters = bool(
        response.queryEcho.activeAllergies
        or response.queryEcho.activeRestrictions
        or response.queryEcho.activeSoftPreferences
    )
    if not has_active_filters:
        return response.visibleCount > 0

    if response.visibleCount <= 0:
        return False

    return (
        response.visibleCount >= response.queryEcho.lowResultsThreshold
        or response.lowResults
    )


def _default_coverage_cases() -> tuple[RecipeCatalogCoverageCase, ...]:
    return (
        RecipeCatalogCoverageCase(
            case_id="empty-catalog",
            name="Default empty catalog state",
            request=_coverage_request(),
            catalog=(),
            expected_empty_catalog=True,
        ),
    )


def _coverage_request(**values: object) -> RecipeCatalogFilterRequest:
    return RecipeCatalogFilterRequest.model_validate(values)


def _active_allergies(values: list[AllergyValue]) -> list[AllergyValue]:
    return [
        value
        for value in values
        if value in ALLERGY_FLAG_BY_PROFILE_VALUE
    ]


def _active_restrictions(values: list[PreferenceValue]) -> list[PreferenceValue]:
    return [
        value
        for value in values
        if value in RESTRICTION_FLAG_BY_PROFILE_VALUE
    ]


def _active_soft_preferences(values: list[PreferenceValue]) -> list[PreferenceValue]:
    return [value for value in values if value in SOFT_PREFERENCES]


def _evaluate_record(
    record: RecipeCatalogRecord,
    *,
    active_allergies: list[AllergyValue],
    active_restrictions: list[PreferenceValue],
    active_soft_preferences: list[PreferenceValue],
) -> RecipeCatalogFilterResult:
    hard_reasons: list[RecipeCatalogFilterReason] = []
    unknown_reasons: list[RecipeCatalogFilterReason] = []

    for allergy in active_allergies:
        allergy_flag = ALLERGY_FLAG_BY_PROFILE_VALUE[allergy]
        if allergy_flag in record.allergenFlags:
            hard_reasons.append(
                RecipeCatalogFilterReason(
                    code="explicit_allergen_match",
                    filterType="allergy",
                    profileValue=allergy,
                    catalogFlag=allergy_flag,
                )
            )
        elif (
            allergy_flag in record.unknownAllergenFlags
            or record.profileFlagState != "complete"
        ):
            unknown_reasons.append(
                RecipeCatalogFilterReason(
                    code="unknown_allergen_flag",
                    filterType="allergy",
                    profileValue=allergy,
                    catalogFlag=allergy_flag,
                )
            )

    for restriction in active_restrictions:
        restriction_flag = RESTRICTION_FLAG_BY_PROFILE_VALUE[restriction]
        if restriction_flag in record.dietaryFlags:
            continue
        if (
            restriction_flag in record.unknownDietaryFlags
            or record.profileFlagState != "complete"
        ):
            unknown_reasons.append(
                RecipeCatalogFilterReason(
                    code="unknown_restriction_flag",
                    filterType="restriction",
                    profileValue=restriction,
                    catalogFlag=restriction_flag,
                )
            )
            continue
        hard_reasons.append(
            RecipeCatalogFilterReason(
                code="explicit_restriction_mismatch",
                filterType="restriction",
                profileValue=restriction,
                catalogFlag=restriction_flag,
            )
        )

    soft_matches, soft_misses = _evaluate_soft_preferences(
        record,
        active_soft_preferences=active_soft_preferences,
    )
    if hard_reasons:
        status = "hidden_hard_exclusion"
    elif unknown_reasons:
        status = "unknown_reveal_required"
    else:
        status = "visible"

    return RecipeCatalogFilterResult(
        recipe=record,
        status=status,
        hardExclusionReasons=hard_reasons,
        unknownReasons=unknown_reasons,
        softPreferenceStatus=_soft_preference_status(
            matches=soft_matches,
            misses=soft_misses,
        ),
        softPreferenceMatches=soft_matches,
        softPreferenceMisses=soft_misses,
        softPreferenceScore=len(soft_matches),
    )


def _evaluate_soft_preferences(
    record: RecipeCatalogRecord,
    *,
    active_soft_preferences: list[PreferenceValue],
) -> tuple[list[PreferenceValue], list[PreferenceValue]]:
    matches: list[PreferenceValue] = []
    misses: list[PreferenceValue] = []
    for preference in active_soft_preferences:
        if _soft_preference_matches(record, preference):
            matches.append(preference)
        else:
            misses.append(preference)
    return matches, misses


def _soft_preference_matches(
    record: RecipeCatalogRecord,
    preference: PreferenceValue,
) -> bool:
    nutrition = record.nutritionSnapshot
    if nutrition.isPartial or nutrition.confidence in {"unknown", "low"}:
        return False
    if preference == "lowCarb":
        return nutrition.carbsGrams <= 35
    if preference == "keto":
        return nutrition.carbsGrams <= 20
    if preference == "highProtein":
        return nutrition.proteinGrams >= 25
    if preference == "highCarb":
        return nutrition.carbsGrams >= 60
    if preference == "lowFat":
        return nutrition.fatGrams <= 12
    if preference == "balanced":
        return (
            350 <= nutrition.kcal <= 650
            and nutrition.proteinGrams >= 15
            and nutrition.carbsGrams >= 30
            and nutrition.fatGrams <= 25
        )
    if preference == "mediterranean":
        return "mediterranean" in record.styleTags
    if preference == "paleo":
        return "paleo" in record.styleTags
    return False


def _soft_preference_status(
    *,
    matches: list[PreferenceValue],
    misses: list[PreferenceValue],
) -> RecipeCatalogSoftPreferenceStatus:
    if not matches and not misses:
        return "not_applicable"
    if matches and misses:
        return "mixed"
    if matches:
        return "match"
    return "miss"


def _result_sort_key(result: RecipeCatalogFilterResult) -> tuple[int, int, str, str]:
    status_rank = {
        "visible": 0,
        "unknown_reveal_required": 1,
        "hidden_hard_exclusion": 2,
    }[result.status]
    return (
        status_rank,
        -result.softPreferenceScore,
        result.recipe.title.lower(),
        result.recipe.recipeId,
    )


def _low_results_threshold(
    *,
    active_allergies: list[AllergyValue],
    active_restrictions: list[PreferenceValue],
    active_soft_preferences: list[PreferenceValue],
    catalog_count: int,
) -> int:
    if catalog_count == 0:
        return 0
    active_filter_count = (
        len(active_allergies)
        + len(active_restrictions)
        + len(active_soft_preferences)
    )
    if active_filter_count == 0:
        return 0
    if active_filter_count >= 2:
        return 3
    return 6
