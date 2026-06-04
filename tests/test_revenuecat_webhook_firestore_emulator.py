"""Route-level Firestore emulator evidence for RevenueCat webhook billing writes."""

from datetime import datetime, timezone
import os
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore

from app.core.config import settings
from app.core.firestore_constants import (
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.main import app
from app.services import ai_credits_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


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


def _transaction_docs(
    client: firestore.Client,
    user_id: str,
) -> list[dict[str, object]]:
    snapshots = (
        _billing_ref(client, user_id)
        .collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION)
        .stream()
    )
    return [dict(snapshot.to_dict() or {}) for snapshot in snapshots]


def _subscription_transition_docs(
    client: firestore.Client,
    user_id: str,
    action: str,
) -> list[dict[str, object]]:
    return [
        doc
        for doc in _transaction_docs(client, user_id)
        if doc.get("type") == "subscription_transition" and doc.get("action") == action
    ]


def _document_data(document_ref: firestore.DocumentReference) -> dict[str, object]:
    snapshot = document_ref.get()
    assert snapshot.exists is True
    return dict(snapshot.to_dict() or {})


def _datetime_value(value: object) -> datetime:
    assert isinstance(value, datetime)
    return value


def _cleanup_user(client: firestore.Client, user_id: str) -> None:
    billing_ref = _billing_ref(client, user_id)
    for snapshot in billing_ref.collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION).stream():
        snapshot.reference.delete()
    _credits_ref(client, user_id).delete()
    billing_ref.delete()
    client.collection(USERS_COLLECTION).document(user_id).delete()


def test_revenuecat_webhook_writes_premium_credits_and_is_idempotent_with_firestore_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore_client = _emulator_client()
    api_client = TestClient(app)
    run_id = uuid4().hex
    user_id = f"l3-pr4-revenuecat-webhook-user-{run_id}"
    event_id = f"evt-l3-pr4-{run_id}"
    webhook_secret = f"secret-l3-pr4-{run_id}"
    premium_entitlement_id = "premium-l3-pr4"
    purchased_at = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    expiration_at = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(
        settings,
        "REVENUECAT_PREMIUM_ENTITLEMENT_ID",
        premium_entitlement_id,
    )
    monkeypatch.setattr(
        ai_credits_service,
        "get_firestore",
        lambda: firestore_client,
    )

    credits_ref = _credits_ref(firestore_client, user_id)
    payload = {
        "event": {
            "id": event_id,
            "type": "INITIAL_PURCHASE",
            "app_user_id": user_id,
            "entitlement_id": premium_entitlement_id,
            "purchased_at": purchased_at.isoformat(),
            "expiration_at": expiration_at.isoformat(),
        }
    }

    try:
        invalid_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": "invalid-secret"},
            json=payload,
        )

        assert invalid_response.status_code == 401
        assert invalid_response.json() == {"detail": "Invalid webhook signature"}
        assert credits_ref.get().exists is False
        assert _transaction_docs(firestore_client, user_id) == []

        first_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert first_response.status_code == 200
        assert first_response.json() == {
            "ok": True,
            "eventType": "INITIAL_PURCHASE",
            "userId": user_id,
            "tier": "premium",
            "balance": settings.AI_CREDITS_PREMIUM,
        }

        first_credits_doc = _document_data(credits_ref)
        assert first_credits_doc["tier"] == "premium"
        assert first_credits_doc["allocation"] == settings.AI_CREDITS_PREMIUM
        assert first_credits_doc["balance"] == settings.AI_CREDITS_PREMIUM
        assert first_credits_doc["renewalAnchorSource"] == "premium_activation"
        assert first_credits_doc["lastRevenueCatEventId"] == event_id
        assert first_credits_doc["revenueCatEntitlementId"] == premium_entitlement_id
        assert _datetime_value(first_credits_doc["periodStartAt"]) == purchased_at
        assert _datetime_value(first_credits_doc["periodEndAt"]) == expiration_at
        assert _datetime_value(first_credits_doc["revenueCatExpirationAt"]) == expiration_at

        first_ledger_docs = _transaction_docs(firestore_client, user_id)
        subscription_transition_docs = _subscription_transition_docs(
            firestore_client,
            user_id,
            "premium_activation",
        )
        assert len(subscription_transition_docs) == 1
        assert subscription_transition_docs[0]["balanceBefore"] == 0
        assert subscription_transition_docs[0]["balanceAfter"] == settings.AI_CREDITS_PREMIUM

        duplicate_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == first_response.json()
        assert _document_data(credits_ref) == first_credits_doc

        duplicate_ledger_docs = _transaction_docs(firestore_client, user_id)
        duplicate_subscription_transition_docs = _subscription_transition_docs(
            firestore_client,
            user_id,
            "premium_activation",
        )
        assert len(duplicate_subscription_transition_docs) == 1
        assert len(duplicate_ledger_docs) == len(first_ledger_docs)
    finally:
        _cleanup_user(firestore_client, user_id)


