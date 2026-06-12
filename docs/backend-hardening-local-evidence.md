# Backend Hardening Operating Guide

Status: active operating guide
Last updated: 2026-06-04

## Purpose

Use this document to choose the smallest reliable backend verification path for
release-hardening work. It should describe how to operate the repository and
what remains next. It should not become a dated log of completed passes.

Local backend green means the backend unit, type, lint, compile, and focused
local/emulator checks pass. It does not replace mobile E2E, smoke/Railway, live
OpenAI, or RevenueCat sandbox rehearsal.

## Repo Boundary

Keep only repeatable, generally useful hardening tooling in the repository.

- `scripts/run-backend-evidence.py` is the generic local public-route baseline.
- `requests/local.http` is the manual replay companion for that baseline.
- Focused regression and emulator checks should live as pytest tests.
- Generated run artifacts stay in `evidence/runs/`, which is ignored by git.
- Do not commit one-off broad runners, temporary seed scripts, ad hoc package
  commands, or historical run diaries.

## Verification Levels

Use the lowest level that proves the active claim.

| Level | Name | Use when |
| --- | --- | --- |
| L0 | Static inventory | Route existence, OpenAPI inventory, or matrix drift is the risk. |
| L1 | Local HTTP no-state | Middleware, auth rejection, disabled states, or redaction can be proved without state. |
| L2 | In-process service/fake repo | Logic branches can be proved without Firebase/OpenAI/provider cost. |
| L3 | Firebase emulator state | Firestore/Auth/Storage shape, idempotency, or user isolation is the claim. |
| L4 | Mobile against local backend | The app workflow is the risk, not only backend behavior. |
| L5 | Smoke/Railway | Final launch rehearsal or provider wiring must be validated. |

Default backend hardening should stop at L0-L3 unless the active risk requires
mobile behavior or live-provider wiring.

## Evidence Lanes

Use these lane names in plans, test names, and summaries:

- `route_inventory`
- `auth_boundary`
- `malformed_payload`
- `valid_payload`
- `user_isolation`
- `emulator_state`
- `idempotency_or_replay`
- `kill_switch`
- `premium_boundary`
- `redacted_observability`
- `mobile_e2e_local`
- `manual_only`

## Standard Backend Gate

Run these before calling backend hardening locally green:

```bash
pytest
python -m compileall app
ruff check .
./.venv/bin/pyright
```

For emulator-backed claims, also run the focused emulator test with explicit
emulator environment variables and then verify the same test skips cleanly
without emulator env.

## Generic Local Baseline

Use the generic baseline only for route inventory, public endpoint shape, and
cheap auth/no-state behavior:

```bash
python scripts/run-backend-evidence.py --base-url http://127.0.0.1:8000
```

Keep `requests/local.http` aligned with this baseline. Do not add PR-specific
or one-off scenarios unless they become recurring operator checks.

## Repair Loop

For each active backend surface:

1. Restate the product risk in one sentence.
2. Read the route, schema, service, tests, docs, and mobile-facing contract.
3. Identify the canonical path and any legacy or duplicate path.
4. Pick the lowest verification level that proves the claim.
5. Patch the smallest correct layer.
6. Run focused verification, then the standard backend gate.
7. Record only durable repo guidance or next steps, not a run diary.

## Next Steps After Local Backend Green

These are the remaining release-readiness steps after local backend checks are
green:

1. Run focused Firebase emulator tests together as a batch to catch singleton or
   environment cleanup conflicts between tests.
2. Run mobile release-gate E2E against a local or smoke backend for launch-critical
   flows: auth, account launch, add meal, premium/paywall, reminders, weekly
   reports, and AI chat where in scope.
3. Run Railway smoke with launch-like config:
   - health endpoint,
   - Firestore health when deliberately validating credentials,
   - authenticated contract smoke when smoke users are configured.
4. Run provider rehearsal with safe credentials:
   - OpenAI limited smoke for AI paths,
   - RevenueCat sandbox purchase/restore/webhook paths,
   - telemetry ingest with bounded props.
5. Review mobile/backend contract fixtures if any response shape, enum, or
   Firestore contract changed.

## Stop Conditions

Stop the loop and re-plan if:

- a test needs production credentials to prove core behavior,
- a disabled feature silently falls back instead of returning an explicit state,
- local evidence starts replacing required smoke/mobile/live-provider rehearsal,
- docs drift into historical status logs instead of durable operating guidance,
- backend and mobile contract fixtures drift.
