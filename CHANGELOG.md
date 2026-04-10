# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows `app/core/config.py → Settings.VERSION`.

---

## [0.1.0] – Unreleased

### Security
- Fixed polynomial ReDoS in email validation regex (`user_account_service.py`)
- Fixed polynomial ReDoS in email masking regex (`sanitization_service.py`)
- Added `pip-audit` to CI and `requirements.txt` for dependency CVE scanning
- Production startup now rejects empty or wildcard `CORS_ORIGINS`

### Changed
- Gunicorn worker count increased from 2 to 4 (`Procfile`)
- Added `# type: ignore` suppressions for missing firebase-admin / google-cloud-firestore stubs (`database_id`, `get_all`)

### Fixed
- Removed stale `tests/test_firestore_service.py` (imported deleted `firestore_service` module)
- Removed unused imports `RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` from `tests/test_ai_gateway_service.py`

---

## [0.0.1] – Initial release

### Added
- FastAPI application with `/api/v1` stable routes and `/api/v2` feature-flagged routes
- Firebase Auth token verification middleware
- AI gateway with credits, rate limiting, and content guard
- Chat threads, meal logging, nutrition state, streaks, badges
- Weekly report aggregation
- RevenueCat webhook handler
- Telemetry ingest (batch, no PII)
- Sentry monitoring integration
- GitHub Actions CI: Ruff, Pyright, Pytest with coverage, pip-audit
