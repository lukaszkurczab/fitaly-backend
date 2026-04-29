# Weekly Reports v1 (Backend)

## Purpose

`GET /api/v2/users/me/reports/weekly?weekEnd=YYYY-MM-DD` is a backend-first weekly synthesis surface.

- It summarizes one closed 7-day window.
- It is deterministic, rule-based, and explainable.
- It does not call an LLM.
- It does not schedule reminders, replace coach, or build a dashboard.
- It is not the source of truth for immediate mobile `Statistics` UI.

## Scope Boundaries

Weekly Reports v1 does:

- collect a closed 7-day input window
- derive bounded weekly signals
- select a small set of user-facing insights
- select 1-2 concrete priorities for the next week

Weekly Reports v1 does not:

- generate long narrative text
- build PDF/export
- reuse chat context
- change reminder delivery behavior
- build a broad analytics subsystem
- replace the mobile local read model used by `Statistics`

## Mobile Statistics Boundary

Weekly reports may reuse backend nutrition summaries and bounded meal aggregation, but that backend path is for reports, coach, and AI-facing synthesis.

It must not become the default source for mobile `Statistics`. The `Statistics` screen is expected to update from the local meals read model immediately after local save/edit/delete, without a backend refetch.

## Endpoint And Window Semantics

Endpoint:

```text
GET /api/v2/users/me/reports/weekly?weekEnd=YYYY-MM-DD
```

Rules:

- `weekEnd` must be a closed day before current UTC day.
- If `weekEnd` is omitted, backend defaults to yesterday UTC.
- Returned `period` always covers exactly 7 full days:
  - `startDay = weekEnd - 6 days`
  - `endDay = weekEnd`
- The service also reads the previous 7-day window to derive a bounded comparison signal.

## Response Contract

Backend returns `WeeklyReportResponse` with:

- `status`
- `period`
- `summary`
- `insights`
- `priorities`

Boundaries enforced by schema:

- `status`: `ready | insufficient_data`
- `insights.length <= 4`
- `priorities.length <= 2`
- `summary.length <= 160`
- `insight.reasonCodes.length <= 6`
- `priority.reasonCodes.length <= 6`

Current selection logic returns at most:

- `3` insights
- `2` priorities

The schema keeps room for one extra insight without changing the public contract.

## Status Semantics

### `ready`

Returned when the closed week has enough evidence for a bounded synthesis.

Current sufficiency rule:

- at least `4` valid logged days in the 7-day window

### `insufficient_data`

Returned when the feature is enabled but the week is too sparse for a trustworthy synthesis.

Current placeholder behavior:

- empty `insights`
- empty `priorities`
- short bounded `summary`

### Disabled surface

When `WEEKLY_REPORTS_ENABLED=false`, the endpoint returns `503 Service Unavailable` with `detail="Weekly reports are disabled"`.

This is a runtime kill switch state, not a fallback weekly report payload.

## Input Layer

The weekly report builds an internal aggregation over day-level meal data and derives bounded weekly signals from it.

Current internal signals:

- `consistency`
- `logging_coverage`
- `start_of_day_stability`
- `day_completion_tendency`
- `weekend_drift`
- `improving_vs_previous_week`

These are internal semantics only. They are not returned directly to clients.

Selection intentionally suppresses weekly signals that do not add distinct meaning to the final report.

Current example:

- `logging_coverage` is not surfaced when it only repeats the same story already expressed by `consistency`
- `logging_coverage` is surfaced when it adds a distinct quality/detail gap, such as unknown-detail days

## Insight Types

Current bounded insight types:

- `consistency`
- `logging_coverage`
- `start_of_day_pattern`
- `day_completion_pattern`
- `weekend_drift`
- `improving_trend`

Each insight:

- maps to one deterministic signal path
- has bounded `importance`
- has bounded `tone`
- carries `reasonCodes`

## Priority Types

Current bounded priority types:

- `maintain_consistency`
- `increase_logging_coverage`
- `stabilize_start_of_day`
- `improve_day_completion`
- `reduce_weekend_drift`

Priorities are selected from chosen insights, not directly from raw data.

## Selection Rules

Current insight ordering rules:

1. strongest positive insight
2. biggest negative gap
3. next actionable non-positive insight
4. trend only if it still fits within bounded output

Current priority rules:

- negative or neutral `consistency` / `logging_coverage` -> `increase_logging_coverage`
- negative or neutral `start_of_day_pattern` -> `stabilize_start_of_day`
- negative or neutral `day_completion_pattern` -> `improve_day_completion`
- negative `weekend_drift` -> `reduce_weekend_drift`
- positive `consistency`, `day_completion_pattern`, `improving_trend`, or positive `weekend_drift` -> `maintain_consistency`

The selector does not add a generic fallback priority just to fill the payload. If no rule-backed priority exists, the output should stay small rather than padded.

## Explainability Guarantees

Weekly Reports v1 stays explainable because:

- signal derivation is deterministic
- insight selection uses explicit scores and ordering rules
- priorities are selected from bounded mappings
- output text comes from bounded templates
- every insight and priority includes deterministic `reasonCodes`

## Failure Semantics

Expected HTTP behavior:

- `200 OK`
  - valid `WeeklyReportResponse`
  - includes `ready` or `insufficient_data`
- `400 Bad Request`
  - invalid `weekEnd`
- `500 Internal Server Error`
  - backend computation failure, Firestore failure, or internal contract bug

The endpoint should not translate backend failures into fake `insufficient_data`.

## Telemetry

Weekly Reports v1 does not require broad new backend telemetry.

If telemetry is enabled on mobile, keep the allowlist bounded to:

- `weekly_report_viewed`
- `weekly_report_cta_clicked`
- `weekly_report_dismissed`

Do not send:

- `summary`
- insight `title` or `body`
- raw `reasonCodes`
- user-authored text
