"""Route-level Firestore emulator evidence for access-state billing truth."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, cast
from unittest.mock import MagicMock
from urllib import request
from uuid import uuid4

import firebase_admin
import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.firestore_constants import (
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    USERS_COLLECTION,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST")
    or not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIREBASE_PROJECT_ID"),
    reason="Firestore/Auth emulators and FIREBASE_PROJECT_ID are not configured.",
)


def _auth_emulator_url(path: str) -> str:
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"].strip()
    return f"http://{host}/identitytoolkit.googleapis.com/v1/{path}?key=fake-api-key"


def _post_auth_emulator(path: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _auth_emulator_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def _sign_up_auth_emulator_user(email: str, password: str) -> tuple[str, str]:
    payload = _post_auth_emulator(
        "accounts:signUp",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    uid = str(payload["localId"])
    id_token = str(payload["idToken"])
    return uid, id_token


def _delete_auth_emulator_user(id_token: str) -> None:
    try:
        _post_auth_emulator("accounts:delete", {"idToken": id_token})
    except Exception:
        return


def _reset_firebase_singletons() -> None:
    from app.db import firebase as firebase_db

    firebase_db.get_firestore.cache_clear()
    firebase_db.get_storage_bucket.cache_clear()
    delete_app = cast(Callable[[firebase_admin.App], None], getattr(firebase_admin, "delete_app"))
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _patch_emulator_firebase_settings(monkeypatch: MonkeyPatch) -> None:
    project_id = os.environ["FIREBASE_PROJECT_ID"].strip()
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        local_credentials = Path("service-account.json")
        if local_credentials.exists():
            credentials_path = str(local_credentials)

    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", database_id)
    if credentials_path:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setattr(settings, "FIRESTORE_DATABASE_ID", database_id)
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", credentials_path)


def _emulator_firestore_client() -> firestore.Client:
    from app.db.firebase import get_firestore

    return get_firestore()


def _billing_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(BILLING_SUBCOLLECTION)
        .document(BILLING_DOCUMENT_ID)
    )


def _credits_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _billing_ref(client, user_id)
        .collection(AI_CREDITS_SUBCOLLECTION)
        .document(AI_CREDITS_CURRENT_DOCUMENT_ID)
    )


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _seed_credits_snapshot(
    client: firestore.Client,
    user_id: str,
    *,
    tier: str,
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
    renewal_anchor_source: str,
    revenuecat_entitlement_id: str | None = None,
    revenuecat_expiration_at: datetime | None = None,
    last_revenuecat_event_id: str | None = None,
) -> None:
    now = datetime(2026, 5, 17, 12, 30, tzinfo=timezone.utc)
    _credits_ref(client, user_id).set(
        {
            "tier": tier,
            "balance": balance,
            "allocation": allocation,
            "periodStartAt": period_start_at,
            "periodEndAt": period_end_at,
            "renewalAnchorSource": renewal_anchor_source,
            "revenueCatEntitlementId": revenuecat_entitlement_id,
            "revenueCatExpirationAt": revenuecat_expiration_at,
            "lastRevenueCatEventId": last_revenuecat_event_id,
            "createdAt": now,
            "updatedAt": now,
        }
    )


def _assert_premium_feature_enabled(feature: dict[str, object]) -> None:
    assert feature == {
        "enabled": True,
        "status": "enabled",
        "reason": None,
        "requiredCredits": None,
        "remainingCredits": None,
    }


def _assert_premium_feature_disabled(feature: dict[str, object]) -> None:
    assert feature == {
        "enabled": False,
        "status": "disabled",
        "reason": "requires_premium",
        "requiredCredits": None,
        "remainingCredits": None,
    }


def _assert_credit_feature_disabled(
    feature: dict[str, object],
    *,
    required_credits: int,
) -> None:
    assert feature == {
        "enabled": False,
        "status": "disabled",
        "reason": "insufficient_credits",
        "requiredCredits": required_credits,
        "remainingCredits": 0,
    }


def test_access_state_routes_use_real_auth_token_and_firestore_credits_snapshot(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    from app.main import app

    api_client = TestClient(app)
    firestore_client = _emulator_firestore_client()
    run_id = uuid4().hex
    password = "emulator-password-123"
    free_token = ""
    premium_token = ""
    free_uid = ""
    premium_uid = ""
    free_period_start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    free_period_end = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    premium_period_start = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    premium_period_end = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    try:
        free_uid, free_token = _sign_up_auth_emulator_user(
            f"access-state-free-{run_id}@example.invalid",
            password,
        )
        premium_uid, premium_token = _sign_up_auth_emulator_user(
            f"access-state-premium-{run_id}@example.invalid",
            password,
        )

        _seed_credits_snapshot(
            firestore_client,
            free_uid,
            tier="free",
            balance=0,
            allocation=settings.AI_CREDITS_FREE,
            period_start_at=free_period_start,
            period_end_at=free_period_end,
            renewal_anchor_source="free_cycle_start",
        )
        _seed_credits_snapshot(
            firestore_client,
            premium_uid,
            tier="premium",
            balance=37,
            allocation=settings.AI_CREDITS_PREMIUM,
            period_start_at=premium_period_start,
            period_end_at=premium_period_end,
            renewal_anchor_source="premium_activation",
            revenuecat_entitlement_id="premium",
            revenuecat_expiration_at=premium_period_end,
            last_revenuecat_event_id=f"evt-{run_id}",
        )

        free_response = api_client.get(
            "/api/v1/billing/access-state",
            headers=_auth_headers(free_token),
        )

        assert free_response.status_code == 200
        free_payload = free_response.json()
        assert free_payload["tier"] == "free"
        assert free_payload["entitlementStatus"] == "inactive"
        assert free_payload["credits"] == {
            "userId": free_uid,
            "tier": "free",
            "balance": 0,
            "allocation": settings.AI_CREDITS_FREE,
            "periodStartAt": "2026-06-01T00:00:00Z",
            "periodEndAt": "2026-07-01T00:00:00Z",
            "costs": {
                "chat": settings.AI_CREDIT_COST_CHAT,
                "textMeal": settings.AI_CREDIT_COST_TEXT_MEAL,
                "photo": settings.AI_CREDIT_COST_PHOTO,
            },
            "renewalAnchorSource": "free_cycle_start",
            "revenueCatEntitlementId": None,
            "revenueCatExpirationAt": None,
            "lastRevenueCatEventId": None,
        }
        _assert_credit_feature_disabled(
            free_payload["features"]["aiChat"],
            required_credits=settings.AI_CREDIT_COST_CHAT,
        )
        _assert_credit_feature_disabled(
            free_payload["features"]["textMealAnalysis"],
            required_credits=settings.AI_CREDIT_COST_TEXT_MEAL,
        )
        _assert_credit_feature_disabled(
            free_payload["features"]["photoAnalysis"],
            required_credits=settings.AI_CREDIT_COST_PHOTO,
        )
        _assert_premium_feature_disabled(free_payload["features"]["weeklyReport"])
        _assert_premium_feature_disabled(free_payload["features"]["fullHistory"])
        _assert_premium_feature_disabled(free_payload["features"]["cloudBackup"])

        premium_response = api_client.get(
            "/api/v1/billing/access-state",
            headers=_auth_headers(premium_token),
        )
        premium_alias_response = api_client.get(
            "/api/v1/me/access",
            headers=_auth_headers(premium_token),
        )

        assert premium_response.status_code == 200
        assert premium_alias_response.status_code == 200
        premium_payload = premium_response.json()
        assert premium_alias_response.json()["tier"] == premium_payload["tier"]
        assert (
            premium_alias_response.json()["entitlementStatus"]
            == premium_payload["entitlementStatus"]
        )
        assert premium_alias_response.json()["credits"] == premium_payload["credits"]
        assert premium_alias_response.json()["features"] == premium_payload["features"]
        assert premium_payload["tier"] == "premium"
        assert premium_payload["entitlementStatus"] == "active"
        assert premium_payload["credits"] == {
            "userId": premium_uid,
            "tier": "premium",
            "balance": 37,
            "allocation": settings.AI_CREDITS_PREMIUM,
            "periodStartAt": "2026-06-01T12:00:00Z",
            "periodEndAt": "2026-07-01T12:00:00Z",
            "costs": {
                "chat": settings.AI_CREDIT_COST_CHAT,
                "textMeal": settings.AI_CREDIT_COST_TEXT_MEAL,
                "photo": settings.AI_CREDIT_COST_PHOTO,
            },
            "renewalAnchorSource": "premium_activation",
            "revenueCatEntitlementId": "premium",
            "revenueCatExpirationAt": "2026-07-01T12:00:00Z",
            "lastRevenueCatEventId": f"evt-{run_id}",
        }
        assert premium_payload["features"]["aiChat"] == {
            "enabled": True,
            "status": "enabled",
            "reason": None,
            "requiredCredits": settings.AI_CREDIT_COST_CHAT,
            "remainingCredits": 37 - settings.AI_CREDIT_COST_CHAT,
        }
        assert premium_payload["features"]["textMealAnalysis"] == {
            "enabled": True,
            "status": "enabled",
            "reason": None,
            "requiredCredits": settings.AI_CREDIT_COST_TEXT_MEAL,
            "remainingCredits": 37 - settings.AI_CREDIT_COST_TEXT_MEAL,
        }
        assert premium_payload["features"]["photoAnalysis"] == {
            "enabled": True,
            "status": "enabled",
            "reason": None,
            "requiredCredits": settings.AI_CREDIT_COST_PHOTO,
            "remainingCredits": 37 - settings.AI_CREDIT_COST_PHOTO,
        }
        _assert_premium_feature_enabled(premium_payload["features"]["weeklyReport"])
        _assert_premium_feature_enabled(premium_payload["features"]["fullHistory"])
        _assert_premium_feature_enabled(premium_payload["features"]["cloudBackup"])

        repeated_free_response = api_client.get(
            "/api/v1/billing/access-state",
            headers=_auth_headers(free_token),
        )
        assert repeated_free_response.status_code == 200
        assert repeated_free_response.json()["credits"]["userId"] == free_uid
        assert repeated_free_response.json()["credits"]["tier"] == "free"
        assert repeated_free_response.json()["credits"]["balance"] == 0
        assert repeated_free_response.json()["features"] == free_payload["features"]
    finally:
        for uid in (free_uid, premium_uid):
            if not uid:
                continue
            _credits_ref(firestore_client, uid).delete()
            _billing_ref(firestore_client, uid).delete()
            firestore_client.collection(USERS_COLLECTION).document(uid).delete()
        if free_token:
            _delete_auth_emulator_user(free_token)
        if premium_token:
            _delete_auth_emulator_user(premium_token)
        _reset_firebase_singletons()
