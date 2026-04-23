# Firestore v2 Backfill and Cutover Plan

## Scope

Canonical v2 backfill covers:

1. Legacy credits snapshot:
- `ai_credits/{uid}`
- -> `users/{uid}/billing/main/aiCredits/current`

2. Legacy credits ledger:
- `ai_credit_transactions/{txId}`
- -> `users/{uid}/billing/main/aiCreditTransactions/{txId}`

3. Meals normalization in place:
- `users/{uid}/meals/{mealId}`
- enforce canonical shape:
  - `id = document id` (document id is source of truth)
  - `timestamp -> loggedAt`
  - remove redundant legacy fields
  - remove `photoLocalPath`
  - canonical `imageRef`

## Migration Script

Script:
- `scripts/migrate_firestore_v2_backfill.py`

Safety features:
- idempotent updates
- `--dry-run`
- resumable state (`--state-file`, `--reset-state`)
- read batch size (`--batch-size`)
- write batch size (`--write-batch-size`)
- retry policy (`--max-retries`)
- optional legacy deletion (`--delete-legacy`)

## Recommended Execution

1. Dry-run first:

```bash
python scripts/migrate_firestore_v2_backfill.py --dry-run --reset-state
```

2. Real run (without deleting legacy yet):

```bash
python scripts/migrate_firestore_v2_backfill.py --yes --reset-state
```

3. Legacy cleanup run after read switch:

```bash
python scripts/migrate_firestore_v2_backfill.py --yes --delete-legacy
```

## Example Dry-Run Report

```json
{
  "startedAt": "2026-04-23T15:18:10Z",
  "endedAt": "2026-04-23T15:19:02Z",
  "dryRun": true,
  "deleteLegacy": false,
  "batchSize": 200,
  "writeBatchSize": 200,
  "maxRetries": 5,
  "stateFile": ".migration_state/firestore_v2_backfill_state.json",
  "totals": {
    "scanned": 12680,
    "migrated": 9211,
    "skipped": 3448,
    "manualIntervention": 21,
    "legacyDeleted": 0
  },
  "phases": {
    "aiCreditsSnapshot": {
      "scanned": 1450,
      "migrated": 1390,
      "skipped": 60,
      "manualIntervention": 0,
      "legacyDeleted": 0
    },
    "aiCreditTransactions": {
      "scanned": 4320,
      "migrated": 4278,
      "skipped": 31,
      "manualIntervention": 11,
      "legacyDeleted": 0
    },
    "meals": {
      "scanned": 6910,
      "migrated": 3543,
      "skipped": 3357,
      "manualIntervention": 10,
      "legacyDeleted": 0
    }
  },
  "manualInterventions": [
    {
      "phase": "aiCreditTransactions",
      "documentPath": "ai_credit_transactions/tx_foo",
      "reason": "Missing userId in legacy transaction payload."
    }
  ]
}
```

## Cutover Sequence

### 1) Dual Write (temporary)

- Billing writes:
  - write canonical billing path
  - optionally mirror legacy only during migration window
- Meals writes:
  - write canonical shape only
  - avoid introducing new legacy fields

### 2) Dual Read (temporary)

- Read canonical first.
- Use legacy read fallback only until backfill + validation complete.

### 3) Read Switch

- Disable legacy read fallback after backfill report reaches acceptable residual manual bucket.
- Keep request correlation and operational logs unchanged.

### 4) Cleanup

- Run migration with `--delete-legacy` for old credits collections.
- Remove legacy read/write code paths from runtime services.
- Remove legacy indexes and related compatibility tests.

## Completion Criteria

- No runtime dependency on:
  - `ai_credits`
  - `ai_credit_transactions`
  - meal legacy fields (`timestamp`, `photoLocalPath`, `mealId/cloudId/userUid` in storage docs)
- Migration report manual bucket resolved or explicitly accepted.
- Post-cutover smoke tests pass for:
  - credits read/deduct/reset
  - meal add/read/update/delete
