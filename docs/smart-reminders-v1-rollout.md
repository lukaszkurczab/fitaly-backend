# Smart Reminders v1 — Rollout Runbook

## 1. Prerequisites

### Backend flags (all required)

| Flag | Required value | Purpose |
|---|---|---|
| `STATE_ENABLED` | `true` | Nutrition state endpoint — reminder input source |
| `HABITS_ENABLED` | `true` | Habit signals embedded in state — decision quality |
| `SMART_REMINDERS_ENABLED` | `true` | Decision endpoint gate |
| `TELEMETRY_ENABLED` | `true` | Accept mobile telemetry for observability |

### Mobile flags (all required)

| Flag | Required value | Purpose |
|---|---|---|
| `EXPO_PUBLIC_ENABLE_V2_STATE` | `true` | State data layer |
| `EXPO_PUBLIC_ENABLE_SMART_REMINDERS` | `true` | Smart Reminders data layer + scheduling |
| `EXPO_PUBLIC_ENABLE_TELEMETRY` | `true` | Emit telemetry events |

### Flag dependency order

```
STATE_ENABLED  ─┐
HABITS_ENABLED ─┤─→ SMART_REMINDERS_ENABLED
                └─→ (both must be true before enabling reminders)
```

Enable in order: `STATE_ENABLED` → `HABITS_ENABLED` → `SMART_REMINDERS_ENABLED`.
Disable in reverse order.

### Infrastructure

- Firestore read/write access (state, preferences, `reminderDailyStats` collection)
- No external AI dependency in the decision path
- No push notification infrastructure required (backend is decision-only)

## 2. What the endpoint does

```
GET /api/v2/users/me/reminders/decision?day=YYYY-MM-DD&tzOffsetMin=<int>
```

- Computes a `ReminderDecision` for the given user and day
- Returns `send`, `suppress`, or `noop` with deterministic reason codes
- Does NOT schedule, send, or deliver notifications
- Mobile is the sole consumer; it schedules local notifications based on `send` decisions

### Query parameters

| Param | Required | Validation | Purpose |
|---|---|---|---|
| `day` | No | `YYYY-MM-DD`, 10 chars | Day key for decision (defaults to today UTC) |
| `tzOffsetMin` | No | `int`, `[-840, 840]` | Client timezone offset (minutes east of UTC) |

### Timezone resolution precedence

1. **Client `tzOffsetMin`** — if provided in query param
2. **Meal heuristic** — `tzOffsetMin` or `loggedAtLocalMin` from latest meal
3. **UTC fallback** — when no offset source is available

## 3. Expected HTTP responses

| Status | When | Meaning |
|---|---|---|
| `200` | Decision computed | Valid `ReminderDecision` payload |
| `400` | Invalid `day` format | Client input error |
| `422` | `tzOffsetMin` out of range | FastAPI validation rejection |
| `500` | Firestore failure, contract violation | Backend bug — investigate |
| `503` | Feature disabled, foundation unavailable | Expected during rollback |

## 4. Decision semantics

| Decision | Meaning | `kind` | `scheduledAtUtc` |
|---|---|---|---|
| `send` | Schedule a reminder | present | present |
| `suppress` | Reminder blocked by hard constraint | `null` | `null` |
| `noop` | No credible reminder opportunity | `null` | `null` |

### Suppression reasons (hard constraints)

- `reminders_disabled` — user turned off smart reminders
- `quiet_hours` — current local time is in quiet hours
- `frequency_cap_reached` — daily send limit (3) exceeded
- `already_logged_recently` — meal logged in last 90 min
- `recent_activity_detected` — meal edited/backfilled recently

### Noop reasons

- `insufficient_signal` — not enough habit data to make a decision
- `day_already_complete` — day is fully logged

## 5. Verification steps after deploy

### 5a. Endpoint health

```bash
# Should return 200 with valid ReminderDecision
curl -H "Authorization: Bearer <token>" \
  "https://<host>/api/v2/users/me/reminders/decision?day=2026-03-20&tzOffsetMin=60"

# Verify response shape
# - dayKey matches request
# - decision is one of: send, suppress, noop
# - reasonCodes array is non-empty
# - computedAt and validUntil are canonical UTC (YYYY-MM-DDTHH:MM:SSZ)
# - confidence is 0.0–1.0
```

