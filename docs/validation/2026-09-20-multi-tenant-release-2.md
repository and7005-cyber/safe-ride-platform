# Validation record — multi-tenant Release 2 (additive schema)

- **Date:** 2026-09-20 · **Deployed commit:** `d279a44` (U3 boundary) ·
  **Stack:** `saferide-backend`, af-south-1.
- **Surface:** backend; migration 013 only. The boundary diff against
  Release 1 touches no `backend/app` file — migration 013, the local seed
  tail (inert in production: all seeds marked) and one test file.

## Go/No-Go gates (plan §Operational Notes, Release 2)

| Gate | Observed | Verdict |
|---|---|---|
| `verify-db.sh migrations` lists 013 | `013_tenancy_schema` applied; 001–012 skipped | **GO** |
| Migrate output | `applied: ["013_tenancy_schema"]`, role synced, seeds skipped; **provider bootstrap v1 applied — `created:and7005+kuumbai@gmail.com`, `created:and7005+kuumbai2@gmail.com`** | **GO** |
| Preflight counts unchanged | Identical to the Release 1 contract, plus the two expected deltas of the bootstrap itself: users-by-role gains `(none): 2` (provider accounts hold no legacy role row); everything else byte-for-byte (2 schools, 32 Msingi students, Greenfield's 14 runs + 1 plan, 2 demo identities, 0 open runs, 0 NULL scopes on pre-013 columns) | **GO** |
| API health unchanged | `https://api.saferidelive.co.ke/api/health` → `{"status":"ok"}` | **GO** |
| Full certification on the expanded schema | Certified at the boundary commit during the branch build (suites green at `d279a44` and at every commit after; final branch certification 2026-08-28) | **GO** |

The two Kuumbai provider accounts now exist in production (identities only —
sign-in stays impossible until the Release 4 code ships the provider login;
initial passwords remain in their SSM parameters).

## Correction carried from Release 1

The R1 record's "live_students is empty" finding was false — a truncated
display of the check output. Msingi carries 32 students; migration 014's
"target has students" pin holds as written. The R1 record is amended in
place. Remaining genuine divergences for Release 3: the Greenfield id
hardcode, Greenfield's 14 runs + 1 fleet plan, and the 2-of-5 identity set.

## Release 3 preparation completed alongside this release

Migration 014 amended in `a9f6ac6` against the recorded contract
(name-based Greenfield, residue cleanup with pinned counts, subset identity
disable) and re-rehearsed green on a scratch database carrying the
production ids and the two-of-five identity set; full backend suites green
after the amendment (281 unit / 458 integration). The Release 3 deploy
builds from the `0b7bca7` boundary with this commit cherry-picked on top,
keeping the API code pre-scoped as the plan requires.

## Rollback position

Redeploy the previous code; additive schema stays (plan §Rollback).
