# Validation record — multi-tenant Release 3 (the data move)

- **Date:** 2026-09-20, evening window (0 open runs verified immediately
  before the act) · **Deployed artifact:** branch `release/tenancy-r3` =
  `0b7bca7` (U4 boundary) + cherry-picked `a9f6ac6` (the contract-reconciled
  014) = `575b5d0`, pushed · **Stack:** `saferide-backend`, af-south-1.
- **Restore point:** manual RDS snapshot
  **`saferide-pre-tenancy-move-2026-09-20`** of
  `saferide-backend-database-rto9a7nrm9ux`, status **available** at
  2026-09-20T08:54:31Z, taken and confirmed BEFORE the deploy. Restore per
  the `infra/README.md` runbook (new instance + template change).
  **Decision deadline: before the next morning's first run.** After that,
  the move is the new baseline (old code keeps working against it).

## Go/No-Go gates (plan §Operational Notes, Release 3)

| Gate | Observed | Verdict |
|---|---|---|
| Manual snapshot taken, id recorded | above, available pre-deploy | **GO** |
| Migrate output | `applied: ["014_tenancy_data_move"]`, status ok — every pin passed, one transaction; bootstrap correctly skipped (v1 applied) | **GO** |
| Numbers match the contract and the move log | before: 2 buses / 4 routes / 32 students / 33 runs / 64 incidents / 9 users → after: 2 / 4 / 32 / **19** / **48** / 9. Deleted: **14 Greenfield runs** (= the recorded contract), **1 fleet plan**, **16 run-linked demo incidents**. `demo_disabled = ["and7005@yahoo.it"]` exactly | **GO** |
| No NULL scope outside audit | all 13 school-owned tables report zero | **GO** |
| Greenfield absent | school-rows = 1 (Msingi Bora, code **MSB-001**); greenfield-row (by name) = 0 | **GO** |
| Provider count = 2 | active_providers = 2 | **GO** |
| Interim director signs in | `admin@test.com` login 200 with token (SSM credential, never displayed), `/me` role `admin`, session logged out after the check; admin **not** disabled | **GO** |
| Driver PIN sign-in | Not exercisable by the operator (real drivers' PINs are their own secrets; the demo driver is disabled by design). The PIN path's code is byte-identical this release and all active drivers' hashes are untouched (app_users count unchanged; only `and7005@yahoo.it` disabled). **Pilot confirms with the first morning sign-in — inside the restore window.** | **GO, with morning confirmation** |
| API health | `{"status":"ok"}` | **GO** |

## Notes

- The 16 deleted incidents were invisible to the preflight contract
  (incidents carried no school column pre-013); they hung off the 14 demo
  runs and their count is pinned in the move log.
- Sessions: only the disabled demo identity's sessions were revoked; real
  pilot users' sessions survive (rehearsed explicitly).

## Rollback position

Before the first morning run: restore the snapshot per the runbook and
redeploy Release 2 code. After it: the move is the baseline; do not restore.