### 5b. Suppression paths

```bash
# Quiet hours (request during night hours for the user's timezone)
# Expected: decision=suppress, reasonCodes=["quiet_hours"]

# After 3 send decisions for same user+day
# Expected: decision=suppress, reasonCodes=["frequency_cap_reached"]
```

### 5c. Failure paths

```bash
# With SMART_REMINDERS_ENABLED=false → 503
# With invalid day format → 400
# With tzOffsetMin=9999 → 422
```

### 5d. Backend structured log

After every successful decision computation, the backend emits:

```
INFO  reminder.decision.computed
  user_id=<uid>
  day_key=2026-03-20
  decision=send|suppress|noop
  kind=log_next_meal|null
  reason_codes=[...]
  confidence=0.84
  tz_offset_min=60|null
```

Verify this log appears in production log stream after deploy.
Filter: `reminder.decision.computed` at INFO level.

### 5e. Mobile telemetry (via TELEMETRY_ENABLED)

After mobile reconcile, these events should appear in telemetry ingest:

| Event | When | Key props |
|---|---|---|
| `smart_reminder_scheduled` | `decision=send` + successfully scheduled | `reminderKind`, `confidenceBucket`, `scheduledWindow` |
| `smart_reminder_suppressed` | `decision=suppress` | `suppressionReason`, `confidenceBucket` |
| `smart_reminder_noop` | `decision=noop` | `noopReason`, `confidenceBucket` |
| `smart_reminder_decision_failed` | Backend unreachable or invalid payload | `failureReason` |
| `smart_reminder_schedule_failed` | Local scheduling error | `failureReason`, `reminderKind` |

### 5f. Strict failure policy (mobile)

When Smart Reminders are enabled on mobile, legacy `meal_reminder` and `day_fill` scheduling
is suppressed unconditionally. Decision failure (service_unavailable, invalid_payload, crash)
results in **no notification**, not a silent fallback to legacy scheduling.

Verify: with backend down and smart reminders enabled, no legacy meal/day reminders fire.

## 6. Rollback

### Primary: disable Smart Reminders only

```env
SMART_REMINDERS_ENABLED=false
```

Effect:
- Backend returns `503` for all decision requests
- Mobile receives `service_unavailable` → cancels any scheduled smart reminders
- Mobile strict failure policy means no legacy fallback either
- Other notification types (calorie_goal, system) unaffected
- No data loss — `reminderDailyStats` collection remains but is inert

### Secondary: disable foundations

```env
HABITS_ENABLED=false
# or
STATE_ENABLED=false
```

Effect:
- Reminder decision returns `503` (foundation unavailable)
- Also affects Coach Insights and state endpoint — broader impact

### Emergency: mobile-side kill

```env
EXPO_PUBLIC_ENABLE_SMART_REMINDERS=false
```

Effect:
- Mobile stops fetching decisions entirely
- `getReminderDecision` returns `disabled` status without network call
- Legacy meal/day scheduling resumes (feature is off, not failing)

### Rollback verification

After rollback, confirm:
1. `GET /api/v2/users/me/reminders/decision` → `503`
2. No new `reminder.decision.computed` logs in backend
3. No new `smart_reminder_*` events in telemetry
4. Existing scheduled notifications still fire (they're local)

## 7. Firestore collections

| Collection | Path | Purpose | Cleanup needed on rollback? |
|---|---|---|---|
| `reminderDailyStats` | `users/{uid}/reminderDailyStats/{dayKey}` | Daily send count for frequency cap | No — inert when feature is off |

## 8. Known limitations in v1

- **No per-user rollout** — `SMART_REMINDERS_ENABLED` is global, not per-user
- **No IANA timezone** — uses fixed offset, not named timezone; DST transitions resolve on next reconcile
- **No delivery confirmation** — backend counts `send` decisions, not actual deliveries
- **No staleness guard** — if mobile caches a decision and never re-reconciles, the decision stays
- **Frequency cap is per-decision, not per-delivery** — 3 `send` decisions/day regardless of actual notification delivery