def test_revenuecat_webhook_renewal_writes_premium_state_and_is_idempotent_with_firestore_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore_client = _emulator_client()
    api_client = TestClient(app)
    run_id = uuid4().hex
    user_id = f"l3-pr4-revenuecat-renewal-user-{run_id}"
    event_id = f"evt-l3-pr4-renewal-{run_id}"
    webhook_secret = f"secret-l3-pr4-renewal-{run_id}"
    premium_entitlement_id = "premium-l3-pr4"
    previous_period_start = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    previous_period_end = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    renewal_at = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    expiration_at = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(
        settings,
        "REVENUECAT_PREMIUM_ENTITLEMENT_ID",
        premium_entitlement_id,
    )
    monkeypatch.setattr(
        ai_credits_service,
        "get_firestore",
        lambda: firestore_client,
    )

    credits_ref = _credits_ref(firestore_client, user_id)
    payload = {
        "event": {
            "id": event_id,
            "type": "RENEWAL",
            "app_user_id": user_id,
            "entitlement_id": premium_entitlement_id,
            "purchased_at": renewal_at.isoformat(),
            "expiration_at": expiration_at.isoformat(),
        }
    }

    try:
        credits_ref.set(
            {
                "tier": "premium",
                "allocation": settings.AI_CREDITS_PREMIUM,
                "balance": 321,
                "periodStartAt": previous_period_start,
                "periodEndAt": previous_period_end,
                "renewalAnchorSource": "premium_activation",
                "revenueCatEntitlementId": premium_entitlement_id,
                "revenueCatExpirationAt": previous_period_end,
                "lastRevenueCatEventId": f"previous-{event_id}",
                "createdAt": previous_period_start,
                "updatedAt": previous_period_start,
            }
        )

        first_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert first_response.status_code == 200
        assert first_response.json() == {
            "ok": True,
            "eventType": "RENEWAL",
            "userId": user_id,
            "tier": "premium",
            "balance": settings.AI_CREDITS_PREMIUM,
        }

        first_credits_doc = _document_data(credits_ref)
        assert first_credits_doc["tier"] == "premium"
        assert first_credits_doc["allocation"] == settings.AI_CREDITS_PREMIUM
        assert first_credits_doc["balance"] == settings.AI_CREDITS_PREMIUM
        assert first_credits_doc["renewalAnchorSource"] == "premium_renewal"
        assert first_credits_doc["lastRevenueCatEventId"] == event_id
        assert first_credits_doc["revenueCatEntitlementId"] == premium_entitlement_id
        assert _datetime_value(first_credits_doc["periodStartAt"]) == renewal_at
        assert _datetime_value(first_credits_doc["periodEndAt"]) == expiration_at
        assert _datetime_value(first_credits_doc["revenueCatExpirationAt"]) == expiration_at

        first_ledger_docs = _transaction_docs(firestore_client, user_id)
        renewal_transition_docs = _subscription_transition_docs(
            firestore_client,
            user_id,
            "premium_renewal",
        )
        assert len(renewal_transition_docs) == 1
        assert renewal_transition_docs[0]["balanceBefore"] == 321
        assert renewal_transition_docs[0]["balanceAfter"] == settings.AI_CREDITS_PREMIUM

        duplicate_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == first_response.json()
        assert _document_data(credits_ref) == first_credits_doc
        assert (
            len(_subscription_transition_docs(firestore_client, user_id, "premium_renewal"))
            == 1
        )
        assert len(_transaction_docs(firestore_client, user_id)) == len(
            first_ledger_docs
        )
    finally:
        _cleanup_user(firestore_client, user_id)


