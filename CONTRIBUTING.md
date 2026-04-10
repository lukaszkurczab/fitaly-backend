# Contributing to fitaly-backend

## Branches

| Pattern | Purpose |
|---------|---------|
| `main` | Production-ready code; protected, requires PR |
| `feat/<short-name>` | New features |
| `fix/<short-name>` | Bug fixes |
| `chore/<short-name>` | Tooling, deps, config |

## Pull Requests

- One logical change per PR; keep diffs reviewable
- Title format: `type: short description` (e.g. `feat: add weekly report v2 endpoint`)
- Link the relevant issue if one exists
- All CI checks must be green before merge (Ruff, Pyright, Pytest, pip-audit)
- Do not merge your own PR without a review on feature/fix branches

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in local values
uvicorn app.main:app --reload
```

If `.venv/bin/python` points to an old path after moving the repo, recreate `.venv` before continuing.

Health check: `curl http://localhost:8000/api/v1/health`

## Running checks

```bash
ruff check .                              # lint
pyright                                   # type checking
pytest -q --cov=app --cov-report=term-missing   # tests + coverage
pip-audit -r requirements.txt             # dependency CVE scan
```

## Environment variables

See `.env.example` for the full list. Critical vars for local dev:

| Variable | Purpose |
|----------|---------|
| `FIREBASE_PROJECT_ID` | Firebase project |
| `FIREBASE_CLIENT_EMAIL` + `FIREBASE_PRIVATE_KEY` | Service account credentials |
| `OPENAI_API_KEY` | AI features |
| `ENVIRONMENT` | `development` / `smoke` / `production` |
| `CORS_ORIGINS` | Comma-separated allowed origins; **must not be empty in production** |
| `FIRESTORE_DATABASE_ID` | `(default)` for prod, `fitaly-smoke` for staging |

## API versioning

- Stable API lives under `/api/v1` — **no breaking changes without a new version**
- Breaking changes must be introduced in `/api/v2` and announced to the mobile team
- The version constants are in `app/core/api_version.py`

## Adding a new endpoint

1. Create a router in `app/api/routes/<name>.py`
2. Register it in `app/api/router.py`
3. Add auth dependency (`verify_firebase_token`) unless the endpoint is intentionally public
4. Write at least one happy-path and one error-path test
5. If the endpoint is consumed by the mobile app, update the contract snapshot (`scripts/verify-backend-contract.sh` in the `fitaly` repo)

## Sensitive data rules

- Never log request bodies, user messages, or AI responses at INFO level or above
- Telemetry payloads must be categorical — no free-text user content
- Email addresses in validation errors must be masked before logging

## Release checklist

Before tagging a production deploy:

- [ ] `CHANGELOG.md` updated
- [ ] `Settings.VERSION` bumped in `app/core/config.py`
- [ ] All CI checks green on `main`
- [ ] `firestore-backup.yml` latest run is green and artifact is readable
- [ ] `firestore-restore-drill.yml` latest run is green and artifact is readable
- [ ] `CORS_ORIGINS` set correctly in Railway production environment
- [ ] `ENVIRONMENT=production` set in Railway
- [ ] `FIRESTORE_DATABASE_ID=(default)` confirmed for prod
- [ ] Sentry DSN configured
- [ ] `OPS_ALERT_DISCORD_WEBHOOK_URL` configured for workflow alerts
