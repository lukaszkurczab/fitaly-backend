"""Firestore emulator evidence for AI credits billing idempotency state."""

from datetime import datetime, timezone
import hashlib
import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.firestore_constants import (
    AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    USERS_COLLECTION,
)
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


def _idempotency_ref(
    client: firestore.Client,
    user_id: str,
    idempotency_key: str,
) -> firestore.DocumentReference:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return (
        _billing_ref(client, user_id)
        .collection(AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION)
        .document(digest)
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


def _document_data(document_ref: firestore.DocumentReference) -> dict[str, object]:
    snapshot = document_ref.get()
    assert snapshot.exists is True
    return dict(snapshot.to_dict() or {})


async def test_pr4_ai_credits_billing_idempotency_state_uses_firestore_emulator(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"l3-pr4-ai-credits-user-{run_id}"
    idempotency_key = f"chat-run-{run_id}"
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)

    mocker.patch("app.services.ai_credits_service.get_firestore", return_value=client)
    mocker.patch("app.services.ai_credits_service._utc_now", return_value=now)

    credits_ref = _credits_ref(client, user_id)
    idempotency_ref = _idempotency_ref(client, user_id, idempotency_key)
    billing_ref = _billing_ref(client, user_id)

    try:
        initial_status = await ai_credits_service.get_credits_status(user_id)

        assert initial_status.userId == user_id
        assert initial_status.tier == "free"
        assert initial_status.balance == settings.AI_CREDITS_FREE
        assert initial_status.allocation == settings.AI_CREDITS_FREE
        assert initial_status.periodStartAt == now
        assert initial_status.renewalAnchorSource == "free_cycle_start"

        credits_doc = _document_data(credits_ref)
        assert credits_doc["tier"] == "free"
        assert credits_doc["balance"] == settings.AI_CREDITS_FREE
        assert credits_doc["allocation"] == settings.AI_CREDITS_FREE
        assert credits_doc["renewalAnchorSource"] == "free_cycle_start"
        assert _transaction_docs(client, user_id) == []

        first_deduct = await ai_credits_service.deduct_credits_idempotent(
            user_id,
            cost=1,
            action="chat",
            idempotency_key=idempotency_key,
        )

        assert first_deduct.applied is True
        assert first_deduct.refunded is False
        assert first_deduct.status.balance == settings.AI_CREDITS_FREE - 1

        idempotency_doc = _document_data(idempotency_ref)
        assert idempotency_doc["key"] == idempotency_key
        assert idempotency_doc["userId"] == user_id
        assert idempotency_doc["state"] == "deducted"
        assert idempotency_doc["creditDeducted"] is True
        assert idempotency_doc["creditRefunded"] is False
        assert idempotency_doc["cost"] == 1
        assert idempotency_doc["action"] == "chat"
        assert idempotency_doc["balanceBefore"] == settings.AI_CREDITS_FREE
        assert idempotency_doc["balanceAfter"] == settings.AI_CREDITS_FREE - 1
        assert idempotency_doc["deductCount"] == 1
        assert idempotency_doc["refundCount"] == 0
        assert len(_transaction_docs(client, user_id)) == 1

        duplicate_deduct = await ai_credits_service.deduct_credits_idempotent(
            user_id,
            cost=1,
            action="chat",
            idempotency_key=idempotency_key,
        )

        assert duplicate_deduct.applied is False
        assert duplicate_deduct.refunded is False
        assert duplicate_deduct.status.balance == settings.AI_CREDITS_FREE - 1
        assert _document_data(credits_ref)["balance"] == settings.AI_CREDITS_FREE - 1
        assert _document_data(idempotency_ref) == idempotency_doc
        assert len(_transaction_docs(client, user_id)) == 1

        first_refund = await ai_credits_service.refund_credits_idempotent(
            user_id,
            cost=1,
            action="chat",
            idempotency_key=idempotency_key,
        )

        assert first_refund.applied is True
        assert first_refund.refunded is True
        assert first_refund.status.balance == settings.AI_CREDITS_FREE

        refunded_doc = _document_data(idempotency_ref)
        assert refunded_doc["state"] == "refunded"
        assert refunded_doc["creditDeducted"] is True
        assert refunded_doc["creditRefunded"] is True
        assert refunded_doc["deductCount"] == 1
        assert refunded_doc["refundCount"] == 1
        assert refunded_doc["refundBalanceBefore"] == settings.AI_CREDITS_FREE - 1
        assert refunded_doc["refundBalanceAfter"] == settings.AI_CREDITS_FREE
        assert len(_transaction_docs(client, user_id)) == 2

        duplicate_refund = await ai_credits_service.refund_credits_idempotent(
            user_id,
            cost=1,
            action="chat",
            idempotency_key=idempotency_key,
        )

        assert duplicate_refund.applied is False
        assert duplicate_refund.refunded is False
        assert duplicate_refund.status.balance == settings.AI_CREDITS_FREE
        assert _document_data(credits_ref)["balance"] == settings.AI_CREDITS_FREE
        assert _document_data(idempotency_ref) == refunded_doc
        assert len(_transaction_docs(client, user_id)) == 2

        rededuct = await ai_credits_service.deduct_credits_idempotent(
            user_id,
            cost=1,
            action="chat",
            idempotency_key=idempotency_key,
        )

        assert rededuct.applied is True
        assert rededuct.refunded is False
        assert rededuct.status.balance == settings.AI_CREDITS_FREE - 1

        rededucted_doc = _document_data(idempotency_ref)
        assert rededucted_doc["state"] == "deducted"
        assert rededucted_doc["creditDeducted"] is True
        assert rededucted_doc["creditRefunded"] is False
        assert rededucted_doc["deductCount"] == 2
        assert rededucted_doc["refundCount"] == 1
        assert rededucted_doc["balanceBefore"] == settings.AI_CREDITS_FREE
        assert rededucted_doc["balanceAfter"] == settings.AI_CREDITS_FREE - 1

        ledger_docs = _transaction_docs(client, user_id)
        assert len(ledger_docs) == 3
        assert [
            (
                doc["type"],
                doc["action"],
                doc["cost"],
                doc["idempotencyKey"],
            )
            for doc in ledger_docs
        ].count(("deduct", "chat", 1, idempotency_key)) == 2
        assert [
            (
                doc["type"],
                doc["action"],
                doc["cost"],
                doc["idempotencyKey"],
            )
            for doc in ledger_docs
        ].count(("refund", "chat", 1, idempotency_key)) == 1
    finally:
        for snapshot in billing_ref.collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION).stream():
            snapshot.reference.delete()
        idempotency_ref.delete()
        credits_ref.delete()
        billing_ref.delete()
        client.collection(USERS_COLLECTION).document(user_id).delete()
