# ADR: Canonical Firestore Meal Document

## Status
Accepted

## Date
2026-04-23

## Context
Meal documents diverged into overlapping shapes (`mealId`, `cloudId`, `timestamp`, `imageId`, `photoUrl`, `userUid`, local-only photo paths). This increased mapper complexity and made migrations fragile.

## Decision
Use one canonical Firestore meal schema where document id is the meal id and event time is `loggedAt`.

Canonical fields:
- `loggedAt`, `createdAt`, `updatedAt`
- `dayKey`, `loggedAtLocalMin`, `tzOffsetMin`
- `type`, `name`, `ingredients`, `source`, `inputMethod`, `aiMeta`, `notes`, `tags`, `deleted`, `totals`
- `imageRef` with `imageId`, `storagePath`, optional `downloadUrl`

Legacy fields are not part of canonical storage:
`mealId`, `cloudId`, `userUid`, `photoLocalPath`, `timestamp`, `imageId`, `photoUrl`.

Migration approach:
- canonical-write: new writes persist canonical fields only
- legacy-read: adapters read old docs and map `timestamp -> loggedAt` and `imageId/photoUrl -> imageRef`

## Consequences
- Single stable schema for Firestore meals.
- Backward compatibility without a destructive one-shot migration.
- Query/index strategy moves to `loggedAt` with temporary legacy timestamp fallback reads.
