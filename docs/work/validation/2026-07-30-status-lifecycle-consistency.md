# Validation record — status lifecycle consistency

- **Plan:** `docs/plans/2026-07-28-001-feat-status-lifecycle-consistency-plan.md`
- **Requirements:** `docs/brainstorms/2026-07-28-status-lifecycle-consistency-requirements.md`
- **Branch:** `feat/status-lifecycle-schema` (20 commits ahead of `main`)
- **Certified:** 2026-07-30, against the local stack

## What changed, in one line

The app stopped saying things nobody recorded. Boarding, drop-off, hand-over and
absence are now per-run facts with timestamps and an actor; the end-of-run sweep
that invented terminal statuses is gone; and a run cannot close while any child
on its roster has no recorded outcome.

## Releases

Shipped as three, because the migrate Lambda resolves migration files from its
own deployed package — so a migration cannot precede the deploy that carries it,
and schema had to go first on its own.

| Release | Contents | Migration |
| --- | --- | --- |
| **A** | Schema only (U1) | `010_status_lifecycle.sql` |
| **B** | Fleet state, vocabulary, lifecycle alerts (U9, U10, U16, U17) | none |
| **C** | Participation, closure gate, releases, force-close, surfaces, docs (U2–U8, U11–U15) | none |

## Certification

`scripts/certify.sh`, end to end, after `scripts/reset-local-db.sh`:

| Suite | Result |
| --- | --- |
| Backend unit | 151 passed, 231 skipped |
| Backend API integration (live stack) | 229 passed, 2 skipped |
| Frontend typecheck (`tsc --noEmit`) | clean |
| Frontend unit | 126 passed (12 files) |
| Frontend production build | clean |
| End-to-end (Playwright) | 55 passed |

Closing line: `✅ Certification complete: backend unit, API integration,
typecheck, unit, build, and e2e all green.` The script restores the canonical
demo seed as its last step; the local database is back to that seed.

The 231 skipped backend unit tests are the integration suite collected but not
run in that phase (they need `RUN_INTEGRATION=1`); they run in the phase below
it. The 2 skipped integration tests are pre-existing skips unrelated to this
work.

## Suites added by this work

| Suite | Covers |
| --- | --- |
| `backend/tests/integration/test_participation.py` | the per-run participation record (U2) |
| `backend/tests/integration/test_driver_correction.py` | same-run driver reversal (U5) |
| `backend/tests/integration/test_force_close.py` | office force-close, conditional arrival, contact obligation (U6) |
| `backend/tests/integration/test_absence_scoping.py` | period-scoped marks, precedence, the witness column (U8) |
| `backend/tests/integration/test_lifecycle_alerts.py` | office alerts for start/end and all four closure events (U16, U11) |
| `frontend/tests/unit/manualVocabulary.test.ts` | the published guides against the shipped vocabulary (U15) |
| `frontend/tests/e2e/driver-flow.spec.ts` (extended) | blocking list, stop-independent absent, note-required hand-over, no raw slugs (U13) |
| `frontend/tests/e2e/admin-crud.spec.ts` (extended) | force-close and the contact obligation; availability, not status (U14) |

## Defects found while validating, and fixed

Each of these was found by running something, not by reading code.

1. **The `today_count` timezone boundary.** The office's "Incidents Today" tile
   compared a `timestamptz` against `(now() at time zone 'Africa/Nairobi')::date`,
   which Postgres resolves at the server's *UTC* midnight. Every incident raised
   between 00:00 and 03:00 Nairobi fell outside "today". It hid for 21 hours a
   day and surfaced only because a certification run crossed the boundary. Fixed
   by converting both sides to a Nairobi date; regression test staged inside the
   window and verified to fail against the old predicate.
2. **The e2e suite had not been run since U4.** Two driver specs had been red
   that whole time — both ended a run after resolving one child, which the gate
   no longer permits. Unit and integration were green throughout, which is how it
   went unnoticed.
