# Notifications Legacy Sunset Note

## Scope

This note tracks controlled cleanup of legacy notifications/reminders residue in backend repo.

## Canonical status

1. Canonical reminder production path is `GET /api/v2/users/me/reminders/decision`.
2. Backend does not schedule/deliver notifications; mobile owns local scheduling/cancellation.
3. Notification preferences endpoint remains active (`/api/v1/users/me/notifications/preferences`).

## Compatibility-only surfaces kept for now

1. `/api/v1/users/me/notifications/reconcile-plan`
2. `/api/v1/users/me/notifications` (list/upsert)
3. `/api/v1/users/me/notifications/{notificationId}/delete`
4. `notification_plan_service` + `notification_plan` schema used by (1)

All of the above are deprecated and labeled compatibility-only.

## Why kept

1. Backward compatibility with older mobile clients not yet sunset.
2. Controlled blast radius: canonical path is already isolated and does not depend on these routes.

## Removal criteria

1. Confirm no active client traffic on compatibility-only `/notifications` endpoints.
2. Remove routes, schema, and service together in one backend-only cleanup PR.
3. Remove related tests that only protect compatibility routes.
