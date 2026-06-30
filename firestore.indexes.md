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

7. `meals` (`COLLECTION`) — `deleted ASC`, `dayKey DESC`, `loggedAt DESC`, `__name__ DESC`
- Used by: `meal_service.list_history` canonical dayKey-first history pagination.

8. `meals` (`COLLECTION`) — `deleted ASC`, `loggedAt DESC`, `__name__ DESC`
- Used by: legacy internal recent-activity `loggedAt` window reads.

9. `meals` (`COLLECTION`) — `deleted ASC`, `loggedAt ASC`, `__name__ ASC`
- Used by: bounded user-owned `loggedAt` range reads with `deleted == false` in signal/state services.

10. `ingredientProducts` (`COLLECTION`) — `updatedAt ASC`, `ingredientProductId ASC`
- Used by: `/users/me/ingredient-products/pull` current-user Product/Ingredient
  synchronization with deterministic compound `updatedAt|ingredientProductId`
  pagination.

11. `telemetry_events` (`COLLECTION`) — `userHash ASC`, `name ASC`, `ts ASC`
- Used by: `telemetry_service.count_events_for_user`.

12. `telemetry_events` (`COLLECTION`) — `userHash ASC`, `ts ASC`
- Used by: `telemetry_service.get_daily_summary` and `telemetry_service.get_smart_reminder_summary`.

13. `ai_runs` (`COLLECTION`) — `userId ASC`, `createdAt DESC`, `__name__ DESC`
- Used by: `app/infra/firestore/repositories/ai_run_repository.py` (`list_recent_for_user`).

## Deployment verification

Production Firestore must have the checked-in indexes deployed. Merging
`firestore.indexes.json` is not enough for production query readiness.

Deploy indexes with the Firebase project selected for the target environment:

```bash
firebase deploy --only firestore:indexes
```

For Launch 1.0 Sentry issue `FITALY-1D`, verify that production includes these
user-owned `meals` collection indexes before changing NutritionState logic:

- `deleted ASC`, `dayKey ASC`, `__name__ ASC`
- `deleted ASC`, `loggedAt ASC`, `__name__ ASC`

Those shapes are used by `nutrition_state_service._load_bounded_meals` and
`habit_signal_service._load_recent_meals`. Missing production deployment of
either index can surface through NutritionState consumers such as coach insights
and smart reminder decisions.

## Query shapes that do not need new composites

- `users/{uid}/billing/main/aiCredits/current`: direct document read/write by id (no composite index).
- `users/{uid}/billing/main/aiCreditTransactions/{txId}` history:
  `order_by("createdAt", DESC)` only (single-field index sufficient).
- `users/{uid}/chat_threads` and nested `messages`:
  single-field ordering/range on one field (`updatedAt` / `createdAt`) only.
- `/users/me/meals/changes` and `/users/me/meal-templates/changes` read user-scoped
  subcollections (`users/{uid}/meals`, `users/{uid}/mealTemplates`) ordered by
  `updatedAt ASC` plus document id (`__name__ ASC`) for deterministic cursor
  pagination. No collection-group or cross-user composite is required; the
  active bounds come from route-level `limit` validation and service-level
  `query.limit(limit_count)`.

## Legacy note

Any leftover legacy `timestamp`-based meal query paths should be removed during cleanup phase; no new indexes are added for those legacy-only shapes.
