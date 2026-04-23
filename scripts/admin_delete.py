#!/usr/bin/env python3
"""Admin-triggered account deletion for DSAR (right-to-erasure) requests.

Permanently deletes all personal data for a given user from Fitaly systems:
  - All Firestore subcollections (meals, myMeals, chat_threads/messages,
    notifications, prefs, feedback, badges, streak, notif_meta)
  - Firebase Storage objects (avatars, meal photos)
  - Firestore billing subtree under users/{uid}/billing and top-level rate_limits/usernames
  - Firebase Auth user record

Usage
-----
By UID:
    python scripts/admin_delete.py --uid <firebase_uid>

By email (resolves to UID via Firebase Auth):
    python scripts/admin_delete.py --email user@example.com

Dry run (no data deleted, only lists what would be removed):
    python scripts/admin_delete.py --uid <uid> --dry-run

Skip confirmation prompt:
    python scripts/admin_delete.py --uid <uid> --yes

Full example for a DSAR:
    python scripts/admin_delete.py --email user@example.com --yes 2>&1 | tee dsar-deletion-log.txt

Environment
-----------
Requires the same .env / env vars used by the backend:
    FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL, FIREBASE_PRIVATE_KEY,
    FIREBASE_STORAGE_BUCKET, FIRESTORE_DATABASE_ID

Exit codes
----------
    0  All steps completed successfully
    1  Unexpected error (check stderr)
    2  User not found in Firebase Auth
    3  Aborted by operator
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── project root on sys.path ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from firebase_admin import auth as firebase_auth  # noqa: E402
from firebase_admin.exceptions import FirebaseError  # noqa: E402
from google.api_core.exceptions import GoogleAPICallError  # noqa: E402

from app.core.firestore_constants import (  # noqa: E402
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    BILLING_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BADGES_SUBCOLLECTION,
    RATE_LIMITS_COLLECTION,
    STREAK_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore, get_storage_bucket, init_firebase  # noqa: E402
from app.services.user_account_service import delete_account_data  # noqa: E402

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("admin_delete")


# ── helpers ──────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_uid(uid: str | None, email: str | None) -> str:
    """Return the Firebase UID, resolving from email if necessary."""
    if uid:
        return uid.strip()
    if not email:
        raise ValueError("Either --uid or --email must be provided.")
    try:
        user_record = firebase_auth.get_user_by_email(email.strip())
        logger.info("Resolved email %s → UID %s", email, user_record.uid)
        return user_record.uid
    except firebase_auth.UserNotFoundError:
        logger.error("No Firebase Auth user found for email: %s", email)
        sys.exit(2)


def _confirm(uid: str, email_hint: str | None) -> None:
    """Interactive confirmation gate."""
    target = f"UID={uid}" + (f" / email={email_hint}" if email_hint else "")
    print()
    print("=" * 60)
    print("  PERMANENT ACCOUNT DELETION — THIS CANNOT BE UNDONE")
    print("=" * 60)
    print(f"  Target user : {target}")
    print("  Data deleted: Firestore docs, Storage files, Auth record")
    print("=" * 60)
    answer = input("Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        logger.warning("Aborted by operator.")
        sys.exit(3)


def _count_collection(user_ref: object, name: str) -> int:
    """Return document count for a subcollection (for dry-run reporting)."""
    ref = user_ref  # type: ignore[assignment]
    return sum(1 for _ in ref.collection(name).stream())  # type: ignore[union-attr]


def _dry_run_report(uid: str) -> None:
    """Print a summary of what *would* be deleted without touching anything."""
    client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(uid)
    snapshot = user_ref.get()

    print()
    print("── DRY RUN ─────────────────────────────────────────────")
    print(f"  UID : {uid}")
    if not snapshot.exists:
        print("  Firestore users/{uid}: NOT FOUND (already deleted?)")
    else:
        profile = snapshot.to_dict() or {}
        print(f"  email    : {profile.get('email', '(none)')}")
        print(f"  username : {profile.get('username', '(none)')}")
        print(f"  plan     : {profile.get('plan', '(none)')}")
        print(f"  createdAt: {profile.get('createdAt', '(none)')}")

    subcollections = [
        "meals", "myMeals", "chat_threads", "notifications",
        "prefs", "notif_meta", "feedback", "badges", "streak",
    ]
    print()
    print("  Subcollection document counts:")
    for name in subcollections:
        count = _count_collection(user_ref, name)
        print(f"    {name:<22} {count:>6} doc(s)")

    # User-owned billing docs
    billing_ref = user_ref.collection(BILLING_SUBCOLLECTION).document(BILLING_DOCUMENT_ID)
    billing_doc = billing_ref.get()
    credits_docs = list(billing_ref.collection(AI_CREDITS_SUBCOLLECTION).stream())
    tx_docs = list(billing_ref.collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION).stream())
    rl_doc = client.collection(RATE_LIMITS_COLLECTION).document(uid).get()
    print()
    print("  Billing documents:")
    print(
        f"    users/{uid}/billing/{BILLING_DOCUMENT_ID:<8} "
        f"{'EXISTS' if billing_doc.exists else 'not found'}"
    )
    print(f"    users/{uid}/billing/{BILLING_DOCUMENT_ID}/aiCredits      {len(credits_docs):>4} doc(s)")
    print(
        f"    users/{uid}/billing/{BILLING_DOCUMENT_ID}/aiCreditTransactions "
        f"{len(tx_docs):>4} doc(s)"
    )
    print()
    print("  Top-level documents:")
    print(f"    rate_limits/{uid:<24} {'EXISTS' if rl_doc.exists else 'not found'}")

    # Storage
    bucket = get_storage_bucket()
    prefixes = [f"avatars/{uid}/", f"meals/{uid}/", f"myMeals/{uid}/"]
    print()
    print("  Storage objects:")
    for prefix in prefixes:
        blobs = list(bucket.list_blobs(prefix=prefix))
        print(f"    {prefix:<40} {len(blobs):>4} object(s)")

    # Auth
    try:
        auth_user = firebase_auth.get_user(uid)
        print()
        print("  Firebase Auth user:")
        print(f"    email    : {auth_user.email}")
        print(f"    disabled : {auth_user.disabled}")
    except firebase_auth.UserNotFoundError:
        print()
        print("  Firebase Auth user: NOT FOUND (already deleted?)")

    print()
    print("  Nothing was deleted (dry run).")
    print("─" * 60)


# ── deletion steps ────────────────────────────────────────────────────────────

async def _delete_extra_subcollections(uid: str, dry_run: bool) -> None:
    """Delete subcollections not covered by delete_account_data (badges, streak)."""
    client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(uid)

    for name in (BADGES_SUBCOLLECTION, STREAK_SUBCOLLECTION):
        docs = list(user_ref.collection(name).stream())
        if not docs:
            logger.info("  %s/%s — empty, skipping", USERS_COLLECTION, name)
            continue
        if dry_run:
            logger.info("  [dry-run] would delete %d doc(s) from %s", len(docs), name)
            continue
        batch = client.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        logger.info("  Deleted %d doc(s) from subcollection '%s'", len(docs), name)


def _delete_top_level_docs(uid: str, dry_run: bool) -> None:
    """Delete users/{uid}/billing subtree and rate_limits/{uid}."""
    client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(uid)
    billing_ref = user_ref.collection(BILLING_SUBCOLLECTION).document(BILLING_DOCUMENT_ID)
    credits_documents = list(billing_ref.collection(AI_CREDITS_SUBCOLLECTION).stream())
    tx_documents = list(billing_ref.collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION).stream())
    billing_document = billing_ref.get()

    if dry_run:
        if credits_documents:
            logger.info(
                "  [dry-run] would delete %d doc(s) from users/%s/billing/%s/aiCredits",
                len(credits_documents),
                uid,
                BILLING_DOCUMENT_ID,
            )
        if tx_documents:
            logger.info(
                "  [dry-run] would delete %d doc(s) from users/%s/billing/%s/aiCreditTransactions",
                len(tx_documents),
                uid,
                BILLING_DOCUMENT_ID,
            )
        if billing_document.exists:
            logger.info("  [dry-run] would delete users/%s/billing/%s", uid, BILLING_DOCUMENT_ID)
    else:
        for document in credits_documents:
            document.reference.delete()
        for document in tx_documents:
            document.reference.delete()
        if billing_document.exists:
            billing_ref.delete()
            logger.info("  Deleted users/%s/billing/%s", uid, BILLING_DOCUMENT_ID)

    rate_limit_ref = client.collection(RATE_LIMITS_COLLECTION).document(uid)
    rate_limit_snapshot = rate_limit_ref.get()
    if not rate_limit_snapshot.exists:
        logger.info("  %s/%s — not found, skipping", RATE_LIMITS_COLLECTION, uid)
        return
    if dry_run:
        logger.info("  [dry-run] would delete %s/%s", RATE_LIMITS_COLLECTION, uid)
        return
    rate_limit_ref.delete()
    logger.info("  Deleted %s/%s", RATE_LIMITS_COLLECTION, uid)


def _delete_auth_user(uid: str, dry_run: bool) -> None:
    """Delete the Firebase Auth record."""
    try:
        firebase_auth.get_user(uid)
    except firebase_auth.UserNotFoundError:
        logger.info("  Firebase Auth user %s not found — already deleted?", uid)
        return
    if dry_run:
        logger.info("  [dry-run] would delete Firebase Auth user %s", uid)
        return
    firebase_auth.delete_user(uid)
    logger.info("  Deleted Firebase Auth user %s", uid)


# ── main ──────────────────────────────────────────────────────────────────────

async def run(uid: str, dry_run: bool) -> None:
    start = _utc_now()
    logger.info("Starting account deletion for UID=%s at %s", uid, start)

    if dry_run:
        _dry_run_report(uid)
        return

    # Step 1 — Firestore subcollections + Storage (existing service)
    logger.info("[1/4] Deleting Firestore subcollections + Storage assets...")
    try:
        await delete_account_data(uid)
    except Exception:
        logger.exception("delete_account_data() failed — aborting.")
        raise

    # Step 2 — Extra subcollections not covered by the service (badges, streak)
    logger.info("[2/4] Deleting extra subcollections (badges, streak)...")
    await _delete_extra_subcollections(uid, dry_run=False)

    # Step 3 — Billing subtree + top-level rate limits document
    logger.info("[3/4] Deleting billing and rate-limit Firestore documents...")
    _delete_top_level_docs(uid, dry_run=False)

    # Step 4 — Firebase Auth record
    logger.info("[4/4] Deleting Firebase Auth user record...")
    _delete_auth_user(uid, dry_run=False)

    end = _utc_now()
    logger.info("Account deletion complete. UID=%s  started=%s  ended=%s", uid, start, end)
    print()
    print("✓ Deletion complete. Update the DSAR register:")
    print("    compliance/templates/dsar-register.md")
    print(f"  Fields to fill: completed={end}, outcome=completed")
    print()
    print("Residual data not deleted by this script:")
    print("  • Sentry error events — purge via Sentry Dashboard → Users → Delete User Data")
    print("  • Railway logs — auto-expire within 30 days")
    print("  • RevenueCat purchase history — delete via RevenueCat Dashboard → Customers")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Admin account deletion for DSAR requests.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--uid", metavar="UID", help="Firebase UID of the user to delete.")
    id_group.add_argument("--email", metavar="EMAIL", help="Email address (resolves to UID).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be deleted without deleting anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Initialise Firebase before any service call
    try:
        init_firebase()
    except Exception:
        logger.exception("Failed to initialise Firebase. Check env vars.")
        sys.exit(1)

    uid = _resolve_uid(args.uid, args.email)

    if not args.dry_run and not args.yes:
        _confirm(uid, args.email)

    try:
        asyncio.run(run(uid, dry_run=args.dry_run))
    except (FirebaseError, GoogleAPICallError) as exc:
        logger.error("Firebase/Firestore error during deletion: %s", exc)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        logger.exception("Unexpected error during deletion.")
        sys.exit(1)


if __name__ == "__main__":
    main()
