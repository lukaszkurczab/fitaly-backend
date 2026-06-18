"""Seed local Firebase emulators for the Ingredient autocomplete Maestro flow."""

from __future__ import annotations

import json
import os
from typing import Any, cast
from urllib import error, request

from google.cloud import firestore


EMAIL = os.getenv("E2E_EMAIL", "e2e@example.com")
PASSWORD = os.getenv("E2E_PASSWORD", "Test@1234")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "demo-fitaly-local")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "fitaly-smoke")


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
    now = "2026-06-15T10:30:00.000Z"
    return {
        "uid": uid,
        "email": EMAIL,
        "username": "e2e-user",
        "plan": "free",
        "syncState": "pending",
        "createdAt": 1735689600000,
        "lastLogin": now,
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
                "grantedAt": now,
                "revokedAt": None,
            },
            "readiness": {
                "status": "ready",
                "onboardingCompletedAt": now,
                "readyAt": now,
            },
        },
    }


def _ingredient_product_document() -> dict[str, Any]:
    now = "2026-06-15T10:30:00.000Z"
    return {
        "ingredientProductId": "e2e-local-oats",
        "recordScope": "global_seed",
        "lifecycleState": "verified",
        "displayName": "Owies lokalny",
        "kind": "generic_ingredient",
        "ingredientName": "Owies",
        "brandName": None,
        "packageName": None,
        "category": "grain",
        "searchPrefixes": ["ow", "owi", "owie", "owies", "owies lokalny"],
        "defaultServing": {"quantity": 50, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 389,
            "protein": 16.9,
            "fat": 6.9,
            "carbs": 66.3,
            "fiber": None,
            "sugar": None,
            "salt": None,
            "saturatedFat": None,
        },
        "sourceAttribution": {
            "sourceType": "internal_seed",
            "sourceId": "e2e-local-seed",
            "sourceName": "Fitaly local E2E seed",
            "provider": None,
            "license": None,
            "observedAt": now,
            "reviewedAt": now,
            "reviewedBy": "e2e",
        },
        "confidence": {
            "identity": "verified",
            "nutrition": "high",
            "profile": "high",
        },
        "profileCompatibility": "compatible",
        "profileFlags": {"dietaryFlags": [], "allergenFlags": []},
        "warningReasonCodes": [],
        "servingSizes": [],
        "dietaryFlags": [],
        "allergenFlags": [],
        "createdAt": now,
        "updatedAt": now,
    }


def _warning_ingredient_product_document() -> dict[str, Any]:
    now = "2026-06-15T10:30:00.000Z"
    return {
        "ingredientProductId": "e2e-warning-oats",
        "recordScope": "global_seed",
        "lifecycleState": "verified",
        "displayName": "Owies ostrzezenie",
        "kind": "generic_ingredient",
        "ingredientName": "Owies",
        "brandName": None,
        "packageName": None,
        "category": "grain",
        "searchPrefixes": ["ost", "ostr", "ostrzezenie", "owies ostrzezenie"],
        "defaultServing": {"quantity": 50, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 389,
            "protein": 16.9,
            "fat": 6.9,
            "carbs": 66.3,
            "fiber": None,
            "sugar": None,
            "salt": None,
            "saturatedFat": None,
        },
        "sourceAttribution": {
            "sourceType": "internal_seed",
            "sourceId": "e2e-local-warning-seed",
            "sourceName": "Fitaly local E2E seed",
            "provider": None,
            "license": None,
            "observedAt": now,
            "reviewedAt": now,
            "reviewedBy": "e2e",
        },
        "confidence": {
            "identity": "verified",
            "nutrition": "high",
            "profile": "medium",
        },
        "profileCompatibility": "warning",
        "profileFlags": {
            "dietaryFlags": [],
            "allergenFlags": [],
            "compatibilityStatus": "warning",
        },
        "warningReasonCodes": ["profile_warning"],
        "servingSizes": [],
        "dietaryFlags": [],
        "allergenFlags": [],
        "createdAt": now,
        "updatedAt": now,
    }


