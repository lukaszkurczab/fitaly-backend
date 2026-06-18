from __future__ import annotations

from collections.abc import Sequence

from app.schemas.recipes import (
    RecipeCatalogAllergenFlag,
    RecipeCatalogDietaryFlag,
    RecipeCatalogFilterQueryEcho,
    RecipeCatalogFilterReason,
    RecipeCatalogFilterRequest,
    RecipeCatalogFilterResponse,
    RecipeCatalogFilterResult,
    RecipeCatalogNutritionSnapshot,
    RecipeCatalogRecord,
    RecipeCatalogSoftPreferenceStatus,
    RecipeCatalogSourceAttribution,
)
from app.schemas.user_account import AllergyValue, PreferenceValue


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


def _source() -> RecipeCatalogSourceAttribution:
    return RecipeCatalogSourceAttribution(
        sourceType="internal_curated",
        sourceId="recipe-catalog-foundation",
        sourceName="Fitaly curated foundation",
        reviewedAt="2026-06-18T00:00:00.000Z",
    )


def _record(
    *,
    recipe_id: str,
    title: str,
    nutrition: RecipeCatalogNutritionSnapshot,
    dietary_flags: list[RecipeCatalogDietaryFlag],
    allergen_flags: list[RecipeCatalogAllergenFlag] | None = None,
    style_tags: list[str] | None = None,
) -> RecipeCatalogRecord:
    return RecipeCatalogRecord.model_validate(
        {
            "recipeId": recipe_id,
            "version": 1,
            "lifecycleState": "active",
            "locale": "pl-PL",
            "title": title,
            "description": "Curated recipe catalog foundation record.",
            "servings": 2,
            "yield": "2 servings",
            "sourceAttribution": _source().model_dump(mode="json"),
            "updatedAt": "2026-06-18T00:00:00.000Z",
            "reviewState": "curated",
            "ingredients": [
                {
                    "ingredientProductId": None,
                    "snapshotName": "Curated ingredient snapshot",
                    "quantity": 100,
                    "unit": "g",
                }
            ],
            "steps": ["Prepare curated recipe."],
            "prepTimeMin": 10,
            "cookTimeMin": 20,
            "nutritionSnapshot": nutrition.model_dump(mode="json"),
            "imageRef": None,
            "profileFlagState": "complete",
            "dietaryFlags": dietary_flags,
            "allergenFlags": allergen_flags or [],
            "unknownDietaryFlags": [],
            "unknownAllergenFlags": [],
            "styleTags": style_tags or [],
        }
    )


CURATED_RECIPE_CATALOG: tuple[RecipeCatalogRecord, ...] = (
    _record(
        recipe_id="vegan-lentil-bowl",
        title="Vegan lentil bowl",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=520,
            proteinGrams=28,
            fatGrams=14,
            carbsGrams=62,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
    _record(
        recipe_id="salmon-rice-plate",
        title="Salmon rice plate",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=610,
            proteinGrams=34,
            fatGrams=22,
            carbsGrams=58,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["pescatarian", "gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
    _record(
        recipe_id="tofu-vegetable-stir-fry",
        title="Tofu vegetable stir fry",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=430,
            proteinGrams=26,
            fatGrams=17,
            carbsGrams=42,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "dairy_free"],
        style_tags=["balanced"],
    ),
    _record(
        recipe_id="berry-yogurt-bowl",
        title="Berry yogurt bowl",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=360,
            proteinGrams=24,
            fatGrams=9,
            carbsGrams=45,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegetarian", "gluten_free"],
        allergen_flags=["lactose"],
        style_tags=["balanced"],
    ),
    _record(
        recipe_id="peanut-oat-bars",
        title="Peanut oat bars",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=390,
            proteinGrams=14,
            fatGrams=18,
            carbsGrams=48,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "dairy_free"],
        allergen_flags=["peanuts", "gluten"],
    ),
    _record(
        recipe_id="chicken-quinoa-salad",
        title="Chicken quinoa salad",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=560,
            proteinGrams=42,
            fatGrams=16,
            carbsGrams=46,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
)


def evaluate_recipe_catalog(
    request: RecipeCatalogFilterRequest,
    *,
    catalog: Sequence[RecipeCatalogRecord] | None = None,
) -> RecipeCatalogFilterResponse:
    records = list(CURATED_RECIPE_CATALOG if catalog is None else catalog)
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
