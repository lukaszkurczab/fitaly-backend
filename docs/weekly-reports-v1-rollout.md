# Weekly Reports v1 Rollout (Backend)

## Preconditions

### Backend flags

| Flag | Required value | Purpose |
|---|---|---|
| `WEEKLY_REPORTS_ENABLED` | `true` | Enables weekly report endpoint |

### Mobile flags

| Flag | Required value | Purpose |
|---|---|---|
| `EXPO_PUBLIC_ENABLE_WEEKLY_REPORTS` | `true` | Enables weekly report fetch and UI |

Enable in order:

1. backend `WEEKLY_REPORTS_ENABLED=true`
2. mobile `EXPO_PUBLIC_ENABLE_WEEKLY_REPORTS=true`

Disable in reverse order.

## Expected Endpoint Behavior

```text
GET /api/v2/users/me/reports/weekly?weekEnd=YYYY-MM-DD
```

Expected responses:

| Status code | When | Meaning |
|---|---|---|
| `200` | feature computed successfully | valid `WeeklyReportResponse` payload |
| `400` | invalid `weekEnd` | client input bug |
| `503` | `WEEKLY_REPORTS_ENABLED=false` | runtime kill switch is active |
| `500` | backend failure | investigate Firestore/service failure |

Expected payload statuses inside `200`:

- `ready`
- `insufficient_data`

After rollout, a disabled response should only appear when the backend flag is intentionally off.

## Verification Before Rollout

1. Verify `GET /api/v2/users/me/reports/weekly?weekEnd=YYYY-MM-DD` returns `200` and matches the schema.
2. Verify a week with at least `4` valid logged days returns `status="ready"`.
3. Verify an empty or very sparse week returns `status="insufficient_data"`.
4. Verify invalid `weekEnd` returns `400`.
5. Verify disabling `WEEKLY_REPORTS_ENABLED` returns `503` with `detail="Weekly reports are disabled"` without backend crash.
6. Verify mobile renders loading, ready, insufficient-data, and unavailable states.

## QA Notes

### Backend smoke checks

```bash
curl -H "Authorization: Bearer <token>" \
  "https://<host>/api/v2/users/me/reports/weekly?weekEnd=2026-03-15"
```

Check:

- `period.startDay` and `period.endDay` form a 7-day closed window
- `status` is bounded
- `insights.length <= 4`
- `priorities.length <= 2`
- `summary` is short
- every insight and priority has `reasonCodes`

### Mobile smoke checks

Check on device or simulator:

1. Home shows a weekly report card only on today.
2. Tapping the card opens the weekly report screen.
3. `ready` shows summary, insights, and priorities.
4. `insufficient_data` shows the bounded empty state.
5. backend failure or disabled state shows unavailable state.

## What To Monitor After Rollout

### Backend

Monitor:

- request success rate for `/api/v2/users/me/reports/weekly`
- `400` rate for invalid `weekEnd`
- `500` rate for backend failures
- payload mix: `ready` vs `insufficient_data`

Interpretation:

- `500` above a low single-digit baseline means backend regression
- `insufficient_data` dominating for established users suggests thresholds are too strict or input quality regressed
- any disabled response while the flag is on indicates rollout/config drift
- repeated reports where both top insights say almost the same thing suggests selection redundancy regression

### Mobile

Monitor:

- screen opens from Home card
- unavailable fallback frequency
- optional bounded telemetry:
  - `weekly_report_viewed`
  - `weekly_report_cta_clicked`
  - `weekly_report_dismissed`

If mobile shows frequent unavailable state while backend is healthy, investigate contract drift first.

## Stability Criteria

Treat Weekly Reports v1 as stable when:

1. backend route returns valid bounded payloads in smoke checks
2. no contract validation regressions appear in backend/mobile tests
3. mobile renders all status states correctly
4. no sustained `500` failures appear after rollout

## Rollback

### Backend rollback

```env
WEEKLY_REPORTS_ENABLED=false
```

Effect:

- backend still serves the endpoint
- backend returns `503` with `detail="Weekly reports are disabled"`
- no weekly synthesis is computed

### Mobile rollback

```env
EXPO_PUBLIC_ENABLE_WEEKLY_REPORTS=false
```

Effect:

- mobile stops fetching the weekly report
- Home card disappears
- weekly report screen is no longer reachable from normal UI flow

## Coverage Checklist

Covered in tests today:

- ready
- insufficient_data
- disabled response count
- deterministic ordering
- insight selection
- priority selection
- bounded payload
- mobile ready / loading / insufficient-data / unavailable rendering
- contract fixture alignment across repos

## Open v1 Risks

- `day_completion_pattern` uses heuristic thresholds and may need tuning after observing real weeks.
- `weekEnd` closure is based on current UTC day, not a user-specific timezone boundary.
- `consistency` and `logging_coverage` are now intentionally de-duplicated in selection, but post-rollout observation should still confirm they feel distinct when both appear.
