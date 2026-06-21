#!/usr/bin/env python3
"""Replay pending meal side-effect outbox rows for one user.

Usage:
    python scripts/reconcile_meal_effect_outbox.py --uid <firebase_uid> --yes

The script uses the same Firebase environment as the backend. It does not touch
primary meal documents directly; it only asks the backend service to process
pending meal-effect outbox rows for the selected user.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.meal_service import reconcile_pending_meal_effects  # noqa: E402

logger = logging.getLogger("reconcile_meal_effect_outbox")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay pending meal side-effect outbox rows for one user.",
    )
    parser.add_argument("--uid", required=True, help="Firebase UID to reconcile.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum pending events to process, capped by service policy.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation.",
    )
    return parser.parse_args()


def _confirm(uid: str) -> None:
    print(f"Replay pending meal side effects for UID={uid}")
    answer = input("Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit(3)


async def _run() -> int:
    args = _parse_args()
    uid = str(args.uid or "").strip()
    if not uid:
        raise SystemExit("--uid must not be blank")
    if not args.yes:
        _confirm(uid)

    result = await reconcile_pending_meal_effects(uid, limit_count=args.limit)
    print(json.dumps(result, sort_keys=True))
    if result.get("failed", 0) or result.get("status_update_failed", 0):
        logger.warning("Meal effect reconciliation completed with pending failures.")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(_run()))
