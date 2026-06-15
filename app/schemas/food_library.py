from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FoodLibraryDomain = Literal[
    "MealTemplate",
    "Recipe",
    "Ingredient/Product",
    "ShoppingList",
]
FoodLibraryDomainOwner = Literal[
    "meal_template_library",
    "recipe_library",
    "ingredient_product_library",
    "shopping_list_library",
]
FoodLibraryDomainField = Literal[
    "templateId",
    "ownerUserId",
    "templateVersion",
    "displayName",
    "description",
    "mealTypeHint",
    "draftItems",
    "draftTotals",
    "nutritionSnapshot",
    "imageRef",
    "recipeId",
    "recipeVersion",
    "title",
    "ingredients",
    "steps",
    "instructions",
    "servings",
    "yield",
    "prepTimeMin",
    "cookTimeMin",
    "ingredientProductId",
    "recordScope",
    "lifecycleState",
    "kind",
    "brandName",
    "ingredientName",
    "packageName",
    "category",
    "barcodeIdentities",
    "externalSourceIds",
    "servingSizes",
    "nutritionPer100",
    "defaultServing",
    "sourceAttribution",
    "confidence",
    "profileFlags",
    "dietaryFlags",
    "allergenFlags",
    "createdAt",
    "updatedAt",
    "listId",
    "items",
    "itemRefs",
    "checkedState",
    "sortOrder",
    "notes",
]
FoodLibraryForbiddenLoggedMealField = Literal[
    "mealTemplateId",
    "templateId",
    "templateVersion",
    "recipeId",
    "recipeInstructions",
    "recipeSteps",
    "recipeYield",
    "recipeServings",
    "productId",
    "productBarcode",
    "productCatalogId",
    "productLifecycleState",
    "shoppingListId",
    "shoppingListItems",
    "shoppingListCheckedAt",
    "shoppingListLifecycleState",
]
CurrentSavedMealLegacyMarker = Literal[
    "myMeals",
    "saved_meals",
    "source:saved",
    "inputMethod:saved",
    "savedMealRefId",
]
BarcodeResultOwner = Literal["backend_provider_adapter", "add_meal_draft_source"]
IngredientProductKind = Literal["generic_ingredient", "branded_product"]
IngredientProductRecordScope = Literal[
    "global_seed",
    "global_internal",
    "user_scoped",
]
IngredientProductLifecycleState = Literal["candidate", "verified", "rejected"]
IngredientProductSourceType = Literal[
    "internal_seed",
    "internal_review",
    "user_created",
    "external_provider",
    "barcode_identity",
    "runtime_ai_candidate",
]
IngredientProductConfidenceLevel = Literal[
    "unknown",
    "low",
    "medium",
    "high",
    "verified",
]
IngredientProductNutritionBasis = Literal["per_100g", "per_100ml"]
IngredientProductServingUnit = Literal["g", "ml", "piece", "serving"]
IngredientProductProfileCompatibilityStatus = Literal[
    "unknown",
    "compatible",
    "incompatible",
    "warning",
]
IngredientProductDietaryFlag = Literal[
    "vegan",
    "vegetarian",
    "gluten_free",
    "lactose_free",
    "halal",
    "kosher",
]
IngredientProductAllergenFlag = Literal[
    "milk",
    "eggs",
    "fish",
    "shellfish",
    "tree_nuts",
    "peanuts",
    "wheat",
    "soy",
    "sesame",
]
IngredientProductSourceAttributionRequiredField = Literal[
    "sourceType",
    "sourceId",
    "sourceName",
]
IngredientProductSourceAttributionOptionalField = Literal[
    "provider",
    "license",
    "observedAt",
    "reviewedAt",
    "reviewedBy",
]
IngredientProductConfidenceField = Literal["identity", "nutrition", "profile"]
IngredientProductNutritionRequiredField = Literal[
    "basis",
    "unit",
    "kcal",
    "protein",
    "fat",
    "carbs",
]
IngredientProductNutritionOptionalField = Literal[
    "fiber",
    "sugar",
    "salt",
    "saturatedFat",
]
IngredientProductServingRequiredField = Literal["defaultServing", "servingSizes"]
IngredientProductServingSizeField = Literal[
    "servingSizeId",
    "label",
    "quantity",
    "unit",
]
IngredientProductBarcodeMinimalIdentityField = Literal[
    "barcode",
    "format",
    "sourceType",
]
IngredientProductBarcodeOptionalField = Literal[
    "normalizedBarcode",
    "country",
    "sourceId",
    "observedAt",
]

