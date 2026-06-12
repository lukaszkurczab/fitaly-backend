# Backend Ops Monitoring Runbook

## Scope

This runbook defines the minimum production monitoring baseline for:

- smoke environment: `https://fitaly-backend-smoke.up.railway.app`
- production environment: `https://fitaly-backend-production.up.railway.app`

## Dashboards and Links

- Ops monitoring workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/ops-monitoring.yml`
- Security workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/security.yml`
- Firestore backup workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/firestore-backup.yml`
- Firestore restore drill workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/firestore-restore-drill.yml`
- Railway production dashboard: `https://railway.app/project/<project-id>/service/<service-id>`
- Sentry backend production dashboard: `https://sentry.io/organizations/<org-slug>/projects/<backend-project-slug>/`

## Monitoring Baseline

1. GitHub Actions workflow `Ops Monitoring` runs every 30 minutes.
2. It checks:
   - `GET /api/v1/health` on smoke
   - `GET /api/v1/health` on production
   - Railway service `Health Check Path` is configured to `/api/v1/health` for both environments
   - authenticated smoke flow contracts (`scripts/check-flow-contracts.py`) when smoke secrets are configured:
     - `GET /api/v1/users/me/export`
     - `GET /api/v1/ai/credits`
     - `GET /api/v2/users/me/reports/weekly` (expected `403 WEEKLY_REPORT_PREMIUM_REQUIRED` for free smoke user)
3. `GET /api/v1/health/firestore` is excluded from automated Railway liveness because it performs a Firestore read. Use it only as a manual deep readiness check when validating Firestore connectivity on purpose.
4. It fails when:
   - HTTP status is not `200`
   - latency is over threshold
   - payload does not contain `status: "ok"` (or `healthy`)
   - or flow contract status/payload checks fail

## Latency Thresholds

- smoke health latency: `<= 3000ms`
- production health latency: `<= 2000ms`
- smoke flow contract latency (per endpoint): `<= 5000ms`

If those thresholds fail repeatedly, treat as an incident candidate even when uptime is still present.

## Alerting Rules (Minimum)

1. `Ops Monitoring` failure on production = open Discord `launch-ops`.
2. ACK SLA for production alerts during Day0-Day7 is `<= 15 minutes`.
3. 2 consecutive production failures = rollback readiness check.
4. Sentry must be enabled on production (`SENTRY_DSN`, `SENTRY_ENVIRONMENT=production`).
5. Any spike of API 5xx visible in Sentry should trigger manual investigation.
6. Workflow-level notifications are sent by `OPS_ALERT_DISCORD_WEBHOOK_URL`; GitHub email stays fallback-only.
7. If flow checks are skipped due to missing smoke secrets, treat it as monitoring debt and configure secrets immediately.

## Observability Privacy Boundary

1. Sentry, Railway service logs, and backend process logs are operational
   observability records. They stay outside account export/delete only when
   backend redaction is active and provider/infrastructure retention remains
   documented in the release evidence packet.
2. Backend Sentry initialization must use the shared `before_send` sanitizer.
   Python logging call sites that include sensitive diagnostic fields must
   redact before emitting `extra`.
3. Operational logs must not intentionally include emails, auth headers,
   tokens, passwords, API keys, provider-looking secrets, raw provider
   prompts/responses, full request/response bodies, user-authored meal/chat
   text, Firebase/Google Storage URLs, raw Storage object paths, or URL query
   strings.
4. Railway and Sentry retention are controlled by their provider project/service
   settings. Before launch and during quarterly review, capture the current
   Sentry event retention and Railway log retention settings as evidence.
5. If unredacted sensitive data is suspected in Sentry, Railway, or backend
   runtime logs, treat the time window as privacy-relevant incident scope and
   escalate through `launch-ops`.

## Incident Triage Checklist

1. Confirm current deployment version on Railway.
2. Verify `GET /api/v1/health` and `GET /api/v1/version`.
3. Run `GET /api/v1/health/firestore` only if incident triage requires explicit Firestore connectivity validation.
4. Check latest Sentry errors and affected endpoint group in the backend production Sentry project dashboard.
5. Open the Railway backend service dashboard and confirm the active deployment, logs, recent restart history, and `Health Check Path=/api/v1/health`.
6. Validate Firebase/OpenAI config variables are still present.
7. Apply kill-switches if needed:
   - `SMART_REMINDERS_ENABLED=false`
   - `WEEKLY_REPORTS_ENABLED=false`
   - `TELEMETRY_ENABLED=false`
8. If user impact persists, rollback to last known-good release.

## Ownership

- Primary owner: backend engineer on release duty
- Incident commander (Day0-Day7): engineering lead
- Day0-Day7 backend owner: backend engineer on release duty
- Day0-Day7 mobile owner: mobile engineer