3. **`tsconfig` only included `src`.** No spec was ever typechecked, so a spec
   calling an unimported helper passed `tsc --noEmit` and failed at runtime.
   `tests` is now included; one type error hiding there is fixed.
4. **The suite exhausted the login rate limiter** (10 per account per 5 minutes)
   on setup, so the last specs to run failed on login timeouts unrelated to their
   subject. Setup signs in once per account and seeds the token; `auth.spec.ts`
   still drives the real form, because that is its subject.
5. **U7 over-reached into the create form.** Removing "Completed" from the run
   status control was right for editing a live run and wrong for recording a
   historical one — and U7's own delete carve-out depends on those bookkeeping
   rows existing.
6. **A stale in-app string.** The driver's absence dialog still said "Contact the
   office to undo" after U5 gave the driver an Undo.

## Known gaps, deliberately left

- **Whole-day driver absence has no control on the phone.** The endpoint accepts
  `whole_day` (U8/R17) but the driver UI never sends it, so a driver's mark
  always covers the current trip only. The office can record a whole-day
  absence. The guides say so. Closing this needs a UI decision about how a
  driver asserts something a parent told them.
- **`live_students.status` is still written and still vestigial.** Nothing
  derives from it since U3. It is maintained where cheap so it does not drift
  visibly, and no column has ever been dropped in this repo.
- **`update_run` still nulls omitted payload fields** other than status. Scoped
  out of U7 deliberately: the admin form sends complete payloads, and widening
  the fix would have meant deciding whether an admin may unassign a run's bus.

## Release gate — satisfied

The plan's one non-code condition for enabling the closure gate:

> The updated driver guide and a short briefing reach the pilot school's drivers,
> and the school confirms it, before the closure gate is enabled.

Confirmed done before Release C was deployed (2026-07-30). The failure mode it
guards against is worth restating, because it is the reason the condition exists:
a driver who discovers mid-route that End Run no longer works has one obvious
escape, and it is marking children absent — which tells those families their
child was never on the bus.

## Deployed to production — 2026-07-30

All three releases are live, in order.

| Release | PR | What went out |
| --- | --- | --- |
| **A** | [#5](https://github.com/and7005-cyber/safe-ride-platform/pull/5) | Migration 010. No behaviour change |
| **B** | [#6](https://github.com/and7005-cyber/safe-ride-platform/pull/6) | Derived bus status, arrival timestamps, office lifecycle feed, shared vocabulary. Backend + frontend |
| **C** | [#7](https://github.com/and7005-cyber/safe-ride-platform/pull/7) | Participation, the closure gate, both driver releases, force-close, the surfaces, the guides. Backend + frontend |

Verified against live production after each deploy: migration 010 applied with
all prior migrations skipped; bus availability backfilled correctly (both buses
were `idle`, so both became `in-service`); every run row carries `no_progress`,
`stale` and `contact_pending`; every student row carries a derived
`display_status`; every absence row carries `marked_period`; the force-close and
record-contact endpoints answer from their handlers rather than 404-ing as
unknown routes.

**Certify each release in isolation, not just the whole branch.** Release B's own
end-to-end suite was red when isolated: three specs still asserted pre-U9/U17
labels, and the fixes lived further down the branch in work that ships with C.
B could not have gone out green without commit `077377f`. The branch-level
certification hid this completely.

## Deployment notes

- Release A must be deployed and its migration run before B or C.
- Runtime-fetched SSM parameters must exist in `af-south-1`.
- `010_status_lifecycle.sql` is forward-only, like every migration here. Its
  three backfills (bus availability, today's participation, arrival timestamps
  for already-passed stops) are idempotent, and were re-verified by applying the
  file twice to a populated database at migration 009 — production's state — before
  Release A went out.
- **Deploy outside route hours.** The backfills are conditional and touched
  nothing on the day, but with a run in progress the participation backfill has
  real work to do, and that is the case it exists for.

## Open item, unrelated to this work

The seeded demo credentials `admin@test.com` / `test1234.` authenticate against
**production**. They were used for the post-deploy verification above. That is a
live admin account with a published test password on the real system, and it
should be closed before the pilot.
