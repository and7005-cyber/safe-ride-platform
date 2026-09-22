# Validation record — GPS tracking Release 4 (Phase 2 streaming)

- **Date:** 2026-09-22 · **Release commit:** `6fa8e23` on `feat/gps-r4-pings`
  (cut from `feat/gps-r3-fixes` at `f01d689`; requires Releases 1–3) ·
  **Plan:** `docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md`, U14–U15.
- **Surface:** interval position pings from the driver app while a run is in
  progress and the app is foregrounded, bound to the auth session that
  started the run, paced and capped server-side; the screen wake lock;
  prominent staleness; real-time arrival offers and departure prompts from
  the trail. No parent notification is ever triggered by a ping.
- **Go decision:** the user chose to proceed with Release 4 on 2026-09-22
  (option 1 at the Release 3 hand-off) without waiting for the
  hardware-tracker timeline. The remaining go/no-go items are deploy gates
  listed below.
- **Not deployed.** Local certification only.

## Commits

| SHA | Unit | Summary |
|---|---|---|
| `4d621bc` | U14 | `POST /api/runs/driver/pings` (1–6 fixes; 404 / 403 / 409 `run-not-active` / 409 `session-mismatch` flagged once per run / 429 `ping-too-soon` paced against the newest ping row; window, (run, capture time) dedup, plausibility with `repeat-coordinates` exempt, forward-only served position from plain pings, no background task); stage default 100/50 and pings-route 20/10 throttles as template parameters with deploy overrides and parity tests; `useRunPings` (moving every interval, stationary every third, stop on hidden/no run, pause on session-mismatch until a tap), `useWakeLock` (request on Start Run, re-acquire on visible, hint on refusal); staleness prominent on fleet map, Buses card and parent badge |
| `50a6252` | U15 | `vicinity_state` (enter at the school's vicinity radius, leave at 1.5×, two-fix hysteresis, plain pings only, derived from the newest 30 plain pings — nothing stored); arrival offer `{stop_order, stop_name}` on the ping response and the driver context, rendered as a client-side "Arrive at <stop>?" card whose button posts Arrive with `target_stop_order` (each passed stop's bypass evaluated, only the target stamped); departure = U2's bypassed-stop prompt raised by the batch that completed the exit, office alert written in the ping transaction through a connection-level helper; latent NudgeQueue reconcile bug fixed |
| `a78d198` | — | e2e `purgeRun` removes the run's notification and incident rows |
| `6fa8e23` | docs | driver, admin and parent guides, the briefing, README and push docs for live pings, the wake lock and the arrival offer; the in-app Start Run notice and the Tracking card's ping-interval help brought into line; vocabulary test (vitest 343) |

## Go/No-Go gates (plan §Operational Notes, Release 4)

| Gate | Observed | Verdict |
|---|---|---|
| Certify green at the release commit | `scripts/certify.sh` at `6fa8e23`: exit 0 — unit 423, integration 529/3 skipped, tsc clean, vitest 343, build, Playwright 102 passed (6.6 m), canonical seed restored | **met** |
| Tracker timeline: hardware trackers more than the stated months away, or not full coverage | User decision to proceed (2026-09-22); timeline still to be recorded by Kuumbai | **decided — record the timeline at deploy** |
| Measured battery drain and screen-on willingness (field check) | Not possible from this environment | **open — human gate** |
| Request-rate estimate against Lambda concurrency | See below | **met on paper; confirm with the field check's real cadence** |
| Driver briefing amended for pings and the wake lock | `docs/user-manual/driver-gps-briefing.md` carries the live-tracking lines under What and When, "Your screen during a run" and the offer card (`6fa8e23`); delivery and sign-off remain Kuumbai's | **text met — delivery is a human gate** |

### Request-rate estimate

Assumptions: the pilot fleet is under ten buses; a moving bus posts one ping
batch every `ping_interval_s` (default 10 s) and one every 30 s when
stationary; every open driver app polls the context every 5 s; each staff
console polls buses every 5 s and lists every 15 s; each Lambda request
completes in well under a second.

- Pings: 10 buses × 0.1 req/s = **1 req/s** at peak, all moving.
- Driver context polls: 10 × 0.2 req/s = 2 req/s (already live today).
- Staff and parent polls: a handful of consoles at 0.2–0.3 req/s each.
- Total under **5 req/s**; with the account's concurrency floor of 10 and
  sub-second requests the pool sustains an order of magnitude more. The
  route-level throttle on the pings route (burst and rate parameters in the
  template) bounds a looping client far below the pool, and the server's
  half-interval pacing rejects a tight loop with 429 before it does any
  work.

