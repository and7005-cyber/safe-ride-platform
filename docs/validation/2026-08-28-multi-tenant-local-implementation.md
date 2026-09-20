# Validation record — multi-tenant schools, local implementation phase

- **Branch / final commit:** `feat/multi-tenant-schools` (see `git log` for the
  per-unit history; one commit per implementation unit U1–U15 of
  `docs/plans/2026-08-23-001-feat-multi-tenant-schools-plan.md`, plus fixes).
- **Scope of this record:** the complete local implementation and its
  certification on the Docker stack. **No production release has been
  executed** — the five gated production releases (plan §Operational Notes)
  each produce their own gate evidence via `verify-db.sh` when run.

## Environment

Local stack (`docker-compose.local.yml`): PostgreSQL 16, API container running
the working tree under the **`saferide_app` runtime role** (LOGIN NOBYPASSRLS —
migration 015's row-level security binds every API query), Vite dev frontend.
Migrations 013–015 applied; the reset script applies 015 twice by design (its
data-dependent steps self-defer on an empty database and arm on the post-seed
pass).

## Certification (scripts/certify.sh, final run 2026-08-28, exit 0)

| Stage | Result |
|---|---|
| Backend unit (`pytest -q`) | 259 passed |
| Backend integration (`RUN_INTEGRATION=1 pytest tests/integration -q`, live stack, API on `saferide_app`) | 426 passed, 2 skipped (pre-existing conditional skips) |
| Frontend typecheck (`tsc --noEmit`) | clean |
| Frontend unit (vitest) | 192 passed / 15 files |
| Frontend production build (vite) | clean |
| End-to-end (Playwright, seeded two-school stack) | **81 passed** |
| Canonical seed restore | clean |

Dedicated isolation evidence inside those suites: the scope-manifest contract
(every route classified and guarded), `test_scope_enforcement.py` (guard
matrix incl. 50 alternating-school requests), `test_isolation_*.py` (per-family
two-school isolation with row-unchanged SQL checks), `test_rls.py` (16 tests as
`saferide_app`: no GUC → zero rows, 42501 on cross-school writes, composite-key
refusal even for the owner, audit split policy, catalog checks, double-apply),
`test_data_move_rehearsal.py` (production-shaped migration-014 rehearsal in a
scratch database), and e2e school-switcher/staff/provider/parent-pending
journeys.

## Defects found by the layered gates (all fixed on this branch)

1. **`INSERT … RETURNING` vs RLS** — PostgreSQL applies the SELECT policy to
   returned rows, so `record_audit`'s read-back failed on GUC-less
   connections, and offer accept/decline (staff-kind audit on the auth
   surface) failed its WITH CHECK: no staff offer could be accepted under the
   runtime role. Found by the first Playwright run against the RLS-live
   stack; fixed by minting audit ids client-side and arming the offer's
   school; pinned by a GUC-less `record_audit` test and a live-stack
   offer-accept regression.
2. **Parent scope bootstrap circularity** — the session enrichment derived a
   parent's school set through a `live_students` join on the GUC-less auth
   connection, which RLS blanks; it now reads the link rows' own
   `school_id` stamp.
3. **Unstamped child-table writers** (route stops, student-route links, run
   snapshots, participation, communicated stops, parent-cancel absences) —
   surfaced by NOT NULL + RLS, all stamped; plus the strict connection seam
   catching four background callees and two DAOs during U7.

## Known limits / deferred

- Production releases 1–5 not executed; `infra/backend/template.yaml` still
  points the ApiFunction at the master role and `SCOPE_HEADER_REQUIRED`
  remains default-false — both are the Release 5 deploy step (documented in
  `infra/README.md`).
- The migration-014 data move is rehearsal-proven, not production-proven; its
  pins abort on any drift by design.
- Provider bootstrap (`scripts/provider-bootstrap.sh`) must run before the
  first production deploy; the deploy script hard-fails without it.
