# Backend Hardening Local Evidence Plan

Status: PR1 complete; PR2 next
Last updated: 2026-06-03

## Objective

Build backend hardening evidence locally before using Railway, production
Firebase, smoke Firebase, OpenAI, or RevenueCat live resources.

The goal is the backend equivalent of FE screenshot evidence: every backend
surface should have a visible, repeatable artifact that shows request,
response, status, headers, request ID, data-state assumptions, and redaction.

## Confirmed Current Reality

- Backend stack: FastAPI, Firebase Admin SDK, Firestore, OpenAI, Sentry.
- Public API split: `/api/v1` current surface, `/api/v2` newer backend-owned
  surfaces.
- Local cheap runner exists: `scripts/run-backend-evidence.py`. PR0 artifacts
  include sanitized request, expected statuses, expected app-facing behavior,
  actual response, and a contract compatibility verdict.
- Manual request replay exists: `requests/local.http` and should stay aligned
  with the cheap runner checks.
- Local Firebase config baseline exists: `firebase.json`, `firestore.rules`,
  `storage.rules`, `.firebaserc`.
- Firebase Admin SDK bypasses Firestore rules. Emulator evidence proves backend
  behavior and document shapes; rules enforcement needs separate client/rules
  tests when mobile writes directly to Firebase.
- Bruno CLI is intentionally not a dependency because tested versions introduced
  high-severity audit findings. `.http` files are the manual alternative.

## Evidence Levels

Use the lowest level that proves the claim.

| Level | Name | Cost | Purpose | Artifact |
| --- | --- | --- | --- | --- |
| L0 | Static inventory | none | Prove every route is known and classified | endpoint inventory, hardening matrix |
| L1 | Local HTTP no-state | low | Middleware, CORS-ish headers, auth rejection, disabled/no-op states | request/response JSON |
| L2 | In-process service/fake repo | low | Logic branches without Firebase/OpenAI cost | pytest evidence, fixture snapshots |
| L3 | Firebase emulator state | medium | Firestore/Auth/Storage document shape and isolation | seeded emulator run artifacts |
| L4 | Mobile Maestro against local backend | medium | FE/BE integration through app workflows | Maestro screenshots/logs + backend artifacts |
| L5 | Smoke/Railway | high | Final release rehearsal only | smoke flow evidence |

Default hardening should stop at L0-L3 unless a surface explicitly needs mobile
interaction or live provider behavior.

## Evidence Lanes

Every endpoint should be assigned one or more lanes:

- `route_inventory`: endpoint exists in generated route/OpenAPI inventory.
- `auth_boundary`: missing, invalid, and cross-user auth behavior is explicit.
- `malformed_payload`: invalid input returns bounded validation errors.
- `valid_payload`: happy path or safe no-op request is covered locally.
- `user_isolation`: User A cannot read/write/delete User B data.
- `emulator_state`: Firestore/Auth/Storage state is seeded and inspected locally.
- `idempotency_or_replay`: duplicate request, webhook event, or retry behavior is covered.
- `kill_switch`: disabled feature state is explicit and has no hidden fallback.
- `redacted_observability`: logs/artifacts redact tokens, secrets, PII, and user-authored content.
- `mobile_e2e_local`: mobile flow runs against local backend via `E2E_API_BASE_URL`.
- `manual_only`: endpoint intentionally excluded from automated cheap checks.

## PR0 Active Workflow

For each backend surface, use this loop before expanding coverage:

1. Verify docs against code, tests, schemas, and mobile contract usage.
2. Check step completeness: route exists, app contract is known, and an evidence
   lane exists or is explicitly missing.
3. Run the local call.
4. Compare expected status and app-facing behavior against the actual response.
5. Classify mismatches as backend bug, mobile contract drift, stale docs, or
   incomplete test harness.
6. Patch the smallest correct layer, rerun focused checks, then continue.

Current PR0 coverage is intentionally limited to L0/L1:

- endpoint inventory,
- public foundation endpoints,
- representative missing-auth boundaries,
- telemetry disabled/no-op behavior,
- RevenueCat invalid-secret/unconfigured-secret rejection.