FOOD_LIBRARY_DOMAINS: tuple[FoodLibraryDomain, ...] = (
    "MealTemplate",
    "Recipe",
    "Ingredient/Product",
    "ShoppingList",
)
FOOD_LIBRARY_LOGGED_MEAL_OWNER = "meal_logging"
FOOD_LIBRARY_LOGGED_MEAL_SCHEMA = "Meal"
FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS: tuple[
    FoodLibraryForbiddenLoggedMealField,
    ...,
] = (
    "mealTemplateId",
    "templateId",
    "templateVersion",
    "recipeId",
    "recipeInstructions",
    "recipeSteps",
    "recipeYield",
    "recipeServings",
    "productId",
    "productBarcode",
    "productCatalogId",
    "productLifecycleState",
    "shoppingListId",
    "shoppingListItems",
    "shoppingListCheckedAt",
    "shoppingListLifecycleState",
)
FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES: tuple[Literal["myMeals", "saved_meals"], ...] = (
    "myMeals",
    "saved_meals",
)
FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL: tuple[
    CurrentSavedMealLegacyMarker,
    ...,
] = (
    "myMeals",
    "saved_meals",
    "source:saved",
    "inputMethod:saved",
    "savedMealRefId",
)
FOOD_LIBRARY_BARCODE_RESULT_OWNERS: tuple[BarcodeResultOwner, ...] = (
    "backend_provider_adapter",
    "add_meal_draft_source",
)
INGREDIENT_PRODUCT_KINDS: tuple[IngredientProductKind, ...] = (
    "generic_ingredient",
    "branded_product",
)
INGREDIENT_PRODUCT_RECORD_SCOPES: tuple[IngredientProductRecordScope, ...] = (
    "global_seed",
    "global_internal",
    "user_scoped",
)
INGREDIENT_PRODUCT_LIFECYCLE_STATES: tuple[IngredientProductLifecycleState, ...] = (
    "candidate",
    "verified",
    "rejected",
)
INGREDIENT_PRODUCT_SOURCE_TYPES: tuple[IngredientProductSourceType, ...] = (
    "internal_seed",
    "internal_review",
    "user_created",
    "external_provider",
    "barcode_identity",
    "runtime_ai_candidate",
)
INGREDIENT_PRODUCT_CONFIDENCE_LEVELS: tuple[IngredientProductConfidenceLevel, ...] = (
    "unknown",
    "low",
    "medium",
    "high",
    "verified",
)
INGREDIENT_PRODUCT_NUTRITION_BASES: tuple[IngredientProductNutritionBasis, ...] = (
    "per_100g",
    "per_100ml",
)
INGREDIENT_PRODUCT_SERVING_UNITS: tuple[IngredientProductServingUnit, ...] = (
    "g",
    "ml",
    "piece",
    "serving",
)
INGREDIENT_PRODUCT_PROFILE_COMPATIBILITY_STATUSES: tuple[
    IngredientProductProfileCompatibilityStatus,
    ...,
] = (
    "unknown",
    "compatible",
    "incompatible",
    "warning",
)
INGREDIENT_PRODUCT_DIETARY_FLAGS: tuple[IngredientProductDietaryFlag, ...] = (
    "vegan",
    "vegetarian",
    "gluten_free",
    "lactose_free",
    "halal",
    "kosher",
)
INGREDIENT_PRODUCT_ALLERGEN_FLAGS: tuple[IngredientProductAllergenFlag, ...] = (
    "milk",
    "eggs",
    "fish",
    "shellfish",
    "tree_nuts",
    "peanuts",
    "wheat",
    "soy",
    "sesame",
)
INGREDIENT_PRODUCT_REQUIRED_FIELDS: tuple[FoodLibraryDomainField, ...] = (
    "ingredientProductId",
    "recordScope",
    "lifecycleState",
    "kind",
    "displayName",
    "sourceAttribution",
    "confidence",
    "nutritionPer100",
    "defaultServing",
    "servingSizes",
    "profileFlags",
    "createdAt",
    "updatedAt",
)
INGREDIENT_PRODUCT_OPTIONAL_FIELDS: tuple[FoodLibraryDomainField, ...] = (
    "ownerUserId",
    "brandName",
    "ingredientName",
    "packageName",
    "category",
    "barcodeIdentities",
    "externalSourceIds",
    "dietaryFlags",
    "allergenFlags",
)
INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_REQUIRED_FIELDS: tuple[
    IngredientProductSourceAttributionRequiredField,
    ...,
] = ("sourceType", "sourceId", "sourceName")
INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_OPTIONAL_FIELDS: tuple[
    IngredientProductSourceAttributionOptionalField,
    ...,
] = ("provider", "license", "observedAt", "reviewedAt", "reviewedBy")
INGREDIENT_PRODUCT_CONFIDENCE_FIELDS: tuple[
    IngredientProductConfidenceField,
    ...,
] = ("identity", "nutrition", "profile")
INGREDIENT_PRODUCT_NUTRITION_REQUIRED_FIELDS: tuple[
    IngredientProductNutritionRequiredField,
    ...,
] = ("basis", "unit", "kcal", "protein", "fat", "carbs")
INGREDIENT_PRODUCT_NUTRITION_OPTIONAL_FIELDS: tuple[
    IngredientProductNutritionOptionalField,
    ...,
] = ("fiber", "sugar", "salt", "saturatedFat")
INGREDIENT_PRODUCT_SERVING_REQUIRED_FIELDS: tuple[
    IngredientProductServingRequiredField,
    ...,
] = ("defaultServing", "servingSizes")
INGREDIENT_PRODUCT_SERVING_SIZE_FIELDS: tuple[
    IngredientProductServingSizeField,
    ...,
] = ("servingSizeId", "label", "quantity", "unit")
INGREDIENT_PRODUCT_BARCODE_MINIMAL_IDENTITY_FIELDS: tuple[
    IngredientProductBarcodeMinimalIdentityField,
    ...,
] = ("barcode", "format", "sourceType")
INGREDIENT_PRODUCT_BARCODE_OPTIONAL_FIELDS: tuple[
    IngredientProductBarcodeOptionalField,
    ...,
] = ("normalizedBarcode", "country", "sourceId", "observedAt")
FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS = {
    "loggedAt",
    "dayKey",
    "loggedAtLocalMin",
    "tzOffsetMin",
    "syncState",
    "source",
    "inputMethod",
    "savedMealRefId",
}
FOOD_LIBRARY_DOMAIN_CONTRACTS: dict[
    FoodLibraryDomain,
    tuple[
        FoodLibraryDomainOwner,
        tuple[FoodLibraryDomainField, ...],
        tuple[FoodLibraryDomainField, ...],
    ],
] = {
    "MealTemplate": (
        "meal_template_library",
        ("templateId", "ownerUserId", "templateVersion"),
        (
            "displayName",
            "description",
            "mealTypeHint",
            "draftItems",
            "draftTotals",
            "nutritionSnapshot",
            "imageRef",
        ),
    ),
    "Recipe": (
        "recipe_library",
        ("recipeId", "ownerUserId", "recipeVersion"),
        (
            "title",
            "description",
            "ingredients",
            "steps",
            "instructions",
            "servings",
            "yield",
            "prepTimeMin",
            "cookTimeMin",
            "nutritionSnapshot",
            "imageRef",
        ),
    ),
    "Ingredient/Product": (
        "ingredient_product_library",
        ("ingredientProductId", "recordScope"),
        (
            "displayName",
            "kind",
            "lifecycleState",
            "ownerUserId",
            "brandName",
            "ingredientName",
            "packageName",
            "category",
            "barcodeIdentities",
            "externalSourceIds",
            "servingSizes",
            "nutritionPer100",
            "defaultServing",
            "sourceAttribution",
            "confidence",
            "profileFlags",
            "dietaryFlags",
            "allergenFlags",
            "createdAt",
            "updatedAt",
        ),
    ),
    "ShoppingList": (
        "shopping_list_library",
        ("listId", "ownerUserId"),
        ("title", "items", "itemRefs", "checkedState", "sortOrder", "notes"),
    ),
}


class LoggedMealBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["meal_logging"]
    schemaName: Literal["Meal"]
    mustRemainNarrow: Literal[True]
    mustNotServeAsLibraryCatchAll: Literal[True]
    mustNotGainFields: list[FoodLibraryForbiddenLoggedMealField] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class CurrentSavedMealsBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currentNames: list[Literal["myMeals", "saved_meals"]]
    isFinalLibraryFoundation: Literal[False]
    laterTargetDomain: Literal["MealTemplate"]
    compatibilityFallbackToOldShapeAccepted: Literal[False]
    legacyMarkersNotCanonicalLibraryFoundation: list[CurrentSavedMealLegacyMarker]
    mustNotExpandWith: list[FoodLibraryForbiddenLoggedMealField] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class BarcodeBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resultOwnership: list[BarcodeResultOwner]
    addMealDraftSourceOnly: Literal[True]
    createsFirstPartyProductCatalogInThisSlice: Literal[False]
    mustNotWriteLibraryDomains: list[Literal["Ingredient/Product"]]
    rationale: str = Field(min_length=1)


class FoodLibraryDomainContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: FoodLibraryDomainOwner
    identityFields: list[FoodLibraryDomainField] = Field(min_length=1)
    ownedFields: list[FoodLibraryDomainField] = Field(min_length=1)


class KindSpecificRequiredFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generic_ingredient: list[FoodLibraryDomainField]
    branded_product: list[FoodLibraryDomainField]


class IngredientProductOwnershipContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopeField: Literal["recordScope"]
    ownerField: Literal["ownerUserId"]
    userScopedScope: Literal["user_scoped"]
    userScopedRequiresOwnerUserId: Literal[True]
    globalScopesMustNotUseOwnerUserId: list[
        Literal["global_seed", "global_internal"]
    ] = Field(min_length=1)
    globalRecordsAreUserAccountData: Literal[False]


class IngredientProductSourceAttributionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFields: list[IngredientProductSourceAttributionRequiredField] = Field(
        min_length=1
    )
    optionalFields: list[IngredientProductSourceAttributionOptionalField]
    sourceTypes: list[IngredientProductSourceType] = Field(min_length=1)
    candidateOnlySourceTypes: list[IngredientProductSourceType]
    durableTruthRequiresNonAiSource: Literal[True]


class IngredientProductConfidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFields: list[IngredientProductConfidenceField] = Field(min_length=1)
    levels: list[IngredientProductConfidenceLevel] = Field(min_length=1)
    unknownMeansNotSafeToAssume: Literal[True]


class IngredientProductNutritionPer100Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFields: list[IngredientProductNutritionRequiredField] = Field(min_length=1)
    optionalFields: list[IngredientProductNutritionOptionalField]
    allowedBases: list[IngredientProductNutritionBasis] = Field(min_length=1)
    missingNutritionPolicy: Literal["unknown_not_guessed"]
    runtimeAiMayBecomeDurableNutritionTruth: Literal[False]


class IngredientProductServingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFields: list[IngredientProductServingRequiredField] = Field(min_length=1)
    servingSizeFields: list[IngredientProductServingSizeField] = Field(min_length=1)
    allowedUnits: list[IngredientProductServingUnit] = Field(min_length=1)


class IngredientProductProfileContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredFields: list[str] = Field(min_length=1)
    allowedDietaryFlags: list[IngredientProductDietaryFlag]
    allowedAllergenFlags: list[IngredientProductAllergenFlag]
    compatibilityStatuses: list[IngredientProductProfileCompatibilityStatus]
    missingProfilePolicy: Literal["unknown_not_guessed"]
    verifiedIsMedicalOrDietarySafetyClaim: Literal[False]
    runtimeAiMayBecomeDurableProfileTruth: Literal[False]


class IngredientProductBarcodeIdentityContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimalIdentityFields: list[IngredientProductBarcodeMinimalIdentityField] = Field(
        min_length=1
    )
    optionalFields: list[IngredientProductBarcodeOptionalField]
    noCatalogWriteInThisSlice: Literal[True]
    noTopLevelAddMealBarcodePath: Literal[True]


class IngredientProductLocalCacheBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    representedAs: Literal["projection_only"]
    localCacheIsTruth: Literal[False]
    mayPromoteToGlobalWithoutReview: Literal[False]


class IngredientProductRecordContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordKinds: list[IngredientProductKind] = Field(min_length=1)
    recordScopes: list[IngredientProductRecordScope] = Field(min_length=1)
    lifecycleStates: list[IngredientProductLifecycleState] = Field(min_length=1)
    verifiedMeaning: Literal[
        "verified_for_fitaly_catalog_use_not_medical_or_dietary_safety_claim"
    ]
    requiredFields: list[FoodLibraryDomainField] = Field(min_length=1)
    optionalFields: list[FoodLibraryDomainField]
    kindSpecificRequiredFields: KindSpecificRequiredFields
    ownership: IngredientProductOwnershipContract
    sourceAttribution: IngredientProductSourceAttributionContract
    confidence: IngredientProductConfidenceContract
    nutritionPer100: IngredientProductNutritionPer100Contract
    serving: IngredientProductServingContract
    profileFlags: IngredientProductProfileContract
    barcodeIdentities: IngredientProductBarcodeIdentityContract
    localCacheBoundary: IngredientProductLocalCacheBoundary


class FoodLibraryDomainsContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["food_library_domains_v1"]
    libraryDomains: list[FoodLibraryDomain]
    domainContracts: dict[FoodLibraryDomain, FoodLibraryDomainContract]
    ingredientProductRecordContract: IngredientProductRecordContract
    loggedMealBoundary: LoggedMealBoundary
    currentSavedMealsBoundary: CurrentSavedMealsBoundary
    barcodeBoundary: BarcodeBoundary

    @model_validator(mode="after")
    def _validate_exact_contract(self) -> "FoodLibraryDomainsContract":
        if tuple(self.libraryDomains) != FOOD_LIBRARY_DOMAINS:
            raise ValueError("libraryDomains must match canonical CH-06 domains")
        if tuple(self.domainContracts.keys()) != FOOD_LIBRARY_DOMAINS:
            raise ValueError("domainContracts must declare each CH-06 domain exactly once")
        for domain, expected in FOOD_LIBRARY_DOMAIN_CONTRACTS.items():
            expected_owner, expected_identity_fields, expected_owned_fields = expected
            domain_contract = self.domainContracts[domain]
            if domain_contract.owner != expected_owner:
                raise ValueError(f"{domain} owner drifted from CH-06 contract")
            if tuple(domain_contract.identityFields) != expected_identity_fields:
                raise ValueError(f"{domain} identityFields drifted from CH-06 contract")
            if tuple(domain_contract.ownedFields) != expected_owned_fields:
                raise ValueError(f"{domain} ownedFields drifted from CH-06 contract")
        ingredient_product_contract = self.ingredientProductRecordContract
        if tuple(ingredient_product_contract.recordKinds) != INGREDIENT_PRODUCT_KINDS:
            raise ValueError("Ingredient/Product kinds drifted from 02-FD-002 contract")
        if (
            tuple(ingredient_product_contract.recordScopes)
            != INGREDIENT_PRODUCT_RECORD_SCOPES
        ):
            raise ValueError("Ingredient/Product scopes drifted from 02-FD-002 contract")
        if (
            tuple(ingredient_product_contract.lifecycleStates)
            != INGREDIENT_PRODUCT_LIFECYCLE_STATES
        ):
            raise ValueError(
                "Ingredient/Product lifecycle states drifted from 02-FD-002 contract"
            )
        if (
            tuple(ingredient_product_contract.requiredFields)
            != INGREDIENT_PRODUCT_REQUIRED_FIELDS
        ):
            raise ValueError(
                "Ingredient/Product required fields drifted from 02-FD-002 contract"
            )
        if (
            tuple(ingredient_product_contract.optionalFields)
            != INGREDIENT_PRODUCT_OPTIONAL_FIELDS
        ):
            raise ValueError(
                "Ingredient/Product optional fields drifted from 02-FD-002 contract"
            )
        if ingredient_product_contract.kindSpecificRequiredFields.generic_ingredient != [
            "ingredientName"
        ]:
            raise ValueError("generic ingredients must require ingredientName")
        if ingredient_product_contract.kindSpecificRequiredFields.branded_product != [
            "brandName"
        ]:
            raise ValueError("branded products must require brandName")
        if ingredient_product_contract.ownership.globalScopesMustNotUseOwnerUserId != [
            "global_seed",
            "global_internal",
        ]:
            raise ValueError("global Product/Ingredient records must not be user-owned")
        if tuple(ingredient_product_contract.sourceAttribution.sourceTypes) != (
            INGREDIENT_PRODUCT_SOURCE_TYPES
        ):
            raise ValueError("Ingredient/Product source types drifted")
        if (
            tuple(ingredient_product_contract.sourceAttribution.requiredFields)
            != INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_REQUIRED_FIELDS
        ):
            raise ValueError("Ingredient/Product source required fields drifted")
        if (
            tuple(ingredient_product_contract.sourceAttribution.optionalFields)
            != INGREDIENT_PRODUCT_SOURCE_ATTRIBUTION_OPTIONAL_FIELDS
        ):
            raise ValueError("Ingredient/Product source optional fields drifted")
        if ingredient_product_contract.sourceAttribution.candidateOnlySourceTypes != [
            "barcode_identity",
            "runtime_ai_candidate",
        ]:
            raise ValueError("barcode and runtime AI sources must stay candidate-only")
        if (
            tuple(ingredient_product_contract.confidence.requiredFields)
            != INGREDIENT_PRODUCT_CONFIDENCE_FIELDS
        ):
            raise ValueError("Ingredient/Product confidence fields drifted")
        if tuple(ingredient_product_contract.confidence.levels) != (
            INGREDIENT_PRODUCT_CONFIDENCE_LEVELS
        ):
            raise ValueError("Ingredient/Product confidence levels drifted")
        if (
            tuple(ingredient_product_contract.nutritionPer100.requiredFields)
            != INGREDIENT_PRODUCT_NUTRITION_REQUIRED_FIELDS
        ):
            raise ValueError("Ingredient/Product nutrition required fields drifted")
        if (
            tuple(ingredient_product_contract.nutritionPer100.optionalFields)
            != INGREDIENT_PRODUCT_NUTRITION_OPTIONAL_FIELDS
        ):
            raise ValueError("Ingredient/Product nutrition optional fields drifted")
        if tuple(ingredient_product_contract.nutritionPer100.allowedBases) != (
            INGREDIENT_PRODUCT_NUTRITION_BASES
        ):
            raise ValueError("Ingredient/Product nutrition bases drifted")
        if (
            tuple(ingredient_product_contract.serving.requiredFields)
            != INGREDIENT_PRODUCT_SERVING_REQUIRED_FIELDS
        ):
            raise ValueError("Ingredient/Product serving required fields drifted")
        if (
            tuple(ingredient_product_contract.serving.servingSizeFields)
            != INGREDIENT_PRODUCT_SERVING_SIZE_FIELDS
        ):
            raise ValueError("Ingredient/Product serving size fields drifted")
        if tuple(ingredient_product_contract.serving.allowedUnits) != (
            INGREDIENT_PRODUCT_SERVING_UNITS
        ):
            raise ValueError("Ingredient/Product serving units drifted")
        if tuple(ingredient_product_contract.profileFlags.allowedDietaryFlags) != (
            INGREDIENT_PRODUCT_DIETARY_FLAGS
        ):
            raise ValueError("Ingredient/Product dietary flags drifted")
        if tuple(ingredient_product_contract.profileFlags.allowedAllergenFlags) != (
            INGREDIENT_PRODUCT_ALLERGEN_FLAGS
        ):
            raise ValueError("Ingredient/Product allergen flags drifted")
        if tuple(ingredient_product_contract.profileFlags.compatibilityStatuses) != (
            INGREDIENT_PRODUCT_PROFILE_COMPATIBILITY_STATUSES
        ):
            raise ValueError("Ingredient/Product profile statuses drifted")
        if (
            tuple(ingredient_product_contract.barcodeIdentities.minimalIdentityFields)
            != INGREDIENT_PRODUCT_BARCODE_MINIMAL_IDENTITY_FIELDS
        ):
            raise ValueError("barcode identity must stay minimal")
        if (
            tuple(ingredient_product_contract.barcodeIdentities.optionalFields)
            != INGREDIENT_PRODUCT_BARCODE_OPTIONAL_FIELDS
        ):
            raise ValueError("barcode optional identity fields drifted")
        if (
            ingredient_product_contract.barcodeIdentities.noCatalogWriteInThisSlice
            is not True
        ):
            raise ValueError(
                "barcode identity must not write product catalog in this slice"
            )
        if ingredient_product_contract.localCacheBoundary.localCacheIsTruth is not False:
            raise ValueError("local cache must not become Ingredient/Product truth")
        meal_template_fields = {
            *self.domainContracts["MealTemplate"].identityFields,
            *self.domainContracts["MealTemplate"].ownedFields,
        }
        if forbidden := (
            meal_template_fields & FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError(
                f"MealTemplate contract reuses logged-meal-only fields: {sorted(forbidden)}"
            )
        if self.loggedMealBoundary.owner != FOOD_LIBRARY_LOGGED_MEAL_OWNER:
            raise ValueError("logged Meal must remain owned by meal logging")
        if self.loggedMealBoundary.schemaName != FOOD_LIBRARY_LOGGED_MEAL_SCHEMA:
            raise ValueError("logged Meal schema name must remain Meal")
        if (
            tuple(self.loggedMealBoundary.mustNotGainFields)
            != FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError("logged Meal forbidden fields must match CH-06 boundary")
        if (
            tuple(self.currentSavedMealsBoundary.currentNames)
            != FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES
        ):
            raise ValueError("current saved-meal names must be explicit")
        if (
            tuple(
                self.currentSavedMealsBoundary.legacyMarkersNotCanonicalLibraryFoundation
            )
            != FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL
        ):
            raise ValueError("legacy saved-meal markers must stay explicit")
        if (
            tuple(self.currentSavedMealsBoundary.mustNotExpandWith)
            != FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError("current saved meals must not expand with library fields")
        if tuple(self.barcodeBoundary.resultOwnership) != FOOD_LIBRARY_BARCODE_RESULT_OWNERS:
            raise ValueError(
                "barcode ownership must stay backend-adapter/add-meal-draft only"
            )
        if self.barcodeBoundary.mustNotWriteLibraryDomains != ["Ingredient/Product"]:
            raise ValueError("barcode lookup must not create product catalog records")
        return self
