# Validation record — GPS tracking Release 2 (GPS-free checks and surfaces)

- **Date:** 2026-09-20 · **Release commit:** `ef73be1` on `feat/gps-r2-checks`
  (cut from `feat/gps-r1-schema` at `db1b331`; requires Release 1's
  migration 016) · **Plan:** `docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md`,
  U2–U5.
- **Surface:** code-only. Backend: per-stop outcome predicate, bypassed-stop
  exception from Arrive, staff exception routes, server-owned prompts,
  auto-resolution, `exception_count`; the dormant position path removed.
  Frontend: driver nudge card on every driver tab, "N to review" badge,
  run-report exceptions panel, incident vocabulary. Docs: admin guide,
  push-notification table. No GPS fix is captured yet (Release 3).
- **Not deployed.** Local certification only; production go/no-go rows fill
  at deploy time.

## Commits

| SHA | Unit | Summary |
|---|---|---|
| `93478dd` | U5 | dormant `POST /driver/position`, `write_position`, `notify_bus_position`, `remaining_student_stops`, in-module haversine and `BUS_APPROACHING_RADIUS_M` removed; one haversine (`geo_service`) |
| `dd330d6` | U2 | `unaccounted_at_stop` shares the gate's SQL; Arrive evaluates stop N−1 in a savepoint; one exception per (run, stop), derived open/resolved; office-only `stop-bypassed` incident deduped on insert; staff list/review routes with `exception-reviewed` audit |
| `b68b0e4` | — | local rehearsal drift: 013 double-apply now re-applies 015/016; `gps` verify set gains `check-widenings` |
| `1997850` | U3 | `pending_prompts` in the driver context; shown/respond routes with structured 409 codes; auto-resolution per action; resolution by stop membership; nudge card + store + cue |
| `ef73be1` | U4 | `exception_count` (stored `reviewed_at`, absent for drivers); badge on Runs and Dashboard; run-report panel with Mark reviewed; alert labels; admin guide |

## Go/No-Go gates (plan §Operational Notes, Release 2)

| Gate | Observed | Verdict |
|---|---|---|
| Certify green at the release commit | `scripts/certify.sh` at `ef73be1`, run 1: unit 254, integration 463/3 skipped, tsc, vitest 230, build green; Playwright 85 passed / 1 failed — `admin-plan.spec.ts:256` timed out on `page.goto("/auth")` during sign-in (fleet-plan restore; unrelated to this release; the run was slower than usual at 3.3 min). Re-run of that spec alone: 5 passed in 21.6 s. Run 2 at the same commit: exit 0 — unit 254, integration 463/3 skipped, tsc clean, vitest 230, build, Playwright 86 passed (2.8 m), canonical seed restored | **met** |
| Seeded-run walk-through in the browser | Done after certify run 2 on the seeded local stack (Vite dev server + synced API), sessions injected the way the e2e helpers do (API token in storage, no form typing). See "Walk-through" below | **met** |
| Release 1 (016) deployed first | Sequencing note for the deploy; the tables must exist | to confirm at deploy |

## Walk-through (seeded driver Daniel Kamau / bus Simba, Greenfield admin)

1. Driver Run page → route "Express 1 — Morning" → Start Run: run in
   progress, 0/4 stops, "Still to account for (3)": Faith Achieng, Happiness
   Kenesa, Kevin Mwangi.
2. Arrive → 1/4 (Kilimani struck through), no prompt.
3. Arrive → 2/4: the nudge card appears above the page content — "Stop 1:
   Kilimani, Nairobi · Faith Achieng has no record. Mark boarded or absent?"
   with Absent / Boarded per child and its own dismiss.
4. Board tab: the same card is at the top of Student Boarding (shared driver
   layout), 0 boarded / 3 remaining.
5. "Boarded" on the card: within one poll the card is gone, Faith reads
   "On bus", 1 boarded / 2 remaining.
6. Database at that point: one `stop-bypassed` exception at stop 1,
   unreviewed; ledger = prompt `answered/resolution` (delivered and shown)
   plus one `resolution` event naming the child; one lifecycle incident,
   acknowledged, `student_id` NULL: "Stop 1 (Kilimani, Nairobi): no record
   yet for Faith Achieng." — no coordinates.