Do not expand to Firebase emulator state, OpenAI provider behavior, mobile
Maestro, or smoke/Railway until PR0 evidence is green.

## Stage Status

| Stage | Status | Done | Next gate |
| --- | --- | --- | --- |
| PR0 - Documentation And Inventory Baseline | Done | Source docs and manual requests are aligned; latest local evidence run `evidence/runs/local-public-20260603T200120Z` captured 54 endpoints and 7/7 default checks passed; `pytest -q`, compileall, Ruff, and Pyright passed. | None. |
| PR1 - Auth Boundary And Replay Privacy | Done | Missing-auth and malformed-token evidence is covered; AI idempotency-key auth checks are covered; same-key cross-bearer replay is blocked by bearer-token digest namespacing; latest local evidence run `evidence/runs/local-public-20260603T201127Z` passed 12/12 checks; `pytest -q`, compileall, Ruff, and Pyright passed. | None. |
| PR2 - Identity, Export, Delete | Done | Backend export/delete service coverage now includes chat memory, AI run telemetry, billing credit idempotency state, storage cleanup, Auth emulator JWT handling, Firebase emulator evidence run `evidence/runs/pr2-emulator-20260603T212805Z` passed 4/4 checks, and local iOS Maestro account-delete evidence passed 1/1 against `http://localhost:8000`. | None. |
| PR3 - Core Meal Loop And Sync | Not started | None. | Start only after PR2 is Go. |
| PR4 - Billing, Credits, RevenueCat | Not started | None. | Start only after PR3 is Go or if explicitly reprioritized as release-critical. |
| PR5 - AI Cost And Privacy Surfaces | Not started | None. | Start only after PR4 is Go or if explicitly reprioritized as release-critical. |
| PR6 - Retention Surfaces | Not started | None. | Start only after PR5 is Go or if explicitly reprioritized as release-critical. |

## PR0 Go/No-Go Criteria

PR0 is **Go** only when all of these are true:

- This document is current with the FastAPI route inventory and risk lanes.
- `requests/local.http` matches the default local evidence runner scenarios.
- `python scripts/run-backend-evidence.py --base-url http://127.0.0.1:8000`
  writes sanitized artifacts with `failed: 0`.
- Generated `endpoint-inventory.json` includes every FastAPI route and assigns
  each route a risk surface and evidence lane.
- Generated `hardening-matrix.json` has no unclassified endpoint.
- Documentation links in `README.md`, `evidence/README.md`, and this document do
  not point to removed or missing hardening docs.
- PR0 does not require Railway, live Firebase, live OpenAI, live RevenueCat, or
  mobile Maestro to pass.
- PR0 does not change backend runtime behavior.
- `pytest -q`, `python -m compileall app`, `ruff check .`, and
  `./.venv/bin/pyright` pass from the backend repo.

PR0 is **No-Go** when any of these are true:

- A real route is missing from inventory or cannot be classified.
- Any evidence artifact exposes secrets, bearer tokens, private keys, emails, or
  user-authored private content outside explicit test fixture payloads.
- The runner needs live Railway/Firebase/OpenAI/RevenueCat to pass.
- Manual requests and automated evidence disagree about expected app behavior.
- Any required verification command fails or is not run.
- Source-of-truth docs still contradict current code, tests, or generated
  artifacts.

Final backend release Go/No-Go is stricter than PR0. PR0 only decides whether it
is safe to continue to PR1 local auth/replay hardening; it is not production
readiness approval.

## PR1 Go/No-Go Criteria

PR1 is **Go** only when all of these are true:

- Missing-auth evidence covers representative protected groups, including
  profile, credits, nutrition state, and AI endpoints.
- Malformed bearer tokens return `401 Invalid authentication credentials`
  without requiring Firebase verification.
- AI endpoints remain auth-protected when `X-Idempotency-Key` is present.
- Repeated unauthenticated AI requests with the same idempotency key are not
  replayed from cache.
- AI idempotency cache entries are namespaced by bearer-token digest, so the
  same idempotency key cannot replay a response across different bearer tokens.
- Raw bearer tokens are not stored in idempotency cache keys or evidence
  artifacts.
