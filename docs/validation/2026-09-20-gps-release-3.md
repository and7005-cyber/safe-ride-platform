# Validation record — GPS tracking Release 3 (fix capture, classification, maps)

- **Date:** 2026-09-20 · **Release commit:** `6551069` on `feat/gps-r3-fixes`
  (cut from `feat/gps-r2-checks` at `9466812`; requires Releases 1 and 2) ·
  **Plan:** `docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md`, U6–U13.
- **Surface:** the driver app captures a fix per tap and sends every action
  as an idempotent envelope; the server stores the trail, serves a sourced
  position, classifies Board/Drop-off and Absent taps against the stop,
  keeps retention; staff and parents read one position model; per-school
  thresholds; plausibility flags; guides. Phase 1 only — no interval pings.
- **Not deployed.** Local certification only. The Release 3 go/no-go also
  carries the **driver briefing gate** (Kuumbai delivers and confirms; school
  informed), the **field check on two representative Android phones**
  (permission flow, fix quality on a real route, fix-wait budget, battery)
  and the **Cloud Map ID** confirmation — none of which this record can
  close; they are listed as open below.

## Commits

| SHA | Unit | Summary |
|---|---|---|
| `82a8905` | U6 | client fix capture (run-scoped watch, 15 s cache, budget, reasons), explainer, LocationStatusBanner, idempotent action envelope (`Idempotency-Key`, `fix`, `device_id`, `expected_stop_order`) |
| `58f744d` | U7 | `SchoolScope.session_id`; `normalise_fix`; `driver_action_keys` claim/seal with 409 `idempotency-mismatch` / `idempotency-in-flight`; trail row per tap; five served-position columns per writer; recorded no-op Arrive; bounded purge after Start Run + migrate-Lambda `gps-purge`; `public_run` strips the session id |
| `4423786` | U8 | one position fragment (`stale`, `gps_off`, `no_gps_for_run`, label) for the staff bus list and the parent Track/children/profile readers via run membership; fleet-map styling; parent Track marker; allowlist `{lat, lng, position_at, stale}` |
| `bf60d75` | — | seed GPS fixtures stamped relative to seeding time (they had aged past retention) |
| `8452277` | U9 | `classify_custody` (cap first, precedence stop-unverified > implausible > no-fix > too-coarse); custody-away per-stop exception with per-tap prompts; unverified rows once per (run, reason); exemption by open bypassed-stop membership; boarding undo arm + `boarding-corrected`; history-preserving retractions; status per child's latest event |
| `c69241c` | U10 | `classify_absent` (unverified first, then corroborated by prior absence / stop vicinity / afternoon school vicinity, else remote); absent-remote three-way prompt; told-me → absent-attested; call-now once per child per run with a post-commit outbox drain on every action and poll; absent-remote incident per (run, child); morning undo clears to no outcome, afternoon restores presumption; neutral corrections; call-now never retracted |
| `a7f90cf` | U11 | seven bounded `GPS_*` Settings fields with env aliases, mirrored as SAM template parameters (API, migrate and verify functions) and deploy-script overrides, with a parity test; `app.dao.school_thresholds` resolver for every reader; `SchoolPayload` five knobs (omitted keeps, null clears, 422 outside bounds); Tracking card; driver context `config`; audit `changes {old, new}`, no-op saves silent |
| `5fef576` | U12 | plausibility flags computed per tap against the previous fix and the planned stops (clock-skew, jump, speed, accuracy-zero, repeat-coordinates, at-planned-stop), stored on the trail row; a flagged fix reads unverified / implausible and corroborates nothing; one implausible-movement review row per run with an event per flagged fix; identical-accuracy arm dropped (iOS quantisation) |
| `6551069` | U13 | driver, admin and parent guides rewritten to the shipped labels; push-notification rows for the four new/neutral types; README offline claim removed; agent instructions updated; `docs/user-manual/driver-gps-briefing.md` (the data-protection notice and sign-off block); manual-vocabulary test pins it all (vitest 306) |

## Go/No-Go gates (plan §Operational Notes, Release 3)

| Gate | Observed | Verdict |
|---|---|---|
| Certify green at the release commit | `scripts/certify.sh` at `6551069`: exit 0 — unit 403, integration 512/3 skipped, tsc clean, vitest 306, build, Playwright 98 passed (4.1 m), canonical seed restored | **met** |
| Driver briefing delivered and confirmed by Kuumbai (school informed) | Briefing page `docs/user-manual/driver-gps-briefing.md` with the sign-off block; delivery is Kuumbai's | **open — human gate** |
| Guides updated | Driver, admin and parent guides, push docs, README, agent instructions (`6551069`); vocabulary test green | **met** |
| Field check on two representative Android phones | Not possible from this environment | **open — human gate** |
| Cloud Map ID (advanced markers) confirmed for production | Demo map id still in the frontend | **open — human gate** |
| Deploy outside route hours | Deploy-time | — |

## Decisions taken during execution

- **Explainer Cancel aborts the Start Run tap** (nothing is asked, no run
  starts); after Continue the run starts on grant, deny or timeout alike —
  the location outcome never gates.
- **Coarse fixes are served** with their accuracy kept so the read model can
  label them; clock-skewed and invalid fixes never are.
- **Any `expected_stop_order` mismatch is a recorded no-op** (ahead as well
  as behind).
- **`started_session_id` never leaves the DAO layer** (`public_run`).
- **`stale` applies to checkpoints too**: between taps more than 90 s apart a
  bus reads "last seen N min ago" in Phase 1. Honest per R27; a product call
  to confirm before deploy (Phase 2 pings make it continuous).