7. Admin Run History: today's Simba row "2/4 stops · 1/3 boarded · In
   progress · **1 to review**"; the seeded 2026-06-17 run shows "5 to
   review".
8. Run Report: "Stop exceptions · 1 to review" → "Stop passed without
   outcomes · Resolved", note, Stop "1 · Kilimani, Nairobi", Children "Every
   child at this stop now has an outcome", ledger lines "prompt answered:
   answered by the driver recording the children" and "a child at this stop
   was recorded", "Raised 20 Sep, 16:13", Mark reviewed.
9. Mark reviewed → "Reviewed · by Greenfield Admin · 20 Sep, 16:15", header
   "All reviewed", row kept; Escape → the badge is gone from today's row
   without a reload; only the seeded "5 to review" remains.
10. Alerts page: "Stop passed without outcomes · Acknowledged — Express 1 —
    Morning (morning) — Simba: a stop was passed without outcomes. Stop 1
    (Kilimani, Nairobi): no record yet for Faith Achieng." — pre-acknowledged
    like every lifecycle row and excluded from the unacknowledged counter.

The walk-through run was removed afterwards with `scripts/reset-local-db.sh`
(canonical seed restored, including the audit row the review wrote).

## Test evidence (final tree before certification)

| Command | Result |
|---|---|
| `pytest -q` (backend unit, pinned venv) | 254 passed, 464 skipped |
| `RUN_INTEGRATION=1 pytest tests/integration -q` (API container synced) | 463 passed, 3 skipped (the third skip is `test_parent_feeds.py:160`, environmental: no feed rows older than 24 h on a freshly reset database) |
| `npx tsc --noEmit` | clean |
| `npm test` (vitest) | 17 files, 230 tests |
| `npx playwright test` | 86 passed (driver-nudges 2, admin-run-exceptions 3 new) |

New integration coverage: U2 15 tests (predicate arms incl. cross-bus and
afternoon undo, AE5 end to end, catch-up idempotency, fault injection on
`run_exceptions`, gate / emptied / last stop, reopen on read, AE10 scoping and
roles, idempotent review, error paths, parent readers), U3 11 tests
(delivery order and `delivered_at`, ownership 403/404, replay and the two
409 codes, `shown_at`, auto-resolution per action, resolution by membership,
event-id hint), U4 1 test (`exception_count`).

## Decisions taken during execution

- **Resolution by membership, not by card id.** U3's brief first keyed the
  Board/Absent "resolution" on the card's event id; the plan's decision is
  membership ("whether or not the card is still on screen"), so any outcome
  for a child at the stop of an open bypassed-stop exception of this run is
  recorded as its resolution. The `event_id` stays on the payloads as a hint.
- **"Last stop" is the highest stop order**, which is never N−1 of any
  Arrive; in the morning the last child stop is therefore evaluated when the
  bus arrives at the school gate, and the gate itself never raises.
- **Undo does not re-prompt.** After an undo reopens a bypassed-stop
  exception the prompt stays `answered/resolution`; the office sees it open
  and the End Run gate still blocks. U9 revisits undo.
- **Card shortcuts are one-tap** (F3); the Board page still confirms Absent.
- **Kind labels live in the panel** (`EXCEPTION_KIND_LABEL`), the two alert
  types in `statusVocabulary.ts`; a unit test pins the shared wording.
- `AlertsPage.tsx` needed no edit (labels come from the vocabulary map).

## Local rehearsal fragilities found (not production issues)

- The 013 double-apply rehearsal in `test_tenancy_schema.py` recreates the
  audit CHECK with 013's list: it reverted 016's widening on every full
  integration run (fixed in `b68b0e4` by re-applying the later files), and
  it cannot run at all while an `exception-reviewed` audit row exists
  (013's `ADD CONSTRAINT` validates existing rows). Integration sandboxes
  purge their audit rows; the admin e2e spec removes the audit row of the
  seeded review it performs. The migrate Lambda never re-runs a marked file,
  so production is unaffected; a future rehearsal design that re-applies
  every migration in a scratch database would remove the fragility.

## Rollback position

Code-only: redeploy the previous API and frontend. The 016 tables stay in
place and unused. Older driver clients ignore `prompts` on the Arrive
response and `pending_prompts` in the context; older staff clients ignore
`exception_count` and `exceptions` on the report.