def test_revenuecat_webhook_expiration_writes_free_state_and_is_idempotent_with_firestore_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore_client = _emulator_client()
    api_client = TestClient(app)
    run_id = uuid4().hex
    user_id = f"l3-pr4-revenuecat-expiration-user-{run_id}"
    event_id = f"evt-l3-pr4-expiration-{run_id}"
    webhook_secret = f"secret-l3-pr4-expiration-{run_id}"
    premium_entitlement_id = "premium-l3-pr4"
    premium_period_start = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    expiration_at = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    free_period_end = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(
        settings,
        "REVENUECAT_PREMIUM_ENTITLEMENT_ID",
        premium_entitlement_id,
    )
    monkeypatch.setattr(
        ai_credits_service,
        "get_firestore",
        lambda: firestore_client,
    )

    credits_ref = _credits_ref(firestore_client, user_id)
    payload = {
        "event": {
            "id": event_id,
            "type": "EXPIRATION",
            "app_user_id": user_id,
            "entitlement_id": premium_entitlement_id,
            "expiration_at": expiration_at.isoformat(),
        }
    }

    try:
        credits_ref.set(
            {
                "tier": "premium",
                "allocation": settings.AI_CREDITS_PREMIUM,
                "balance": 47,
                "periodStartAt": premium_period_start,
                "periodEndAt": expiration_at,
                "renewalAnchorSource": "premium_renewal",
                "revenueCatEntitlementId": premium_entitlement_id,
                "revenueCatExpirationAt": expiration_at,
                "lastRevenueCatEventId": f"previous-{event_id}",
                "createdAt": premium_period_start,
                "updatedAt": premium_period_start,
            }
        )

        first_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert first_response.status_code == 200
        assert first_response.json() == {
            "ok": True,
            "eventType": "EXPIRATION",
            "userId": user_id,
            "tier": "free",
            "balance": settings.AI_CREDITS_FREE,
        }

        first_credits_doc = _document_data(credits_ref)
        assert first_credits_doc["tier"] == "free"
        assert first_credits_doc["allocation"] == settings.AI_CREDITS_FREE
        assert first_credits_doc["balance"] == settings.AI_CREDITS_FREE
        assert first_credits_doc["renewalAnchorSource"] == "premium_expiration_free_cycle_start"
        assert first_credits_doc["lastRevenueCatEventId"] == event_id
        assert first_credits_doc["revenueCatEntitlementId"] is None
        assert first_credits_doc["revenueCatExpirationAt"] is None
        assert _datetime_value(first_credits_doc["periodStartAt"]) == expiration_at
        assert _datetime_value(first_credits_doc["periodEndAt"]) == free_period_end

        first_ledger_docs = _transaction_docs(firestore_client, user_id)
        expiration_transition_docs = _subscription_transition_docs(
            firestore_client,
            user_id,
            "premium_expiration_free_cycle_start",
        )
        assert len(expiration_transition_docs) == 1
        assert expiration_transition_docs[0]["balanceBefore"] == 47
        assert expiration_transition_docs[0]["balanceAfter"] == settings.AI_CREDITS_FREE

        duplicate_response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == first_response.json()
        assert _document_data(credits_ref) == first_credits_doc
        assert (
            len(
                _subscription_transition_docs(
                    firestore_client,
                    user_id,
                    "premium_expiration_free_cycle_start",
                )
            )
            == 1
        )
        assert len(_transaction_docs(firestore_client, user_id)) == len(
            first_ledger_docs
        )
    finally:
        _cleanup_user(firestore_client, user_id)


def test_revenuecat_webhook_uncancellation_uses_activation_path_with_firestore_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firestore_client = _emulator_client()
    api_client = TestClient(app)
    run_id = uuid4().hex
    user_id = f"l3-pr4-revenuecat-uncancellation-user-{run_id}"
    event_id = f"evt-l3-pr4-uncancellation-{run_id}"
    webhook_secret = f"secret-l3-pr4-uncancellation-{run_id}"
    premium_entitlement_id = "premium-l3-pr4"
    uncancelled_at = datetime(2026, 6, 21, 9, 30, tzinfo=timezone.utc)
    expiration_at = datetime(2026, 7, 21, 9, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "REVENUECAT_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setattr(
        settings,
        "REVENUECAT_PREMIUM_ENTITLEMENT_ID",
        premium_entitlement_id,
    )
    monkeypatch.setattr(
        ai_credits_service,
        "get_firestore",
        lambda: firestore_client,
    )

    credits_ref = _credits_ref(firestore_client, user_id)
    payload = {
        "event": {
            "id": event_id,
            "type": "UNCANCELLATION",
            "app_user_id": user_id,
            "entitlement_id": premium_entitlement_id,
            "purchased_at": uncancelled_at.isoformat(),
            "expiration_at": expiration_at.isoformat(),
        }
    }

    try:
        response = api_client.post(
            "/webhooks/revenuecat",
            headers={"X-RevenueCat-Signature": webhook_secret},
            json=payload,
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "eventType": "UNCANCELLATION",
            "userId": user_id,
            "tier": "premium",
            "balance": settings.AI_CREDITS_PREMIUM,
        }

        credits_doc = _document_data(credits_ref)
        assert credits_doc["tier"] == "premium"
        assert credits_doc["allocation"] == settings.AI_CREDITS_PREMIUM
        assert credits_doc["balance"] == settings.AI_CREDITS_PREMIUM
        assert credits_doc["renewalAnchorSource"] == "premium_activation"
        assert credits_doc["lastRevenueCatEventId"] == event_id
        assert credits_doc["revenueCatEntitlementId"] == premium_entitlement_id
        assert _datetime_value(credits_doc["periodStartAt"]) == uncancelled_at
        assert _datetime_value(credits_doc["periodEndAt"]) == expiration_at
        assert _datetime_value(credits_doc["revenueCatExpirationAt"]) == expiration_at
        assert (
            len(
                _subscription_transition_docs(
                    firestore_client,
                    user_id,
                    "premium_activation",
                )
            )
            == 1
        )
    finally:
        _cleanup_user(firestore_client, user_id)
