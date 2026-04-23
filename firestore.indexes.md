# Firestore Index Audit (fitaly-backend)

This document maps `firestore.indexes.json` to active backend query shapes after the Firestore v2 user-owned refactor.

## Composite indexes in use

1. `meals` (`COLLECTION_GROUP`) — `loggedAt DESC`, `__name__ DESC`
- Used by: meal-domain cross-user reads with descending event-time pagination.

2. `meals` (`COLLECTION_GROUP`) — `totals.carbs ASC`, `loggedAt DESC`, `__name__ DESC`
3. `meals` (`COLLECTION_GROUP`) — `totals.fat ASC`, `loggedAt DESC`, `__name__ DESC`
4. `meals` (`COLLECTION_GROUP`) — `totals.kcal ASC`, `loggedAt DESC`, `__name__ DESC`
5. `meals` (`COLLECTION_GROUP`) — `totals.protein ASC`, `loggedAt DESC`, `__name__ DESC`
- Used by: history-style nutrient filters with deterministic `loggedAt` + document-id ordering.

6. `meals` (`COLLECTION`) — `deleted ASC`, `dayKey ASC`, `__name__ ASC`
- Used by: bounded user-owned day-window reads in `habit_signal_service` and `nutrition_state_service`.

7. `meals` (`COLLECTION`) — `deleted ASC`, `loggedAt DESC`, `__name__ DESC`
- Used by: `meal_service.list_history` canonical cursor pagination.

8. `meals` (`COLLECTION`) — `deleted ASC`, `loggedAt ASC`, `__name__ ASC`
- Used by: bounded user-owned `loggedAt` range reads with `deleted == false` in signal/state services.

9. `telemetry_events` (`COLLECTION`) — `userHash ASC`, `name ASC`, `ts ASC`
- Used by: `telemetry_service.count_events_for_user`.

10. `telemetry_events` (`COLLECTION`) — `userHash ASC`, `ts ASC`
- Used by: `telemetry_service.get_daily_summary` and `telemetry_service.get_smart_reminder_summary`.

11. `ai_runs` (`COLLECTION`) — `userId ASC`, `createdAt DESC`, `__name__ DESC`
- Used by: `app/infra/firestore/repositories/ai_run_repository.py` (`list_recent_for_user`).

## Query shapes that do not need new composites

- `users/{uid}/billing/main/aiCredits/current`: direct document read/write by id (no composite index).
- `users/{uid}/billing/main/aiCreditTransactions/{txId}` history:
  `order_by("createdAt", DESC)` only (single-field index sufficient).
- `users/{uid}/chat_threads` and nested `messages`:
  single-field ordering/range on one field (`updatedAt` / `createdAt`) only.

## Legacy note

Any leftover legacy `timestamp`-based meal query paths should be removed during cleanup phase; no new indexes are added for those legacy-only shapes.