- CORS allows `X-Idempotency-Key`.
- Local HTTP evidence, focused auth/idempotency tests, and the full backend
  quality gate pass without live Firebase/OpenAI/RevenueCat.

PR1 is **No-Go** when any of these are true:

- An idempotency key can bypass auth or convert an unauthenticated request into
  a replay.
- The same idempotency key can replay a cached AI response across different
  bearer tokens.
- Malformed bearer-token handling depends on live Firebase availability.
- Raw bearer tokens appear in cache keys or evidence artifacts.
- Any required verification command fails or is not run.

## Current Endpoint Inventory

Generated from the FastAPI app with `EAGER_FIREBASE_INIT=false`.

### Public Foundation

| Method | Path | Required lanes |
| --- | --- | --- |
| GET | `/api/v1/health` | route_inventory, valid_payload |
| GET | `/api/v1/version` | route_inventory, valid_payload |
| GET | `/api/v1/health/firestore` | route_inventory, manual_only, emulator_state |

### Client Error / Telemetry / Observability

| Method | Path | Required lanes |
| --- | --- | --- |
| POST | `/api/v1/logs/error` | route_inventory, malformed_payload, valid_payload, redacted_observability, rate_limit |
| POST | `/api/v2/telemetry/events/batch` | route_inventory, malformed_payload, valid_payload, kill_switch, redacted_observability, rate_limit |
| GET | `/api/v2/telemetry/events/summary/daily` | route_inventory, auth_boundary, user_isolation, emulator_state, kill_switch |
| GET | `/api/v2/telemetry/smart-reminders/summary` | route_inventory, auth_boundary, user_isolation, emulator_state, kill_switch |

### AI And Credits

| Method | Path | Required lanes |
| --- | --- | --- |
| POST | `/api/v1/ai/photo/analyze` | route_inventory, auth_boundary, malformed_payload, valid_payload, idempotency_or_replay, kill_switch, redacted_observability |
| POST | `/api/v1/ai/text-meal/analyze` | route_inventory, auth_boundary, malformed_payload, valid_payload, idempotency_or_replay, kill_switch, redacted_observability |
| GET | `/api/v1/ai/credits` | route_inventory, auth_boundary, user_isolation, emulator_state |
| GET | `/api/v1/ai/credits/transactions` | route_inventory, auth_boundary, user_isolation, emulator_state |
| POST | `/api/v1/ai/credits/sync-tier` | route_inventory, auth_boundary, malformed_payload, valid_payload, idempotency_or_replay, emulator_state |
| POST | `/api/v2/ai/chat/runs` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, idempotency_or_replay, kill_switch, redacted_observability, emulator_state |
| GET | `/api/v2/users/me/chat/threads` | route_inventory, auth_boundary, user_isolation, emulator_state |
| GET | `/api/v2/users/me/chat/threads/{threadId}/messages` | route_inventory, auth_boundary, user_isolation, malformed_payload, emulator_state |

### Billing / Access / Payments

| Method | Path | Required lanes |
| --- | --- | --- |
| GET | `/api/v1/billing/access-state` | route_inventory, auth_boundary, user_isolation, emulator_state |
| GET | `/api/v1/me/access` | route_inventory, auth_boundary, user_isolation, emulator_state |
| POST | `/webhooks/revenuecat` | route_inventory, malformed_payload, valid_payload, idempotency_or_replay, redacted_observability |

### Identity / Profile / Onboarding / Account Lifecycle

