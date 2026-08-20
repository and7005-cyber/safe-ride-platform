# Validation record — students-first fleet plan drafting

- **Plan:** `docs/plans/2026-08-19-001-feat-fleet-plan-drafting-plan.md` (deepened 2026-08-19; two ce-doc-review rounds on the origin, one headless + one interactive round on the plan)
- **Requirements:** `docs/brainstorms/2026-07-30-fleet-route-planning-requirements.md` (origin R1–R24, F1–F5, AE1–AE8)
- **Branch:** `feat/fleet-plan-drafting` — 22 commits ahead of `main` at `350e791`
- **Certified:** 2026-08-20, four full `scripts/certify.sh` runs against the local stack

## What shipped, in one line

Route planning inverted to students-first: the system drafts a whole-fleet plan
from enrolled students' homes under hard constraints and exact lexicographic
objectives, the admin reviews, edits, and applies it as one gated, audit-logged,
provider-free transaction with a one-level restore, families are notified only
on material change against a communicated baseline, bulk enrolment with geocode
triage is the front door, and mid-year changes arrive as slot-in proposals.

## Units delivered

All twelve planned units (U1–U12) plus U13, added during execution when a trace
gap surfaced: origin R15 requires manual live-route edits to notify, but the
plan had scoped notification writing to apply/restore only. U13 wires the
manual-edit surfaces through the same baseline diff.

Two plan-level Open Questions were resolved during execution and are reflected
in the code: the unplaceable→placed interaction became the `assign` edit action
(backend) plus a per-row Place dialog (UI); the gate-dialog escape threshold is
`GATE_ESCAPE_THRESHOLD = 10`.

## Certification (final run, `scripts/certify.sh` end to end)

| Suite | Result |
| --- | --- |
| Backend unit | 180 passed |
| Backend API integration (live stack) | 290 passed, 2 skipped (pre-existing skips) |
| Frontend typecheck (`tsc --noEmit`) | clean |
| Frontend unit (vitest) | 153 passed (13 files) |
| Frontend production build | clean |
| End-to-end (Playwright, serial) | 62 passed |

Integration grew 229 → 290 tests and e2e 55 → 62 across this work. One test-
infrastructure change was required: the certification suite's account churn
exceeded the 50/h signup net in a single run, so the two coarse per-IP auth
budgets now scale by `AUTH_IP_RATE_MULTIPLIER` (default 1 — production posture
unchanged, pinned by test; the local compose file opts into 20).

## Defects found by running things, and fixed

1. **First apply was the one un-restorable act.** The `previous`-row creation
   was gated on an existing applied row, so a first apply silently discarded
   the as-evolved capture — hand-built routes would have been unrecoverable,
   violating R21. Caught by the first execution of the new e2e suite; the
   testing reviewer independently found it by static analysis the same hour.
   Fixed (`269d006`): the capture always lands, inserting a fresh previous row
   when no applied row exists.
2. **An e2e locator ambiguity** (child name appears twice in a leg panel) —
   spec scoped to the stop row.

## Code review (Tier 2, 13 reviewers, validator wave)

`ce-code-review` in agent mode over the full branch diff: 4 always-on personas
+ security, performance, api-contract, data-migration, reliability, adversarial
+ agent-native, learnings, and deployment-verification agents. Artifacts:
`/tmp/compound-engineering/ce-code-review/20260820-101407-437c50ae/`.

15 findings validated (8-validator independent wave: 7 confirmed, 1 rejected;
4 more direct-verified at anchor 100; 3 carried two independent reviewers).
**All 15 were applied and re-certified** — none accepted as residual risk:

- apply no longer half-migrates a confirmed-enrolled child (links severed,
  bus/pickup nulled, chain legs exempt — mirroring restore)
- review-edit recomputes run concurrently (worst-case provider wall clock is
  one 8 s call, not four, under the plan lock)
- a bus entering a multi-trip chain after drafting 409s as fleet drift instead
  of an unhandled unique violation
- global lock order "plan rows before route rows" closes the slot-in-accept vs
  apply/restore ABBA deadlock
