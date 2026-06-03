# Backend Evidence Harness

This directory defines local, repeatable backend evidence runs.

The hardening source-of-truth plan is
`docs/backend-hardening-local-evidence.md`.

Generated run artifacts are written to `evidence/runs/` and ignored by git.
Artifacts should stay sanitized: no raw bearer tokens, secrets, private keys,
emails, or user-authored content beyond the explicit test fixture payload.

## Local Public Baseline

Start the backend with Firebase eager init disabled when no emulator is running:

```bash
EAGER_FIREBASE_INIT=false uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then run:

```bash
python scripts/run-backend-evidence.py --base-url http://127.0.0.1:8000
```

The default scenario checks:

- public health and version endpoints,
- protected endpoints reject missing auth,
- telemetry disabled behavior is explicit.

## Manual Requests

Use `requests/local.http` for manual local request replay in editors that support
`.http` files, such as VS Code REST Client or JetBrains HTTP Client. Keep manual
requests aligned with the Python evidence runner; the runner remains the
canonical automated proof because it writes sanitized artifacts.

## Emulator Baseline

After installing dev tools:

```bash
npm run firebase:emulators
```

Use the emulator baseline for targeted Firestore/Auth/Storage evidence when a
specific risk requires stateful proof.

Backend Admin SDK access bypasses Firestore rules. Emulator runs prove backend
behavior and document shapes. Client-side Firestore rules still need their own
rules-focused checks when mobile writes directly to Firebase.

One-off broad PR runners and PR-specific commands should stay out of committed
scripts and `package.json`. Keep their sanitized outputs in `evidence/runs/`
when they are useful for local review, but do not turn them into a standing
repo workflow unless the check becomes recurring product infrastructure.
