# Backend Hardening Local Evidence Plan

Status: active guidance and pending backlog, no active PR selected
Last updated: 2026-06-04

## Objective

Build backend hardening evidence locally before using Railway, production
Firebase, smoke Firebase, OpenAI, RevenueCat, or mobile E2E.

The goal is the backend equivalent of FE screenshot evidence: each active
backend risk should have visible, repeatable proof of request, response, status,
data-state assumptions, and redaction. This file is both:

- stable methodology for backend evidence work,
- lightweight backlog/context for remaining backend hardening areas.

It is not a place for one-off runner scripts, package commands, or permanent
links to every temporary local run.

## Repo Boundary

Keep only repeatable, generally useful evidence tooling in the repository.

- `scripts/run-backend-evidence.py` is the generic local public baseline.
- `requests/local.http` is the manual replay companion for that baseline.
- One-off broad PR runners, ad hoc seed scripts, and PR-specific commands should
  not live in `package.json`, this document, or committed scripts.
- Generated run artifacts stay in `evidence/runs/`, which is ignored by git.
- If a targeted check becomes recurring product infrastructure, promote it with
  a neutral name and stable contract after explicit decision.

## Current Reality

- Backend stack: FastAPI, Firebase Admin SDK, Firestore, OpenAI, Sentry.
- Public API split: `/api/v1` current surface, `/api/v2` newer backend-owned
  surfaces.
- Local Firebase config baseline exists: `firebase.json`, `firestore.rules`,
  `storage.rules`, `.firebaserc`.
- Firebase Admin SDK bypasses Firestore rules. Emulator evidence proves backend
  behavior and document shapes; client/rules enforcement needs separate rules
  tests when mobile writes directly to Firebase.
- Bruno CLI is intentionally not a dependency because tested versions introduced
  high-severity audit findings. `.http` files are the manual alternative.

## Evidence Levels

Use the lowest level that proves the claim.

| Level | Name | Cost | Purpose | Artifact |
| --- | --- | --- | --- | --- |
| L0 | Static inventory | none | Prove every route is known and classified | endpoint inventory, hardening matrix |
| L1 | Local HTTP no-state | low | Middleware, auth rejection, disabled/no-op states, redaction | request/response JSON |
| L2 | In-process service/fake repo | low | Logic branches without Firebase/OpenAI/provider cost | pytest evidence, fixture snapshots |
| L3 | Firebase emulator state | medium | Firestore/Auth/Storage document shape and user isolation | sanitized run artifacts |
| L4 | Mobile against local backend | medium | FE/BE integration through app workflows | Maestro/screens/log evidence |
| L5 | Smoke/Railway | high | Final release rehearsal only | smoke flow evidence |

Default backend hardening should stop at L0-L3 unless the active risk requires
mobile interaction or live provider behavior.

## Evidence Lanes

Use these lane names in temporary plans, run summaries, and final reports:

- `route_inventory`: endpoint exists in generated route/OpenAPI inventory.
- `auth_boundary`: missing, invalid, and cross-user auth behavior is explicit.
- `malformed_payload`: invalid input returns bounded validation errors.
- `valid_payload`: happy path or safe no-op request is covered locally.
- `user_isolation`: User A cannot read/write/delete User B data.
- `emulator_state`: Firestore/Auth/Storage state is seeded and inspected locally.
- `idempotency_or_replay`: duplicate request, webhook event, or retry behavior is covered.
- `kill_switch`: disabled feature state is explicit and has no hidden fallback.
- `premium_boundary`: free/premium access is backend-true.
- `redacted_observability`: artifacts redact tokens, secrets, PII, and private content.
- `mobile_e2e_local`: mobile flow runs against local backend.
- `manual_only`: endpoint is intentionally excluded from automated cheap checks.

## Stable Baseline

Use the generic baseline when the goal is route inventory, public endpoints, or
auth/no-state behavior:

```bash
python scripts/run-backend-evidence.py --base-url http://127.0.0.1:8000
```

Keep `requests/local.http` aligned with that baseline only. Do not add one-off
PR scenarios to the manual request file unless they become a recurring operator
check.

## Evidence Workflow

For each active backend surface, use this loop before expanding coverage:

