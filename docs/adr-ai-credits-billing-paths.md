# ADR: AI Credits User-Owned Billing Paths

## Status
Accepted

## Date
2026-04-23

## Context
AI credits were stored in top-level Firestore collections (`ai_credits`, `ai_credit_transactions`) with `userId` duplicated in every document. Ownership was implicit in payload fields instead of document paths.

## Decision
Move credits snapshot + ledger to user-owned billing paths.

Canonical storage:
- `users/{uid}/billing/main/aiCredits/current` (current snapshot)
- `users/{uid}/billing/main/aiCreditTransactions/{txId}` (ledger entries)

Notes:
- Snapshot and ledger domain model stays unchanged.
- Runtime code reads/writes only canonical billing paths.
- Legacy top-level collections are migrated and removed (no runtime fallback).

## Consequences
- Firestore path itself encodes ownership.
- Redundant ownership in storage (`userId`) is no longer required in persisted docs.
- Billing subtree must be included in account deletion flows.
- Migration is idempotent by document id for ledger entries.