def _private_delete_ingredient_product_document(uid: str) -> dict[str, Any]:
    now = "2026-06-15T10:30:00.000Z"
    return {
        "ingredientProductId": "e2e-private-delete-qa",
        "recordScope": "user_scoped",
        "lifecycleState": "candidate",
        "ownerUserId": uid,
        "displayName": "Prywatny delete QA",
        "kind": "generic_ingredient",
        "ingredientName": "Prywatny delete QA",
        "brandName": None,
        "packageName": None,
        "category": "e2e",
        "searchPrefixes": [
            "pr",
            "pry",
            "pryw",
            "prywa",
            "prywat",
            "prywatn",
            "prywatny",
            "prywatny delete",
            "prywatny delete qa",
            "de",
            "del",
            "dele",
            "delet",
            "delete",
            "delete qa",
        ],
        "defaultServing": {"quantity": 40, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 150,
            "protein": 8,
            "fat": 3,
            "carbs": 22,
            "fiber": None,
            "sugar": None,
            "salt": None,
            "saturatedFat": None,
        },
        "sourceAttribution": {
            "sourceType": "user_created",
            "sourceId": "ingredient-product:create:e2e-private-delete-qa",
            "sourceName": "manual_entry",
            "provider": None,
            "license": None,
            "observedAt": now,
            "reviewedAt": None,
            "reviewedBy": None,
        },
        "confidence": {
            "identity": "low",
            "nutrition": "low",
            "profile": "unknown",
        },
        "profileFlags": {
            "compatibilityStatus": "unknown",
            "dietaryFlags": [],
            "allergenFlags": [],
        },
        "warningReasonCodes": ["pending_user_record", "profile_unknown"],
        "servingSizes": [],
        "dietaryFlags": [],
        "allergenFlags": [],
        "creationClientMutationId": "ingredient-product:create:e2e-private-delete-qa",
        "createdAt": now,
        "updatedAt": now,
    }


def _private_update_ingredient_product_document(uid: str) -> dict[str, Any]:
    now = "2026-06-15T10:30:00.000Z"
    return {
        "ingredientProductId": "e2e-private-update-qa",
        "recordScope": "user_scoped",
        "lifecycleState": "candidate",
        "ownerUserId": uid,
        "displayName": "Prywatny update QA",
        "kind": "generic_ingredient",
        "ingredientName": "Prywatny update QA",
        "brandName": None,
        "packageName": None,
        "category": "e2e",
        "searchPrefixes": [
            "pr",
            "pry",
            "pryw",
            "prywa",
            "prywat",
            "prywatn",
            "prywatny",
            "prywatny update",
            "prywatny update qa",
            "up",
            "upd",
            "upda",
            "updat",
            "update",
            "update qa",
        ],
        "defaultServing": {"quantity": 80, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 180,
            "protein": 9,
            "fat": 4,
            "carbs": 27,
            "fiber": None,
            "sugar": None,
            "salt": None,
            "saturatedFat": None,
        },
        "sourceAttribution": {
            "sourceType": "user_created",
            "sourceId": "ingredient-product:create:e2e-private-update-qa",
            "sourceName": "manual_entry",
            "provider": None,
            "license": None,
            "observedAt": now,
            "reviewedAt": None,
            "reviewedBy": None,
        },
        "confidence": {
            "identity": "low",
            "nutrition": "low",
            "profile": "unknown",
        },
        "profileFlags": {
            "compatibilityStatus": "unknown",
            "dietaryFlags": [],
            "allergenFlags": [],
        },
        "warningReasonCodes": ["pending_user_record", "profile_unknown"],
        "servingSizes": [],
        "dietaryFlags": [],
        "allergenFlags": [],
        "creationClientMutationId": "ingredient-product:create:e2e-private-update-qa",
        "createdAt": now,
        "updatedAt": now,
    }


def main() -> None:
    _require_env("FIRESTORE_EMULATOR_HOST")
    uid, _ = _seed_auth_user()
    firestore_client_factory = cast(Any, firestore.Client)
    client = firestore_client_factory(project=PROJECT_ID, database=DATABASE_ID)
    client.collection("users").document(uid).set(_profile_document(uid), merge=True)
    client.collection("ingredientProducts").document("e2e-local-oats").set(
        _ingredient_product_document(),
        merge=True,
    )
    client.collection("ingredientProducts").document("e2e-warning-oats").set(
        _warning_ingredient_product_document(),
        merge=True,
    )
    (
        client.collection("users")
        .document(uid)
        .collection("ingredientProducts")
        .document("e2e-private-delete-qa")
        .set(
            _private_delete_ingredient_product_document(uid),
        )
    )
    (
        client.collection("users")
        .document(uid)
        .collection("ingredientProducts")
        .document("e2e-private-update-qa")
        .set(
            _private_update_ingredient_product_document(uid),
        )
    )
    print(json.dumps({"uid": uid, "email": EMAIL}, sort_keys=True))


if __name__ == "__main__":
    main()