| Method | Path | Required lanes |
| --- | --- | --- |
| GET | `/api/v1/usernames/availability` | route_inventory, malformed_payload, valid_payload, emulator_state |
| POST | `/api/v1/users/me/username` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| GET | `/api/v1/users/me/profile` | route_inventory, auth_boundary, user_isolation, emulator_state |
| POST | `/api/v1/users/me/profile` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/ai-health-data-consent` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/onboarding` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/onboarding/complete` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/email-pending` | route_inventory, auth_boundary, malformed_payload, valid_payload, redacted_observability, emulator_state |
| POST | `/api/v1/users/me/avatar-metadata` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/avatar` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| GET | `/api/v1/users/me/export` | route_inventory, auth_boundary, user_isolation, redacted_observability, emulator_state |
| POST | `/api/v1/users/me/delete` | route_inventory, auth_boundary, destructive_privacy, user_isolation, emulator_state, mobile_e2e_local |

### Core Meal Loop / History / Saved Meals / Streaks

| Method | Path | Required lanes |
| --- | --- | --- |
| GET | `/api/v1/users/me/meals/history` | route_inventory, auth_boundary, user_isolation, malformed_payload, emulator_state |
| GET | `/api/v1/users/me/meals/photo-url` | route_inventory, auth_boundary, user_isolation, malformed_payload, emulator_state |
| GET | `/api/v1/users/me/meals/changes` | route_inventory, auth_boundary, user_isolation, malformed_payload, emulator_state |
| POST | `/api/v1/users/me/meals/photo` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/meals` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state, mobile_e2e_local |
| POST | `/api/v1/users/me/meals/{mealId}/delete` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| GET | `/api/v1/users/me/my-meals/changes` | route_inventory, auth_boundary, user_isolation, malformed_payload, emulator_state |
| POST | `/api/v1/users/me/my-meals` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/my-meals/{mealId}/delete` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/my-meals/{mealId}/photo` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| GET | `/api/v1/users/me/streak` | route_inventory, auth_boundary, user_isolation, emulator_state |
| POST | `/api/v1/users/me/streak/ensure` | route_inventory, auth_boundary, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/streak/reset-if-missed` | route_inventory, auth_boundary, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/streak/recalculate` | route_inventory, auth_boundary, valid_payload, user_isolation, emulator_state |

### Badges / Notifications / Feedback

| Method | Path | Required lanes |
| --- | --- | --- |
| POST | `/api/v1/users/me/badges/premium/reconcile` | route_inventory, auth_boundary, valid_payload, user_isolation, emulator_state |
| GET | `/api/v1/users/me/badges` | route_inventory, auth_boundary, user_isolation, emulator_state |
| GET | `/api/v1/users/me/notifications/preferences` | route_inventory, auth_boundary, user_isolation, emulator_state |
| POST | `/api/v1/users/me/notifications/preferences` | route_inventory, auth_boundary, malformed_payload, valid_payload, user_isolation, emulator_state |
| POST | `/api/v1/users/me/feedback` | route_inventory, auth_boundary, malformed_payload, valid_payload, redacted_observability, emulator_state |

### V2 Nutrition / Retention / Premium

| Method | Path | Required lanes |
| --- | --- | --- |
| GET | `/api/v2/users/me/habits` | route_inventory, auth_boundary, user_isolation, kill_switch, emulator_state |
| GET | `/api/v2/users/me/state` | route_inventory, auth_boundary, user_isolation, malformed_payload, kill_switch, emulator_state |
| GET | `/api/v2/users/me/coach` | route_inventory, auth_boundary, user_isolation, malformed_payload, kill_switch, emulator_state |
| GET | `/api/v2/users/me/reminders/decision` | route_inventory, auth_boundary, user_isolation, malformed_payload, kill_switch, emulator_state, mobile_e2e_local |
| GET | `/api/v2/users/me/reports/weekly` | route_inventory, auth_boundary, user_isolation, malformed_payload, kill_switch, premium_boundary, emulator_state, mobile_e2e_local |

## PR Plan

### PR0 - Documentation And Inventory Baseline

Scope:

- Keep this document current.
- Keep `requests/local.http` aligned with the cheap evidence runner.
- Generate local endpoint inventory and hardening matrix artifacts.
- Do not change backend behavior.

Verification:

- `python scripts/run-backend-evidence.py --base-url http://127.0.0.1:8000`
- `pytest -q`
- `python -m compileall app`
- `ruff check .`
- `./.venv/bin/pyright`

Stop conditions:

- Endpoint inventory misses a real route.
- Runner needs live Railway/Firebase/OpenAI to pass.
- Evidence artifacts expose secrets or user-authored private content.

### PR1 - Auth Boundary And Replay Privacy

Scope:

