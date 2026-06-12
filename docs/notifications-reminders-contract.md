# Notification and Reminder Contract

## Canonical Surfaces

Fitaly is pre-launch. There is no backward-compatibility requirement for legacy notification plans, legacy notification definition forms, reconcile endpoints, or rollout-era Smart Reminder documents.

Backend owns two active surfaces:

1. `GET /api/v1/users/me/notifications/preferences`
2. `POST /api/v1/users/me/notifications/preferences`
3. `GET /api/v2/users/me/reminders/decision?day=YYYY-MM-DD&tzOffsetMin=<int>`

Mobile owns local notification permission checks, Expo scheduling, cancellation, diagnostics, and delivery behavior.

## Notification Preferences

Preferences live in Firestore at:

`users/{userId}/prefs/global.notifications`

Supported fields:

- `smartRemindersEnabled?: boolean`
- `motivationEnabled?: boolean`
- `statsEnabled?: boolean`
- `weekdays0to6?: number[]`
- `daysAhead?: number`
- `quietHours?: { startHour: number; endHour: number }`

The preferences endpoint is the only backend-owned settings surface for notification-related toggles.

## Smart Reminder Decision

`GET /api/v2/users/me/reminders/decision` returns a `ReminderDecision`.

Inputs:

- nutrition state for the requested day
- habit signals embedded in nutrition state
- notification preferences from `prefs/global.notifications`
- recent meal/activity suppression signals
- daily send-decision count
- client timezone offset when supplied

The backend does not create notification schedules. `scheduledAtUtc` is a decision output for mobile to consume when it schedules a one-shot local notification.

Decision values:

- `send`: mobile may schedule a local reminder at `scheduledAtUtc`.
- `suppress`: a reminder opportunity existed but was blocked by preferences, quiet hours, frequency cap, or recent activity.
- `noop`: backend computed successfully and found no useful reminder opportunity.

Failure values are explicit HTTP errors, not synthetic `noop` decisions:

- `503`: Smart Reminders, nutrition state, or habit foundations are unavailable.
- `500`: backend computation or persistence failed.
- `400`: client supplied an invalid day key.

## Smart Reminder Telemetry Allowlist

Smart Reminder telemetry must stay categorical and bounded. Do not send freeform
copy, raw reason text, user-authored content, or sensitive profile data.

Canonical event names:

- `smart_reminder_suppressed`
- `smart_reminder_scheduled`
- `smart_reminder_noop`
- `smart_reminder_decision_failed`
- `smart_reminder_schedule_failed`

Canonical props by event:

| Event | Allowed props |
| --- | --- |
| `smart_reminder_suppressed` | `decision`, `suppressionReason`, `confidenceBucket` |
| `smart_reminder_scheduled` | `reminderKind`, `decision`, `confidenceBucket`, `scheduledWindow` |
| `smart_reminder_noop` | `decision`, `noopReason`, `confidenceBucket` |
| `smart_reminder_decision_failed` | `failureReason` |
| `smart_reminder_schedule_failed` | `reminderKind`, `decision`, `confidenceBucket`, `failureReason` |

The paired contract fixture is
`tests/contract_fixtures/smart_reminder_telemetry.json`; backend/mobile copies
must stay identical.

## Kill Switches

`SMART_REMINDERS_ENABLED=false` disables the reminder decision surface and returns `503`. It must not fall back to legacy notification plans.

Frontend `EXPO_PUBLIC_ENABLE_SMART_REMINDERS=false` keeps mobile Smart Reminder scheduling inactive. It must not call any legacy plan or reconcile endpoint.

## Removed Legacy Surfaces

The following backend surfaces are removed:

- `POST /api/v1/users/me/notifications/reconcile-plan`
- `GET /api/v1/users/me/notifications`
- `POST /api/v1/users/me/notifications`
- `POST /api/v1/users/me/notifications/{notificationId}/delete`

The removed Firestore shape `users/{userId}/notifications/*` is not migrated because Fitaly has no production users and canonical preferences already live under `prefs/global.notifications`.

## Frontend Contract

Frontend should use:

- `src/services/notifications/notificationsRepository.ts` for notification preferences.
- `src/services/reminders/reminderService.ts` for backend reminder decisions.
- `src/services/reminders/reminderScheduling.ts` for local one-shot Smart Reminder scheduling.
- `src/services/notifications/system.ts` for local system notification scheduling.

Frontend should not call backend notification plan/reconcile/CRUD endpoints.
