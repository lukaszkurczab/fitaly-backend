#!/usr/bin/env python3
"""Migrate AI credits storage from legacy top-level collections to user-owned billing paths.

Legacy (to remove):
  - ai_credits/{uid}
  - ai_credit_transactions/{txId}

Canonical target:
  - users/{uid}/billing/main/aiCredits/current
  - users/{uid}/billing/main/aiCreditTransactions/{txId}
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
from typing import Any

# ── project root on sys.path ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.coercion import coerce_optional_str  # noqa: E402
from app.core.firestore_constants import (  # noqa: E402
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore, init_firebase  # noqa: E402

LEGACY_AI_CREDITS_COLLECTION = "ai_credits"
LEGACY_AI_CREDIT_TRANSACTIONS_COLLECTION = "ai_credit_transactions"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_ai_credits_to_billing")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _billing_root_ref(client: Any, user_id: str) -> Any:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(BILLING_SUBCOLLECTION)
        .document(BILLING_DOCUMENT_ID)
    )


def _credits_ref(client: Any, user_id: str) -> Any:
    return (
        _billing_root_ref(client, user_id)
        .collection(AI_CREDITS_SUBCOLLECTION)
        .document(AI_CREDITS_CURRENT_DOCUMENT_ID)
    )


def _transactions_ref(client: Any, user_id: str) -> Any:
    return _billing_root_ref(client, user_id).collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION)


def _normalize_snapshot_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.pop("userId", None)
    return normalized


def _normalize_transaction_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.pop("userId", None)
    return normalized


def migrate(*, dry_run: bool, keep_legacy: bool) -> None:
    client = get_firestore()
    now = _utc_now()

    migrated_snapshots = 0
    skipped_snapshots = 0
    migrated_transactions = 0
    skipped_transactions = 0
    deleted_legacy_snapshots = 0
    deleted_legacy_transactions = 0
    missing_tx_user = 0
    touched_users: set[str] = set()

    legacy_snapshot_docs = list(client.collection(LEGACY_AI_CREDITS_COLLECTION).stream())
    legacy_tx_docs = list(client.collection(LEGACY_AI_CREDIT_TRANSACTIONS_COLLECTION).stream())

    logger.info(
        "Found %d legacy snapshot docs and %d legacy transaction docs.",
        len(legacy_snapshot_docs),
        len(legacy_tx_docs),
    )

    for legacy_doc in legacy_snapshot_docs:
        user_id = legacy_doc.id
        legacy_payload = dict(legacy_doc.to_dict() or {})
        target_ref = _credits_ref(client, user_id)
        target_snapshot = target_ref.get()
        if target_snapshot.exists:
            skipped_snapshots += 1
            logger.info("Skipping snapshot for uid=%s (target already exists).", user_id)
            if not keep_legacy and not dry_run:
                legacy_doc.reference.delete()
                deleted_legacy_snapshots += 1
            continue

        normalized = _normalize_snapshot_payload(legacy_payload)
        touched_users.add(user_id)
        if dry_run:
            migrated_snapshots += 1
            continue

        _billing_root_ref(client, user_id).set(
            {"namespace": "ai_billing", "updatedAt": now},
            merge=True,
        )
        target_ref.set(normalized, merge=True)
        migrated_snapshots += 1
        if not keep_legacy:
            legacy_doc.reference.delete()
            deleted_legacy_snapshots += 1

    for legacy_doc in legacy_tx_docs:
        legacy_payload = dict(legacy_doc.to_dict() or {})
        user_id = coerce_optional_str(legacy_payload.get("userId"))
        if not user_id:
            missing_tx_user += 1
            logger.warning("Skipping tx=%s (missing userId).", legacy_doc.id)
            continue

        target_doc_ref = _transactions_ref(client, user_id).document(legacy_doc.id)
        target_doc_snapshot = target_doc_ref.get()
        if target_doc_snapshot.exists:
            skipped_transactions += 1
            logger.info("Skipping tx=%s for uid=%s (target already exists).", legacy_doc.id, user_id)
            if not keep_legacy and not dry_run:
                legacy_doc.reference.delete()
                deleted_legacy_transactions += 1
            continue

        touched_users.add(user_id)
        normalized = _normalize_transaction_payload(legacy_payload)
        if dry_run:
            migrated_transactions += 1
            continue

        _billing_root_ref(client, user_id).set(
            {"namespace": "ai_billing", "updatedAt": now},
            merge=True,
        )
        target_doc_ref.set(normalized, merge=True)
        migrated_transactions += 1
        if not keep_legacy:
            legacy_doc.reference.delete()
            deleted_legacy_transactions += 1

    logger.info("Migration summary:")
    logger.info("  users touched: %d", len(touched_users))
    logger.info("  snapshots migrated: %d", migrated_snapshots)
    logger.info("  snapshots skipped: %d", skipped_snapshots)
    logger.info("  tx migrated: %d", migrated_transactions)
    logger.info("  tx skipped: %d", skipped_transactions)
    logger.info("  tx missing userId: %d", missing_tx_user)
    if not keep_legacy:
        logger.info("  legacy snapshots deleted: %d", deleted_legacy_snapshots)
        logger.info("  legacy tx deleted: %d", deleted_legacy_transactions)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print migration summary without writing/deleting data.",
    )
    parser.add_argument(
        "--keep-legacy",
        action="store_true",
        help="Do not delete legacy top-level documents after successful migration.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    init_firebase()
    migrate(dry_run=args.dry_run, keep_legacy=args.keep_legacy)


if __name__ == "__main__":
    main()