- Auth-required endpoint baseline.
- Invalid token behavior.
- Cross-user replay/idempotency checks.
- First target: HTTP idempotency cache for AI endpoints.

Verification:

- Local HTTP evidence.
- Focused pytest for middleware/auth.
- No Firebase live dependency.

### PR2 - Identity, Export, Delete

Scope:

- Seed emulator with User A and User B.
- Export includes only current user data.
- Delete removes backend-owned state intentionally and does not delete other users.
- Storage/avatar/photo cleanup evidence.
- Export/delete must include backend-owned chat memory summaries, top-level AI run telemetry filtered by `userId`, and billing credit idempotency documents.

Verification:

- Firebase emulator run: latest passing artifact `evidence/runs/pr2-emulator-20260603T212805Z`.
- Local iOS Maestro account-delete run against local backend:
  `e2e/artifacts/pr2-account-delete-20260603T233756Z/junit.xml`
  (`account-delete-disposable-user`, 1/1 passed, device `Fitaly-MJ050 - iOS 18.6`).

### PR3 - Core Meal Loop And Sync

Scope:

- Meals/history/changes/myMeals state fixtures.
- Pagination, day/range query validation, deleted state, photo URL behavior.
- User isolation and malformed query handling.

Verification:

- Firebase emulator.
- Targeted mobile local E2E for add meal/history only after backend evidence is green.

### PR4 - Billing, Credits, RevenueCat

Scope:

- RevenueCat invalid/valid secret.
- Duplicate event/replay behavior.
- Access state and credits state consistency.
- Premium/free weekly report boundary.

Verification:

- Emulator state.
- No live RevenueCat until final smoke rehearsal.

### PR5 - AI Cost And Privacy Surfaces

Scope:

- AI chat v2, v1 photo/text analysis.
- No credits, disabled flags, malformed payloads, content redaction.
- Provider failure and refund behavior with fake OpenAI/client adapters.

Verification:

- In-process fake provider tests.
- Local HTTP evidence for disabled/error contracts.
- No live OpenAI until final smoke rehearsal.

### PR6 - Retention Surfaces

Scope:

- State, habits, coach, reminders, weekly reports.
- Kill switches.
- Premium/free boundaries.
- Telemetry allowlists and redaction.

Verification:

- Emulator state.
- Local mobile E2E for reminders/weekly entry only after backend evidence is green.

## Worker Tasks

Use workers only where scopes are independent.

### Worker A - Inventory And Route Classification

Objective: keep route inventory and hardening matrix synchronized with FastAPI.

Scope:

- `scripts/run-backend-evidence.py`
- `requests/local.http`
- this document

Expected output:

- endpoint inventory artifact,
- hardening matrix artifact,
- missing route report.

Stop condition:

- any route cannot be classified without reading implementation.

### Worker B - Auth Boundary

Objective: prove protected surfaces reject missing/invalid auth and do not leak data.

Scope:

- `app/api/deps/auth.py`
- auth-required route tests
- local HTTP evidence

Expected output:

- missing-auth and invalid-auth evidence for every protected group.

Stop condition:

- a route validates payload before auth in a way that weakens security signal.

### Worker C - Emulator State

Objective: seed local Firebase emulator and verify stateful user isolation.

Scope:

- Firebase config/rules,
- seed scripts,
- export/delete/meals/credits fixtures.

Expected output:

- deterministic seed,
- User A/User B isolation evidence.

Stop condition:

- emulator cannot reproduce a backend-owned state shape.

### Worker D - Provider And Cost Fakes

Objective: prove AI/payment failure paths without live provider cost.

Scope:

- fake OpenAI responses,
- fake RevenueCat payloads,
- credits/refund/replay evidence.

Expected output:

- no credits,
- provider failure,
- refund/idempotency artifacts.

Stop condition:

- test requires a real provider key to prove core behavior.

## Open Decisions

- Whether Firebase CLI stays as npm dev dependency despite moderate audit
  findings, or becomes an operator-installed prerequisite.
- Whether rules tests should live in backend, mobile, or a shared workspace
  harness once mobile direct Firebase writes are audited.