- **Parent Track's `run` is today's run the child is on** (was the home
  bus's latest run) and its stops follow that run's route.
- **GPS defaults became `Settings` fields in U11**, mirrored as SAM template
  parameters and deploy-script overrides (live-parity rule); the template
  parameters are additive with defaults, so no operator action is needed
  at deploy unless overriding.
- **Plausibility: the plan's "identical consecutive accuracy" arm was left
  out.** iOS reports quantised accuracies (5, 10, 35, 65 m), so identical
  consecutive values are normal there and the arm would have classified most
  iPhone Boards and Absents as implausible — silently disabling the custody
  and absent checks on iPhones. `repeat-coordinates` alone still catches a
  frozen or replayed feed. Flags are system-wide constants, not per-school.
- **An unjudged fix vouches for nothing:** when the plausibility step fails
  the custody/absent checks are skipped (not run flagless), and
  classification-failed rows are excluded from "bus seen at stop" and absent
  corroboration.
- **Served position keeps U7's rule** — only clock-skewed fixes are
  withheld; a jump-flagged fix is still served (and flagged for review).

## Test evidence

| Command | Result (final tree before certification) |
|---|---|
| `pytest -q` (backend unit, pinned venv) | 403 passed, 513 skipped (position rules: validation, custody, absent, plausibility; GPS settings parity; scope session id; migrate steps) |
| `RUN_INTEGRATION=1 pytest tests/integration -q` (API container synced) | 512 passed, 3 skipped (the skips are `test_parent_feeds` fixture skips on a freshly reset database) |
| `npx tsc --noEmit` | clean |
| `npm test` (vitest) | 21 files, 306 tests (fix capture 20, action envelope 15, position freshness, nudge queue, exception panel, tracking settings, manual vocabulary) |
| `npx playwright test` | 98 passed (driver-gps 7 incl. custody and remote-absent flows, driver-nudges 2, parent-track-position 3, admin-crud Tracking card, admin-run-exceptions 3) |
| `sam validate` (infra/backend) | valid; `--lint` fails only on the pre-existing W1011 (secret parameters via `!Ref`) |
| `scripts/certify.sh` at `6551069` | exit 0 — unit 403 passed / 513 skipped, integration 512 passed / 3 skipped, tsc clean, vitest 306, production build, Playwright 98 passed (4.1 m), canonical seed restored |

New integration coverage by unit: U7 13 (envelope, five columns, replay /
mismatch / concurrent / other-driver / no header, recorded no-op Arrive,
clock skew, invalid fix, End Run and force-close clearing, purge scope,
batch, lock skip and on-demand action, session id stamped but never
serialised, retention boundary under three session zones), U8 6 (parity
with exact parent key sets, cross-bus rider, two schools, no link,
retention, gps_off / no_gps_for_run), U9 (AE3, AE4, AE17, cap-first, five
children, undo after confirm, exemption incl. dismissed prompt and re-tap,
forced failure, AE9, stop-unverified and no-fix once per run, un-boarding
refused, afternoon parity), U10 10 (AE6, AE7, AE8, AE16, dismiss and
unanswered once, Board leaves pending, End Run silent, morning / afternoon
undo, mark-undo-mark once, lost fan-out re-sent once, coarse fix at a
coordinate-less stop), U11 4 (300 m school, null clears / omitted keeps,
context config, 403 / 422 / audit), U12 5 (jump, accuracy and pin rules,
one review row per run, flagged fix corroborates nothing, forced failure,
retention).

## Rollback position

Code-only on top of Releases 1–2. Rolling back to Release 2 leaves the
qualifiers set on buses whose pair the old writer nulls; the read rule
tolerates it and the next Release 3 deploy needs no repair (plan
§Documentation, Rollback).

## Deployed

- **2026-09-22, 17:49–17:56 UTC (20:49–20:56 EAT, outside route hours).**
  Merged to `main` in order via PRs #11 (Release 1, `1f408e4`), #12
  (Release 2, `40882e6`), #13 (Release 3, `6e57d87`) and #14 (Release 4,
  `f8a22fe`); one `just release` from `main` at `f8a22fe` deployed all four
  together: backend stack `saferide-backend` (af-south-1) `UPDATE_COMPLETE`,
  migrate Lambda result `applied: ["016_gps_tracking"]` with every earlier
  file skipped, frontend built against `https://api.saferidelive.co.ke`,
  uploaded and CloudFront invalidated.
- **Post-deploy verification:** `verify-db.sh migrations` lists
  `016_gps_tracking` (2026-09-22 17:53 UTC); `verify-db.sh gps` returns zero
  counts on every entry, `trail-rows-past-retention` 0 for both schools at
  the 90-day default, and `check-widenings` true for all three CHECKs;
  `GET /api/health` `{"status":"ok"}`; `https://saferidelive.co.ke` 200.
- **Observation (pre-existing, not from this deploy):** the production
  marker table also carries `015_tenancy_constraints_rls 2` and
  `015_tenancy_constraints_rls 3` (2026-09-20 11:15 UTC) — duplicate copies
  of the 015 file that were present at the Release 5 deploy and applied by
  the migrate Lambda; 015 is idempotent, so the schema is unaffected, and
  commit `1649d47` now refuses untracked migration files at deploy time.
- Deployed together with Releases 1, 2 and 4 at the user's decision; the human gates listed above (driver briefing delivered and confirmed by Kuumbai, the two-phone field check, the Cloud Map ID for advanced markers) were **not** closed before this deploy and remain open follow-ups.
