# Validation record — multi-tenant Release 1 (verification and infrastructure)

- **Date:** 2026-09-20 · **Deployed commit:** `7a837e7` (U1+U2 boundary, per the
  plan's release table) · **Stack:** `saferide-backend`, af-south-1 · merged
  branch: PR #10 (`2ad2f23`).
- **Surface:** backend infra only — migrate/verify Lambdas and template
  parameters. `backend/app` diff at the boundary touches only
  `core/tenancy.py`, `migrate_handler.py`, `verify_handler.py`; API behavior
  code is identical to what was already live (and PR #9, the branch base, had
  no backend changes).

## Prerequisite: provider bootstrap

`scripts/provider-bootstrap.sh` run for two Kuumbai accounts
(`and7005+kuumbai@gmail.com`, `and7005+kuumbai2@gmail.com`, both
"Andrea Natali"). Created `/saferide/totp-pepper` (once), wrote
`/saferide/provider-bootstrap` **version 1** (af-south-1), stored both
initial passwords as their own SecureStrings (names printed by the script;
never on screen or in this repo). First real run surfaced two script bugs —
a pipefail double-emit in the version probe and SSM-illegal `@`/`+` in the
password parameter path — fixed in `2656fe1` before the successful run.

## Go/No-Go gates (plan §Operational Notes, Release 1)

| Gate | Observed | Verdict |
|---|---|---|
| Migrate output: role step, no migrations | `applied: []` (12 skipped), `app_role: {role: saferide_app, synced: true}`, `provider_bootstrap: skipped ("provider_accounts table not present yet" — materializes at Release 2), seeds all skipped` | **GO** |
| Deploy fails fast without the pepper | Hard-fail paths exercised in `deploy-backend.sh`; pepper pre-existing at deploy time | **GO** |
| `verify-db.sh tenancy-preflight` recorded as the data-move contract | rc=0, full output summarized below | **GO** |
| API health unchanged | `https://api.saferidelive.co.ke/api/health` → `{"status":"ok"}` | **GO** |

## The recorded data-move contract (tenancy-preflight, 2026-09-20)

- **school-rows (2):** Greenfield Academy `23e0cb4c-95bb-4f4b-b7cb-4e7078aee144`
  (2026-07-02), Msingi Bora `bd2b680c-5cb1-42c1-902a-7b3aaa8a2769` (2026-07-14).
- **users-by-role:** admin 1, driver 4, parent 2.
- **null-scope-counts:** 0 on live_buses / live_routes / live_students / live_runs.
- **school-references:** buses → Msingi 2; routes → Msingi 4; fleet plans →
  Msingi 4, **Greenfield 1**; runs → Msingi 19, **Greenfield 14**;
  students → **Msingi 32**. *(Correction 2026-09-20: this record first claimed
  live_students was empty — a truncated display of the check output, not the
  data. The full row set always carried Msingi's 32 students.)*
- **audit-by-school:** Msingi 2 (audit is excluded from the move pins).
- **demo-identities present:** `admin@test.com`, `and7005@yahoo.it` (neither
  drives a live bus). `and7005@gmail.com`, `francis@saferide.test`,
  `mary@saferide.test` **do not exist in production**.
- **open-runs:** 0.

## Divergences from migration 014's pins — Release 3 blockers, by design

The three-exit pins in `014_tenancy_data_move.sql` were written against the
local snapshot; the contract above shows production has drifted. **014 as
committed would abort in production** (correct behavior — abort, not guess):

1. **Greenfield id mismatch:** 014 pins `greenfield = '5cae0000-0000-0000-0000-000000000001'`
   (the local seed id); production's Greenfield is `23e0cb4c-…`. First pin
   aborts. 014 must resolve Greenfield deliberately (by name + created-order
   assertion) before Release 3.
2. **Greenfield is not empty:** 14 runs and 1 fleet plan reference it; the
   zero-references pin (audit excluded) fails. Needs a decision: delete the
   demo school's run history and plan, or move them — before Release 3.
3. *(Withdrawn — see the correction above: Msingi has 32 students and the
   "target has students" pin holds as written.)*
4. **Identity inventory:** only 2 of the 5 pinned seed identities exist; the
   five-identity assertion and the four-email disable list must shrink to
   the observed set.

None of these blocks Releases 1–2. Release 2 (additive schema, migration 013)
is unaffected by data shape. **014 must be amended and re-rehearsed against a
production-shaped fixture matching this contract before Release 3 is
scheduled.**

## Rollback position

Per the plan: redeploy previous code; nothing schema-changing shipped. The
role (`saferide_app`) and SSM parameters are additive and inert to the
running API.