- apply/restore feed bodies and baselines carry only the recipient child's own
  stop label — never a sibling-cluster's joined names
- place-change diffs are physical (collapse-radius haversine; names compared
  only when both sides are coordinate-less), ending a traced spurious-push
  cycle between the two baseline writers
- apply and restore confirmations are discoverable from read paths
  (`basis_drift` on review, `drift` on the previous plan) and the restore UI
  gained a real gate dialog instead of a blind POST
- the idempotent (gateway-retry) path shows "Already applied" instead of
  interpolating undefined counts
- the direction-aware ride-time walk is one shared helper with one decided
  short-legs behavior (pad + degraded flag), replacing three divergent
  implementations of which one could crash and one silently misaligned
- slot-in generation is two-phase (no transaction across provider calls) with
  a fingerprint discard and a matrix size cap (`SLOT_IN_MAX_MATRIX_POINTS`)
- bulk duplicate detection is one query per request; a stale cached admin tab
  gets an actionable 400; unknown-school answers align on 404
- the two structural P1s — `fleet_plan_dao.py` (3.3k lines) and
  `PlanReviewPage.tsx` (1.2k lines) — were split behavior-preservingly
  (byte-identical bodies, `vars()`-diffed facade, identical testid inventory)
  and proven by a full green certification

## Known gaps, deliberately left (with the reviewers' words)

- **Global admin role**: any admin token reaches every school's PII and
  live-route mutation; per-school scoping and step-up auth are pre-existing
  platform posture, unchanged here.
- **Audit is append-only by convention** (no trigger/grant enforcement);
  plan-document reads are admin-only but not audit-logged (the pin map is) —
  named follow-up.
- **Zero-route apply+restore previous-row race**: a concurrent apply and
  restore on a school with no routes can still conflict on the previous-row
  delete/promote — pre-existing relative to the lock-order fix, single-admin
  pilot posture.
- **Per-route geometry-refresh failure isolation** is proven plan-wide
  (keyless degrades uniformly), not per-route.
- **Solver determinism under CPU contention**: the seed reproduces a draft
  bit-identically only when the restart budget completes inside the 5 s cap.
- **Cut coverage**: no test for a same-school chain rider through draft→apply,
  concurrent fan-out duplicate suppression, or apply latency at the ~500-student
  revisit threshold.

## Post-deploy monitoring & validation

The plan's Operational Notes carry the full release mapping (five releases by
commit SHA — release boundaries are operational discipline, no feature flag),
per-release go/no-go SQL, rollback story, and monitoring lines. The
deployment-verification agent's sharpened checklist (exact queries, expected
values) is in the review run artifacts. Non-negotiable gates before deploys:

1. **Release 1 (migration 011) needs a named RDS verification path** — none
   exists in `infra/` (private subnets, no bastion); provision a read-only
   verification Lambda or SSM tunnel first, or go/no-go degenerates to "the
   Lambda returned success".
2. **The published demo-admin credential (`admin@test.com`) must be closed
   before Release 3** (the apply-bearing release) — open item carried from the
   status-lifecycle validation record.
3. **Decide U13's release explicitly** (it can seed baselines from Release-2
   surface, changing what "first apply seeds the whole school" means).
4. **Release-3 rollback order is restore-first, then code rollback** — rolling
   code back first removes the restore endpoint while the previous-plan row it
   needs still exists but is unreachable.
5. **The first production apply is its own scheduled act**: evening, after the
   PM run, admin present, drivers and school briefed, baseline captured,
   restore rehearsed.

## Skipped checks, with reasons

- Real-Google-key drafting/refresh verified only via the shipped provider
  conventions; the keyless deterministic path carries all automated coverage
  (house rule — a real key in local `.env` breaks the degraded-path suite).
  First live verification happens at Release 2 against production geocodes.
- Manual in-browser pass of the new admin surfaces was not performed in this
  session; the 62-spec serial e2e suite drove every flow end to end instead.
- No linter is configured for the backend (none exists in the repo); frontend
  is gated by `tsc --noEmit` + vitest + build.
