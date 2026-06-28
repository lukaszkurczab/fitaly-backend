"""Seed local Firebase emulators with the baseline E2E login user/profile."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, cast
from urllib import error, request
from urllib.parse import urlsplit

from google.cloud import firestore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.firestore_constants import USERS_COLLECTION  # noqa: E402


EMAIL = os.getenv("E2E_EMAIL", "e2e@example.com")
PASSWORD = os.getenv("E2E_PASSWORD", "Test@1234")
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "demo-fitaly-local")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
NOW = "2026-06-15T10:30:00.000Z"


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set for local emulator seeding.")
    return value


def _require_local_emulator_host(name: str) -> str:
    value = _require_env(name)
    parsed = urlsplit(value if "://" in value else f"//{value}")
    host = (parsed.hostname or "").strip().lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            f"{name} must point to localhost/127.0.0.1 for local emulator seeding."
        )
    return value


def _auth_emulator_url(path: str) -> str:
    host = _require_local_emulator_host("FIREBASE_AUTH_EMULATOR_HOST")
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


def _emulator_firestore_client() -> firestore.Client:
    _require_local_emulator_host("FIRESTORE_EMULATOR_HOST")
    client_factory = cast(Any, firestore.Client)
    return cast(
        firestore.Client,
        client_factory(project=PROJECT_ID, database=DATABASE_ID),
    )


def main() -> None:
    uid, _ = _seed_auth_user()
    client = _emulator_firestore_client()
    client.collection(USERS_COLLECTION).document(uid).set(
        _profile_document(uid),
        merge=True,
    )
    print(
        json.dumps(
            {
                "uid": uid,
                "email": EMAIL,
                "profileDocument": f"{USERS_COLLECTION}/{uid}",
                "databaseId": DATABASE_ID,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
