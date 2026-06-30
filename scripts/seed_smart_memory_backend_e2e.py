"""Seed local emulators with a backend-owned Smart Memory item for Maestro."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, cast
from urllib import error, request

from google.cloud import firestore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.firestore_constants import (  # noqa: E402
    SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
    SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
    SMART_MEMORY_SETTINGS_SUBCOLLECTION,
    SMART_MEMORY_SUBCOLLECTION,
    SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.schemas.smart_memory import (  # noqa: E402
    SmartMemoryCandidateUpsertRequest,
    SmartMemorySettingsUpdateRequest,
)
from app.services import smart_memory_service  # noqa: E402


EMAIL = os.getenv("E2E_EMAIL", "e2e@example.com")
PASSWORD = os.getenv("E2E_PASSWORD", "Test@1234")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "demo-fitaly-local")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
NOW = "2026-06-15T10:30:00.000Z"

MEMORY_ITEM_ID = "e2e-memory-portion-yogurt"
CANDIDATE_ID = "e2e-memory-candidate-backend-pull"
SETTINGS_MUTATION_ID = "e2e-smart-memory-backend-pull-settings"
CANDIDATE_MUTATION_ID = "e2e-smart-memory-backend-pull-candidate"
PROMOTE_MUTATION_ID = "e2e-smart-memory-backend-pull-promote"


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set for local emulator seeding.")
    return value


def _auth_emulator_url(path: str) -> str:
    host = _require_env("FIREBASE_AUTH_EMULATOR_HOST")
    return f"http://{host}/identitytoolkit.googleapis.com/v1/{path}?key=fake-api-key"


def _post_auth_emulator(path: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _auth_emulator_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            return dict(json.loads(response.read().decode("utf-8")))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        if "EMAIL_EXISTS" in detail and path == "accounts:signUp":
            return _post_auth_emulator(
                "accounts:signInWithPassword",
                {"email": EMAIL, "password": PASSWORD, "returnSecureToken": True},
            )
        raise RuntimeError(detail) from exc


def _seed_auth_user() -> tuple[str, str]:
    payload = _post_auth_emulator(
        "accounts:signUp",
        {"email": EMAIL, "password": PASSWORD, "returnSecureToken": True},
    )
    return str(payload["localId"]), str(payload["idToken"])


def _profile_document(uid: str) -> dict[str, Any]:
    return {
        "uid": uid,
        "email": EMAIL,
        "username": "e2e-user",
        "plan": "free",
        "syncState": "pending",
        "createdAt": 1735689600000,
        "lastLogin": NOW,
        "lastSyncedAt": "",
        "avatarUrl": "",
        "avatarlastSyncedAt": "",
        "profile": {
            "language": "pl",
            "nutritionProfile": {
                "unitsSystem": "metric",
                "age": "30",
                "sex": "female",
                "height": "170",
                "heightInch": "",
                "weight": "65",
                "preferences": ["balanced"],
                "activityLevel": "moderate",
                "goal": "maintain",
                "chronicDiseases": [],
                "chronicDiseasesOther": "",
                "allergies": [],
                "allergiesOther": "",
                "lifestyle": "",
                "calorieTarget": 2200,
            },
            "aiPreferences": {"stylePersona": "calm_guide"},
            "aiConsent": {
                "status": "granted",
                "grantedAt": NOW,
                "revokedAt": None,
            },
            "readiness": {
                "status": "ready",
                "onboardingCompletedAt": NOW,
                "readyAt": NOW,
            },
        },
    }


def _candidate_request() -> SmartMemoryCandidateUpsertRequest:
    return SmartMemoryCandidateUpsertRequest.model_validate(
        {
            "clientMutationId": CANDIDATE_MUTATION_ID,
            "candidateId": CANDIDATE_ID,
            "memoryType": "typical_portion",
            "subject": {
                "kind": "ingredient_alias",
                "aliasHash": "e2e_alias_hash_yogurt",
            },
            "evidenceSummary": {
                "thresholdVersion": "typical_portion_v1",
                "requiredObservationCount": 3,
                "requiredDistinctDayCount": 3,
                "eligibleObservationCount": 3,
                "distinctDayCount": 3,
                "proposedValue": {"amount": 180, "unit": "g"},
            },
            "sourceRefs": [
                {
                    "kind": "meal_portion_observation",
                    "sourceHash": "e2e_source_hash_yogurt_1",
                },
                {
                    "kind": "meal_portion_observation",
                    "sourceHash": "e2e_source_hash_yogurt_2",
                },
                {
                    "kind": "meal_portion_observation",
                    "sourceHash": "e2e_source_hash_yogurt_3",
                },
            ],
            "confidenceReasonCodes": ["distinct_days_met"],
            "suppressionChecks": {
                "deletedSuppressed": False,
                "sourceDeleted": False,
                "subjectSuppressionKey": "typical_portion:e2e_alias_hash_yogurt",
            },
            "firstSeenAt": "2026-06-15T10:00:00.000Z",
            "lastSeenAt": "2026-06-15T10:02:00.000Z",
        }
    )


def _emulator_firestore_client() -> firestore.Client:
    _require_env("FIRESTORE_EMULATOR_HOST")
    client_factory = cast(Any, firestore.Client)
    return cast(
        firestore.Client,
        client_factory(project=PROJECT_ID, database=DATABASE_ID),
    )


def _clear_collection(collection_ref: firestore.CollectionReference) -> None:
    for snapshot in collection_ref.stream():
        snapshot.reference.delete()


def _reset_smart_memory_state(client: firestore.Client, uid: str) -> None:
    user_ref = client.collection(USERS_COLLECTION).document(uid)
    for subcollection in (
        SMART_MEMORY_SUBCOLLECTION,
        SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
        SMART_MEMORY_SETTINGS_SUBCOLLECTION,
        SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
        SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
    ):
        _clear_collection(user_ref.collection(subcollection))


async def _seed_smart_memory(uid: str, client: firestore.Client) -> dict[str, Any]:
    smart_memory_service.get_firestore = lambda: client
    await smart_memory_service.update_settings(
        uid,
        SmartMemorySettingsUpdateRequest.model_validate(
            {
                "clientMutationId": SETTINGS_MUTATION_ID,
                "enabled": True,
            }
        ),
    )
    candidate = _candidate_request()
    await smart_memory_service.upsert_candidate(uid, candidate)
    result = await smart_memory_service.promote_candidate(
        uid,
        candidate.candidateId,
        memory_item_id=MEMORY_ITEM_ID,
        client_mutation_id=PROMOTE_MUTATION_ID,
    )
    items = await smart_memory_service.list_items(uid, limit_count=10)
    candidates = await smart_memory_service.list_candidates(uid, limit_count=10)
    return {
        "memoryItemId": result["document"]["memoryItemId"],
        "state": result["document"]["state"],
        "items": len(items),
        "candidateRowsReturned": len(candidates),
    }


async def _main_async() -> None:
    uid, _ = _seed_auth_user()
    client = _emulator_firestore_client()
    _reset_smart_memory_state(client, uid)
    client.collection(USERS_COLLECTION).document(uid).set(
        _profile_document(uid),
        merge=True,
    )
    seeded = await _seed_smart_memory(uid, client)
    print(
        json.dumps(
            {
                "uid": uid,
                "email": EMAIL,
                **seeded,
            },
            sort_keys=True,
        )
    )


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
