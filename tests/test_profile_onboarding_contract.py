from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, get_args

from app.domain.users.models.user_profile import UserProfile
from app.domain.users.services.user_profile_service import UserProfileService
from app.schemas.user_account import (
    ActivityLevelValue,
    AiPersonaValue,
    AllergyValue,
    ChronicDiseaseValue,
    GoalValue,
    LanguageValue,
    PreferenceValue,
    SexValue,
    UnitsSystemValue,
    UserOnboardingRequest,
    UserProfilePatchRequest,
)
from app.services.user_account_service import (
    _build_onboarding_profile_document,
    _normalize_language,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


class _StubProfileService(UserProfileService):
    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile

    async def get_profile(self, *, user_id: str) -> UserProfile | None:
        return self._profile


def _load_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES_DIR / "profile_onboarding_v1.contract.json").read_text(
            encoding="utf-8"
        )
    )


def test_onboarding_request_fields_match_contract_fixture() -> None:
    contract = _load_fixture()

    required_fields = [
        name
        for name, field in UserOnboardingRequest.model_fields.items()
        if field.is_required()
    ]
    optional_fields = [
        name
        for name, field in UserOnboardingRequest.model_fields.items()
        if not field.is_required()
    ]

    assert required_fields == contract["onboardingRequest"]["requiredFields"]
    assert optional_fields == contract["onboardingRequest"]["optionalFields"]


def test_profile_patch_fields_match_contract_fixture() -> None:
    contract = _load_fixture()

    assert list(UserProfilePatchRequest.model_fields.keys()) == contract["profilePatch"][
        "editableFields"
    ]


def test_profile_patch_enums_match_contract_fixture() -> None:
    contract = _load_fixture()

    assert contract["enums"] == {
        "unitsSystem": list(get_args(UnitsSystemValue)),
        "sex": list(get_args(SexValue)),
        "activityLevel": list(get_args(ActivityLevelValue)),
        "goal": list(get_args(GoalValue)),
        "language": list(get_args(LanguageValue)),
        "preferences": list(get_args(PreferenceValue)),
        "chronicDiseases": list(get_args(ChronicDiseaseValue)),
        "allergies": list(get_args(AllergyValue)),
        "aiPersona": list(get_args(AiPersonaValue)),
    }


def test_onboarding_profile_defaults_match_contract_fixture() -> None:
    contract = _load_fixture()

    profile = _build_onboarding_profile_document(
        user_id="user-1",
        normalized_username="neo",
        normalized_language="en",
        auth_email="user@example.com",
        now_iso="2026-05-05T10:00:00Z",
        now_ms=1,
        existing={},
    )

    assert set(profile.keys()) == set(contract["onboardingProfile"]["fields"])
    assert profile["uid"] == "user-1"
    assert profile["email"] == "user@example.com"
    assert profile["username"] == "neo"
    assert profile["plan"] == "free"
    assert profile["createdAt"] == 1
    assert profile["lastLogin"] == "2026-05-05T10:00:00Z"

    for key, expected in contract["onboardingProfile"]["defaults"].items():
        assert profile[key] == expected


def test_language_semantics_match_contract_fixture() -> None:
    contract = _load_fixture()
    semantics = contract["semantics"]["language"]

    assert semantics["default"] == _normalize_language(None)

    for raw_value, expected in semantics["normalizedExamples"].items():
        assert _normalize_language(raw_value) == expected


def test_ai_persona_style_semantics_match_contract_fixture() -> None:
    contract = _load_fixture()
    semantics = contract["semantics"]["aiPersona"]

    default_profile = UserProfileService._to_profile(
        user_id="user-1",
        raw={"language": "en"},
    )
    assert default_profile.ai_persona == semantics["default"]

    style_labels = {}
    for persona in get_args(AiPersonaValue):
        profile = UserProfileService._to_profile(
            user_id="user-1",
            raw={"aiPersona": persona, "language": "en"},
        )
        style_labels[persona] = profile.style_profile["label"]

    assert style_labels == semantics["styleProfileLabels"]

    for raw_value, expected in contract["backendNormalizationExamples"][
        "aiPersona"
    ].items():
        profile = UserProfileService._to_profile(
            user_id="user-1",
            raw={"aiPersona": raw_value, "language": "en"},
        )
        assert profile.ai_persona == expected


def test_critical_field_groups_cover_backend_surfaces() -> None:
    contract = _load_fixture()
    patch_fields = set(UserProfilePatchRequest.model_fields.keys())

    for group_name in ("readiness", "language", "aiPersona", "nutrition"):
        assert set(contract["criticalFieldGroups"][group_name]).issubset(patch_fields)

    profile = UserProfile(
        user_id="user-1",
        goal="maintain",
        activity_level="moderate",
        calorie_target=2200,
        preferences=["vegan"],
        allergies=[],
        language="en",
        ai_persona="focused_coach",
        style_profile={"id": "focused_coach", "label": "Focused Coach"},
        readiness_status="ready",
        readiness_onboarding_completed_at="2026-05-01T09:00:00Z",
        readiness_ready_at="2026-05-02T09:00:00Z",
    )
    summary = asyncio.run(_StubProfileService(profile).get_profile_summary(user_id="user-1"))

    assert set(contract["criticalFieldGroups"]["aiStyle"]).issubset(summary.keys())