## Decisions taken during execution

- **Pacing counts pings, not taps.** The 429 compares receipt time against
  the run's newest *ping* row, so a Board followed by a ping is never
  refused; the pace bounds the stream, which is what the concurrency risk
  is about.
- **Clock-skew band for pings.** Ahead of receipt by up to 30 s: servable
  (U7's tolerance — a phone clock a second fast must not lose every ping);
  30 s to 5 min: stored flagged `clock-skew`, never served; beyond: dropped
  as outside the window. Behind receipt: the run's creation minus a minute.
- **`repeat-coordinates` is exempt for pings.** A parked phone's stream
  legitimately repeats coordinates; flagging it would withhold every
  stationary ping from the map and spam the review row. Taps keep the rule.
- **Only plain pings move the dot.** Coarse, clock-skewed, flagged and
  unjudged pings are stored but never served — a dense stream loses
  nothing by it — whereas taps keep U7's rule (coarse served, labelled).
- **A run bound to no session** (office-created; nothing has tapped it yet)
  refuses pings with `session-mismatch` and flags nothing; the driver's
  first tap binds it and pings start.
- **The pings route declares its own HttpApi event** so the per-route
  throttle has a key; the proxy route is unchanged.
- **Wake lock after a refusal** is re-requested on the next visibility
  change or Start Run, not on every 5 s poll.
- **e2e hygiene:** `purgeRun` now deletes the run's notification and
  incident rows (SET NULL orphans broke a re-run count).
- **Vicinity is derived, never stored:** the newest 30 plain pings by
  capture time are replayed per stop on every accepted batch and, read-only,
  on every driver-context poll. A count rather than a clock keeps the entry
  in the window across a locked screen.
- **The departure fires from the batch that completed the exit**, not from
  a standing "left" state — otherwise every later batch would re-prompt a
  dismissed card. If that batch's evaluation faults, the next Arrive's U2
  check covers it (asserted).
- **Coarse pings are skipped, not pair-breaking,** in the vicinity replay;
  flagged pings are filtered before it.
- **The arrival offer is client-side** (no exception kind exists for it and
  none is wanted): the server offers, the client renders and remembers a
  dismiss per stop until the offer changes; the gate is offered like any
  stop and never raised on leaving.
- **Two write paths for the stop-bypassed office alert** by design: the
  Arrive path post-commit as before, the ping path inside its transaction
  because the pings route schedules no background task.

## Test evidence

| Command | Result (final code tree, before the docs pass) |
|---|---|
| `pytest -q` (backend unit, pinned venv) | 423 passed, 530 skipped (position rules incl. the vicinity replay 90; throttle parity) |
| `RUN_INTEGRATION=1 pytest tests/integration -q` (API container synced) | 529 passed, 3 skipped (`test_parent_feeds` fixture skips) |
| `npx tsc --noEmit` | clean |
| `npm test` (vitest) | 22 files, 337 tests (runPings 24, wake lock, nudge queue incl. the arrival offer) |
| `npx playwright test` | 102 passed (driver-pings 4: AE13 hidden/visible, staleness ageing, wake-lock stub and refusal hint, seeded-route replay through emulated pings with the offer, Arrive from the card and the departure card) |
| `sam validate` (infra/backend) | valid; `--lint` only the pre-existing W1011 |
| `scripts/certify.sh` at `6fa8e23` | exit 0 — unit 423 passed / 530 skipped, integration 529 passed / 3 skipped (3 m 29 s), tsc clean, vitest 343, production build, Playwright 102 passed (6.6 m), canonical seed restored |

New integration coverage: U14 `test_pings` 9 (AE15; out-of-order,
duplicate and six-in-a-batch; second-session refusal flagged once and
re-bound by a tap; over-cap and too-soon batches; coarse / skewed / flagged
stored not served; no background task — a due-but-unsent call-now stays
unsent across a ping), U15 +9 (AE14 end to end with one row, one event, one
incident and a quiet later Arrive; single fix does not enter, two do, the
band does not toggle, coarse and flagged change nothing; k+2 offer by name,
Arrive there evaluates k+1 and stamps k+2 only; locked screen then exactly
one; fallback to the next Arrive).

Note on the e2e runs: U14's first two full passes lost four and two tests
respectively to `page.goto: Page crashed` in unrelated specs (headless
Chromium under host load, each passing on immediate rerun); the third pass
and U15's single pass were clean.

## Rollback position

Code-only on top of Releases 1–3; rolling back to Release 3 leaves ping rows
in the trail (purged by retention like any row) and the tap-based position
model unchanged. The template's throttling parameters are additive with
defaults.
