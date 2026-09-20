# Validation record — multi-tenant Release 5 (constraints, RLS, runtime role)

- **Date:** 2026-09-20 · **Deployed commit:** `17173d2` (main HEAD: 015
  restored + ApiFunction on `saferide_app` + `SCOPE_HEADER_REQUIRED=true`;
  migrate/verify Lambdas stay on the master role by design).
- **Operator's call, recorded:** the plan's full-pilot-day-before-R5 gate was
  compressed — R4 and R5 shipped the same day on the operator's instruction.
  A real director existed before the act (2 active directors pre-retirement)
  and both provider accounts stand; the coordinator account remains to be
  created in-app.

## Go/No-Go gates (plan §Operational Notes, Release 5)

| Gate | Observed | Verdict |
|---|---|---|
| Migration 015 applied | Applied; `VALIDATE CONSTRAINT` raised nothing (zero violations); NOT NULL + composite keys + policies live | **GO** |
| `tenancy-rls` under `SET ROLE` | `current_user=saferide_app` (session_user master), RLS enabled (not FORCEd) + ≥1 policy on all 14 tables, **zero rows visible with no GUC on all 13**, zero foreign rows under the school GUC, provider health fn returns counts (no rosters) | **GO** |
| Zero NULL-scope rows after the move | zero on all 13 | **GO** |
| API on the runtime role | ApiFunction `DATABASE_URL` → `saferide_app`; health ok; logs clean (no errors, no 42501) post-swap; pilot traffic live (runs_today 1, staff writes this morning) | **GO** |
| Header fallback off | `SCOPE_HEADER_REQUIRED=true` on the ApiFunction env (the shipped frontend sends the header everywhere on the staff surface) | **GO** |
| Seeded admin no longer signs in | `admin@test.com` login → **401** (disabled, hash rotated to a sentinel, sessions revoked by 015) | **GO** |
| Full certification | The deployed tree is the certified branch (2026-08-28: 259 unit / 426→ integration / 192 vitest / 81 e2e) | **GO** |

## Incident: file-sync duplicates rode into the migrate package

The migrate output listed three applied entries: `015_tenancy_constraints_rls
2`, `… 3`, and the real file. Two untracked, byte-identical duplicate copies
(file-sync/Finder conflict copies created during the morning's branch
switches; this working copy lives in a synced Documents folder) were bundled
by `sam build`, which packages the directory, not the git tree. Because 015
is double-apply-safe by design, the effect was one real application plus two
no-ops. Residue: two junk marker rows in `saferide_migrations` (cosmetic;
left in place). **Fix shipped (`1649d47`):** the deploy script now aborts
when the migrations/seeds directories contain any file git does not track.

## Point of no return

Per the plan: once the second school holds data, no release before Release 4
is deployed again. The certified artifacts are tagged by the release
branches (`release/tenancy-r3`, `release/tenancy-r4`) and `main` HEAD.

## Remaining human steps

Coordinator account creation (director, in-app); the second school is now
allowed to be created — from the provider console only.
