# Validation record — GPS tracking Release 1 (schema only)

- **Date:** 2026-09-20 · **Release commit:** `176ee79` on `feat/gps-r1-schema`
  (cut from `main` at `e524365`, tenancy Release 5 live) · **Plan:**
  `docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md`, U1.
- **Surface:** one migration (`backend/db/migrations/016_gps_tracking.sql`),
  tenancy registrations, a new read-only `gps` verify check set, the local
  seed tail, integration tests, and `backend/requirements.txt` pinned. No API
  behaviour changes: nothing reads or writes the new objects until Release 2.
- **Not deployed.** This record covers the local certification; the
  production go/no-go rows below are to be filled at deploy time.

## Go/No-Go gates (plan §Operational Notes, Release 1)

| Gate | Observed | Verdict |
|---|---|---|
| Tenancy Release 5 live (015 in production) | Deployed 2026-09-20, PR #10; `docs/validation/2026-09-20-multi-tenant-release-5.md` | **met** |
| Double-apply rehearsal | psql re-apply on a database already at 016: exit 0, only the two `SET LOCAL … outside a transaction` warnings; the Lambda's single-transaction path (`pgconn.exec_`) double-applied by `test_migration_016_double_applies_cleanly` | **met** |
| `gps` verify set clean | Empty database: eight entries, zero counts / empty lists, no labeled errors (`test_gps_check_set_runs_clean_on_a_database_at_016`). Production: run `infra/scripts/verify-db.sh gps` after the migrate Lambda | **met locally** |
| Requirements pinned | `backend/requirements.txt` pinned to the 2026-09-20 containerized `sam build` resolution (nine top-level + starlette, pydantic, psycopg-pool); `pip check` clean; suites green on the pinned venv | **met** |

## Rehearsals

1. **Fresh local reset** (`scripts/reset-local-db.sh`): 001–016 applied with
   `ON_ERROR_STOP`, seeds 001–003 (016 tail included: 3 trail rows, 6
   exceptions, 6 events on the historical Simba run), 015 post-seed pass,
   post-seed integrity assertion now expecting **18** RLS tables — clean.
2. **Empty database** (scratch `saferide_016_rehearsal`, 001–016, no seeds,
   0 schools): the four tables have `relrowsecurity = t`,
   `relforcerowsecurity = f`, one `school_isolation` policy (`*`, role
   `saferide_app`), runtime-role SELECT/INSERT/UPDATE/DELETE grants; all seven
   composite FKs `convalidated = t` with the intended `ON DELETE` (cascade to
   run and bus, `SET NULL (student_id)` / `SET NULL (run_id)`); the three
   widened CHECKs carry the new values; 016 re-applied cleanly; database
   dropped.
3. **Migrate-Lambda shape:** the file is one implicit transaction; timeouts
   (`lock_timeout 5s`, `statement_timeout 90s`) at the top, new-table DDL
   first, hot-table ALTERs last, no empty-database guard, role-missing raise
   kept. The plan's four rollback-review properties hold: every CHECK is a
   verbatim union of the current list (011's 15 notification types, 010's 13
   incident types, 013's 47 audit actions); every new column on an existing
   table is nullable with no default; no constraint relates the position
   qualifiers to `current_lat/lng`; RLS and composite FKs touch new tables
   only.

## Test evidence

| Command | Result |
|---|---|
| `RUN_INTEGRATION=1 pytest tests/integration/test_rls.py test_tenancy_schema.py test_verify_handler.py -q` | 43 passed (10 new: catalog drift, cross-school visibility, 42501 on cross-school insert, no-GUC empty/refused, `INSERT … RETURNING` under RLS, owner-level composite-key refusal, policy + FK shape, sandbox teardown with rows in all four tables, double-apply) |
| `pytest -q` (backend unit, pinned venv) | 259 passed, 436 skipped |
| `RUN_INTEGRATION=1 pytest tests/integration -q` (pinned venv) | 436 passed, 2 skipped |
| `scripts/certify.sh` at `364fc36` (migration commit, pre-pin venv) | exit 0 — unit 259, integration 436/2 skipped, tsc clean, vitest 192, vite build, Playwright 81 passed, canonical seed restored |
| `scripts/certify.sh` at `176ee79` (release commit, pinned venv) | exit 0 — unit 259, integration 436/2 skipped, tsc clean, vitest 192, vite build, Playwright 81 passed (2.7 m), canonical seed restored |

## Decisions and divergences from the plan text

- **Composite FKs on every child reference.** The plan named composite
  `(run_id, school_id)` keys only; 015's convention gives *every* child
  reference a `(col, school_id)` key with the column-list SET NULL form. 016
  follows 015: `run_positions.bus_id` (cascade), `run_exceptions.student_id`
  and `run_exception_events.student_id` (`SET NULL (student_id)`),
  `driver_action_keys.run_id` (`SET NULL (run_id)`). The parent keys exist
  from 013, so this holds on an empty database too.
- **Explicit grants** to `saferide_app` on the four tables, although 013's
  default privileges already cover them — keeps the file self-sufficient.
- **`reset-local-db.sh`** (not in U1's file list): its post-seed assertion
  now counts the 18 RLS tables instead of 14, so a future migration that
  forgets a policy fails the reset.
- **Pinning surfaced one dependency drift.** On FastAPI 0.141.1 (what the
  Lambdas run) `include_router()` entries are lazy `_IncludedRouter`s, and
  `tests/api/test_scope_manifest.py` walked zero routes — with an empty
  manifest it would have passed vacuously. The walk now descends into
  included routers on both shapes and asserts a realistic route count.
  Nothing in the app enumerates routes; production was never affected.
- **Plan risk text is stale, not the plan.** "Production is at migration
  012; 013–015 exist only on the in-flight branch" predates the tenancy merge
  (PR #10) and Release 5 deploy; the dependency is met. The plan body is not
  edited during execution.

## Known observations (pre-existing, not changed here)

- Re-applying the whole of seed `003_local_snapshot.sql` onto a database
  where 015's `NOT NULL school_id` is already armed fails at the dump's
  pre-tenancy `live_buses` inserts (reported by the U1 rehearsal; not
  reproduced independently). The migrate Lambda applies seeds once by
  marker; `scripts/start-local.sh` re-runs seeds on non-reset starts. Use
  `scripts/reset-local-db.sh` locally.
- The API container image is baked (three weeks old) and keeps its own
  dependency resolution until rebuilt; certify syncs source only. The pinned
  set is what the Lambdas and the next image build resolve.

## Rollback position

Redeploy previous code. 016 is additive and rollback-neutral by
construction (see the four properties above); the tables sit unused until
Release 2. A failed Lambda apply rolls the whole file back with no marker —
re-invoke. Deploy outside route hours: the CHECK widenings and hot-table
column adds hold ACCESS EXCLUSIVE locks until commit.
