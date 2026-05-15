# Runtime Config Contract

This is the backend side of the mobile to backend runtime contract for Fitaly prod, smoke, and dev/local environments. It mirrors the mobile contract in `fitaly/docs/runtime-config.md`.

Do not commit secrets. Values such as Firebase private keys, OpenAI keys, RevenueCat secrets, and Sentry DSNs stay in Railway/EAS secret stores.

## Environment Matrix

| Contract environment | Backend Railway environment | Backend `ENVIRONMENT` | Expected mobile API URL | Telemetry | Smart Reminders | Billing / RevenueCat | Firebase eager init | OpenAI / AI gateway |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `prod` | `prod` | `production` | Mobile `EXPO_PUBLIC_API_BASE_URL=https://fitaly-backend-production.up.railway.app` | `TELEMETRY_ENABLED=true`; mobile must also build with `EXPO_PUBLIC_ENABLE_TELEMETRY=true`. | `SMART_REMINDERS_ENABLED=true`; mobile must build with `EXPO_PUBLIC_ENABLE_SMART_REMINDERS=true`. | Billing enabled. Mobile `DISABLE_BILLING=false`; production RevenueCat SDK keys in EAS. Backend `REVENUECAT_API_KEY` and `REVENUECAT_WEBHOOK_SECRET` target the production RevenueCat project. | `EAGER_FIREBASE_INIT=true`; startup must fail fast when production Firebase config or credentials are invalid. | `OPENAI_API_KEY` configured; `AI_CHAT_ENABLED=true`; `AI_GATEWAY_ENABLED=true`. |
| `smoke` | `smoke` | `production` | Mobile `EXPO_PUBLIC_API_BASE_URL=https://fitaly-backend-smoke.up.railway.app` | Launch-like smoke: `TELEMETRY_ENABLED=true`; mobile `EXPO_PUBLIC_ENABLE_TELEMETRY=true`. If disabled for rollback testing, do not count that run as launch readiness. | `SMART_REMINDERS_ENABLED=true`; mobile `EXPO_PUBLIC_ENABLE_SMART_REMINDERS=true`. | Billing enabled for launch rehearsal. Use smoke/sandbox RevenueCat credentials or another explicitly safe test setup. Do not reuse production webhook secrets unless the environment is intentionally wired to production RevenueCat behavior. | Default `EAGER_FIREBASE_INIT=false` for lightweight smoke runtime. Temporarily set `true` only for deliberate Firestore credential/startup readiness checks. | Smoke or limited `OPENAI_API_KEY` configured; `AI_CHAT_ENABLED=true`; `AI_GATEWAY_ENABLED=true`. |
| `dev/local` | local developer runtime | `local` or `development` | Mobile local default `http://localhost:8000/`; remote dev-client builds may intentionally point to smoke. | Default `TELEMETRY_ENABLED=false`; enable only when testing telemetry locally. | Default `SMART_REMINDERS_ENABLED=true`, but local failures do not block release readiness. | Developer choice. RevenueCat backend secrets may be empty unless testing sync/webhooks. | Developer choice based on local Firebase testing needs. | `OPENAI_API_KEY` may be empty unless testing AI. |

Notes:

- Railway `smoke` is an environment label. Backend `ENVIRONMENT=smoke` is accepted as a deployment-label alias and normalized to `production`; use `SENTRY_ENVIRONMENT=smoke` to keep Sentry streams separate.
- `SENTRY_ENVIRONMENT` should be `production` in prod and `smoke` in smoke.
- Smoke should use separate or scoped Firebase, OpenAI, RevenueCat, and Sentry secrets where possible. The contract requires launch-like behavior, not shared secrets.
- Local/dev fallbacks must not be used as production readiness evidence.

## Smoke Checklist After Runtime Config Changes

1. Confirm Railway smoke variables:
   - `ENVIRONMENT=production`
   - `DEBUG=false`
   - `WEB_CONCURRENCY=1`
   - `FIRESTORE_DATABASE_ID=fitaly-smoke`
   - `EAGER_FIREBASE_INIT=false` unless intentionally testing Firebase startup readiness
   - `TELEMETRY_ENABLED=true`
   - `SMART_REMINDERS_ENABLED=true`
   - `WEEKLY_REPORTS_ENABLED=true`
   - `AI_CHAT_ENABLED=true`
   - `AI_GATEWAY_ENABLED=true`
   - `SENTRY_ENVIRONMENT=smoke`
2. Confirm mobile smoke build profile points at `https://fitaly-backend-smoke.up.railway.app` with telemetry, Smart Reminders, and billing enabled.
3. Confirm RevenueCat smoke/sandbox credentials are the intended ones:
   - EAS `RC_IOS_API_KEY`
   - EAS `RC_ANDROID_API_KEY`
   - Railway `REVENUECAT_API_KEY`
   - Railway `REVENUECAT_WEBHOOK_SECRET`
4. Confirm OpenAI and Firebase smoke secrets are separate or intentionally scoped and do not expose production-only credentials.
5. Run lightweight health:
   - `GET https://fitaly-backend-smoke.up.railway.app/api/v1/health`
6. Run deep Firebase health only when deliberately validating Firestore:
   - temporarily set `EAGER_FIREBASE_INIT=true` if startup credential validation is part of the test
   - `GET https://fitaly-backend-smoke.up.railway.app/api/v1/health/firestore`
7. Run authenticated contract smoke when smoke user secrets are configured:
   - `python scripts/check-flow-contracts.py --base-url https://fitaly-backend-smoke.up.railway.app --env smoke`
8. Attach release evidence for telemetry ingest, Smart Reminder decision, AI path, weekly report premium gating, and RevenueCat purchase/restore smoke note.