1. Verify docs against code, tests, schemas, and mobile contract usage.
2. Check completeness: route exists, app contract is known, and an evidence lane
   exists or is explicitly missing.
3. Run the local call or focused test.
4. Compare expected app-facing behavior against the actual response/state.
5. Classify mismatches as backend bug, mobile contract drift, stale docs, or
   incomplete harness.
6. Patch the smallest correct layer, add focused regression coverage, rerun
   checks, then continue.

## Completed Context

Keep completed broad passes as context, not as commands to rerun blindly.

- Documentation and route inventory baseline exists through the generic local
  evidence runner.
- Auth boundary and AI idempotency/replay privacy were hardened in earlier
  passes.
- Identity/export/delete evidence was handled as a broad local pass. Its
  one-off runner should not remain a committed workflow.

## Pending Backend Hardening PR Backlog

These are planning anchors for future targeted PRs. They are not an instruction
to run another broad pass in this order if newer product evidence points
elsewhere.

### PR3 - Core Meal Loop And Sync

Scope:

- Meals/history/changes/myMeals state fixtures.
- Pagination, day/range query validation, deleted state, photo URL behavior.
- User isolation and malformed query handling.
- Targeted mobile local E2E for add meal/history only when backend evidence is
  green and the frontend flow is the active risk.

Verification:

- Focused pytest for deterministic service bugs.
- Temporary Firebase emulator harness only for the specific state claim.
- Local mobile E2E only as integration evidence, not as backend unit proof.

Stop conditions:

- The harness starts becoming a committed general-purpose PR runner.
- Mobile and backend meal contract fixtures drift.
- A query shape cannot be proved without live Firebase.

### PR4 - Billing, Credits, RevenueCat

Scope:

- RevenueCat invalid/valid secret behavior.
- Duplicate event/replay behavior.
- Access state and credits state consistency.
- Premium/free weekly report boundary.

Verification:

- In-process and emulator state where possible.
- No live RevenueCat until final smoke rehearsal.

Stop conditions:

- A test requires real provider credentials to prove core behavior.
- Free/premium contract changes are needed without paired mobile contract review.

### PR5 - AI Cost And Privacy Surfaces

Scope:

- AI Chat v2 and v1 photo/text analysis.
- No-credits, disabled flags, malformed payloads, and content redaction.
- Provider failure and refund behavior with fake OpenAI/client adapters.

Verification:

- In-process fake provider tests.
- Local HTTP evidence for disabled/error contracts.
- No live OpenAI until final smoke rehearsal.

Stop conditions:

- User-authored prompt/content appears in logs or artifacts.
- Credit deduction/refund semantics cannot be proved without a stable fake.

### PR6 - Retention Surfaces

Scope:

- Nutrition state, habits, coach, reminders, and weekly reports.
- Kill switches.
- Premium/free boundaries.
- Telemetry allowlists and redaction.

Verification:

- Focused service tests and emulator state.
- Local mobile E2E for reminders/weekly entry only after backend evidence is
  green.

Stop conditions:

- Disabled surfaces fall back silently instead of returning explicit degraded
  state.
- Telemetry props become unbounded or user-authored.

## Worker Task Templates

Use workers only where scopes are independent.

### Inventory And Route Classification

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

### Auth Boundary

Objective: prove protected surfaces reject missing/invalid auth and do not leak
data.

Scope:

- `app/api/deps/auth.py`
- auth-required route tests
- local HTTP evidence

Expected output:

- missing-auth and invalid-auth evidence for every protected group.

Stop condition:

- a route validates payload before auth in a way that weakens security signal.

### Emulator State

Objective: seed local Firebase emulator and verify stateful user isolation for
the active risk only.

Scope:

- Firebase config/rules,
- temporary seed script or focused pytest,
- export/delete/meals/credits fixtures as needed by the active claim.

Expected output:

- deterministic seed,
- User A/User B isolation evidence.

Stop condition:

- emulator cannot reproduce a backend-owned state shape.

### Provider And Cost Fakes

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

## Quality Gate

Backend work still uses the repository quality gate:

```bash
pytest
python -m compileall app
ruff check .
./.venv/bin/pyright
```

Add focused tests for any product bug found during evidence. Evidence itself is
not a substitute for a regression test when the bug is deterministic.
