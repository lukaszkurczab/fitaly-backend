# Backend Hardening Evidence Guidance

Status: active guidance, no active PR queue
Last updated: 2026-06-04

## Objective

Use cheap, local backend evidence to prove a specific release-risk claim before
using Railway, production Firebase, smoke Firebase, OpenAI, RevenueCat, or
mobile E2E.

This document is methodology, not a backlog. Do not treat old broad hardening
runs as the next PR sequence.

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

| Level | Name | Purpose |
| --- | --- | --- |
| L0 | Static inventory | Route/OpenAPI inventory and risk classification. |
| L1 | Local HTTP no-state | Middleware, auth rejection, disabled/no-op states, redaction. |
| L2 | In-process service/fake repo | Logic branches without Firebase/OpenAI/provider cost. |
| L3 | Firebase emulator state | Firestore/Auth/Storage document shape and user isolation. |
| L4 | Mobile against local backend | FE/BE integration through real app workflows. |
| L5 | Smoke/Railway | Final release rehearsal only. |

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

## Targeted Evidence Rules

For future targeted backend work:

1. State the concrete risk being proven.
2. Pick the minimum evidence level that proves it.
3. Prefer focused pytest or a temporary local harness over a committed broad
   runner.
4. Keep run output sanitized and outside git.
5. Commit product fixes and stable tests discovered by evidence.
6. Do not commit PR-specific status tables, next-PR sequencing, or temporary
   package scripts.

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
