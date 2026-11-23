# Validation record — production hardening Release 1b (code)

- **Status: draft** (drafted 2026-11-23, before the code review). Nothing is
  tagged or deployed; every gate below is `open`.
- **Date:** planned 2026-12-04 · **Release commit:** to be tagged
  `release/1b` on the `feat/hardening-r1b` tip after the code review and the
  final certify (the branch is cut from `main` at `42a20b7`, which holds all
  of Release 1a, live since 2026-11-19) · **Plan:**
  `docs/plans/2026-11-16-001-feat-production-hardening-plan.md`, U7–U12 and
  U13 (U16 shipped in Release 1a, D8), as amended by the execution decisions
  D1–D27 (the Release 1a record) and D28–D54 below.
- **Surface:** parent password recovery by email (SES v2, inline, one
  attempt, purpose-scoped single-use tokens, a per-account cap) and by the
  office (**Reset password**, **Confirm and reset**); sign-up that links
  nothing until its owner verifies from a signed-in session; the office's
  email change requiring verification again; **Change password** for every
  role except PIN sessions; the public `mailEnabled` flag and the mail-off
  screens; driver sign-in bound to the school's driver code (typed, link or
  QR), school-salted six-digit PINs with upgrade-on-login, a per-school lock
  keyed on (school, source) with `Retry-After`, the one-school rule, the
  Drivers page's driver code card with **Regenerate**; request ids, redacted
  error logging and the "(Reference: …)" suffix; the database-aware health
  check with the version; no API docs outside local; the API's security
  headers; the nightly retention job on the migrate function from EventBridge
  Scheduler with its heartbeat; log metric filters and alarms for failed
  mail, PIN failures, PIN locks and `school-required`, and the 4xx alarm moved
  to the app's non-404 answers. **No migration** (D1: no 018). Documentation:
  the guides' final pass and their vocabulary pins, the Release 1b deploy
  checklist in `docs/runbooks/hardening-release-checklists.md`, the 1b
  rollback notes in `infra/RESTORE.md`, and this record.

## Commits

`git log --oneline main..HEAD` on `feat/hardening-r1b`, plus U13.

| SHA | Unit | Summary |
|---|---|---|
| `48778e4` | U11 | A raw ASGI middleware puts the Lambda request id (never an inbound header) on every response as `X-Request-Id`, with HSTS, `nosniff`, `Cache-Control: no-store` and `default-src 'none'; frame-ancestors 'none'`; an unexpected error leaves one `unexpected-error` record (method, path, request id, class, traceback frames, and for database errors the SQL state, constraint and table only — no `exc_info`), redacted again by a root filter; the 500 body carries the request id and the client appends "(Reference: id)" to 5xx messages; `GET /api/health` runs a bounded, cached `select 1` and reports the version (503 with `db: error`); interactive docs only locally |
| `17dd95a` | U7, U8 | `MailService` with an SES v2 transport (one attempt, ten-second timeouts) and a simulated one (local dev routes for the last links); a failed send logs `mail-send-failed kind=…` without the recipient and never fails the request; reset (30 minutes) and verification (24 hours) tokens scoped by `purpose`, at most three mails per account per hour; forgot-password's neutral answer; sign-up issues no session — a new address gets an unverified account (D1) that links nothing and raises no incident until its signed-in owner verifies, an existing address gets the "you already have an account" mail; the emailed reset ends every session, drops push registrations, verifies the address and signs in (not providers, who still pass the second factor); the office's email change requires verification again and notifies the old address; `GET /api/push/config` carries `mailEnabled`, and the screens send people to the office while it is false |
| `de59f8d` | U9 | Either staff role issues a parent a 16-character temporary password (Crockford alphabet, read out in groups of four, typed any way) through a helper shared with the staff reset: forced change within 72 hours, sessions revoked, tokens voided, push registrations removed, a neutral notice mail and a `parent-password-reset` audit row with counts only; a neutral 409 for anyone with a membership at any school or a provider account; **Confirm and reset** verifies an unverified sign-up matching a slot email, links it and issues the same temporary password (`parent-email-confirmed` and `parent-password-reset` rows); **Change password** for every role except PIN sessions, marked in the session token, keeping the current session. Also fixes the pre-existing shared-parent detection under row security (below) |
| `85030e3` | U10 | The phone is set up once with the school's driver code (`GET /api/auth/schools/{code}`, typed, link or QR); a PIN without a school is refused with `school-required`; new PINs are six digits, refused when trivial, stored as `v2:k1:` + HMAC(pepper, school id + ":" + PIN), so the same PIN may exist at two schools and the in-school check no longer confirms another school's PINs; a legacy PIN signs in once and is rewritten to the salted form; more than twenty failures from one source at one school in fifteen minutes answer 429 `school-pin-locked` with `Retry-After`; every failure writes a row and a `pin-failure school=<code>` line; a director regenerates the code; one active driver membership per PIN holder; fixed driver codes in the seeds and e2e fixtures; `qrcode.react` 4.2.0 |
| `c2bd543` | U12 | EventBridge Scheduler invokes the migrate function at 01:30 Africa/Nairobi with `{"action": "retention"}` (no async retries, 600-second timeout); the run deletes in bounded skip-locked batches — feed rows past 90 days, used or expired tokens, expired sessions and pre-auth tokens, PIN-failure rows, idempotency keys, unverified parents older than 30 days with no link, membership, provider account or audit and incident reference — then purges the trail per school under a time budget; never incidents or the audit log; an `ops_job_runs` row and an EMF `RetentionRunSucceeded` point per run (a budget stop is success); the heartbeat, Scheduler target-error, mail, PIN, PIN-lock and `school-required` alarms on metric filters; `ApiClientErrorsBurst` moved in place to the app's non-404 4xx (D47) |
| uncommitted | U13 | The guides' final pass (parent, driver, staff, provider, index) in the app's words and five new `manualVocabulary` tests; the stale texts (index date, `agent-implementation-instructions.md`'s PIN lines, the seed-002 header); the `MailEnabled` parameter description; the Release 1b deploy checklist; the 1b paragraph of `infra/RESTORE.md`, "Code rollback"; this record |

## Go/No-Go gates (plan §Operational Notes, Release 1b checklist)

Each gate is a step of the
[Release 1b deploy checklist](../runbooks/hardening-release-checklists.md#release-1b-deploy-checklist).

| Gate | Observed | Verdict |
|---|---|---|
| Release 1a stable for a week (from 2026-11-26): alarms `OK` in both regions, both subscriptions confirmed, SES identity verified with DKIM and MAIL FROM `SUCCESS`, no unexplained ERROR line | Through 2026-11-23 (the 1a record, "+95 h"): all `OK` except two `ApiClientErrorsBurst` pairs from scanners (404s, D47); subscriptions confirmed; `true`/`SUCCESS`/`SUCCESS`; 0 ERROR lines | **open** |
| SES production access state recorded and the `MAIL_ENABLED` decision taken, with the UI state that ships | 2026-11-23: still `DENIED` in the sandbox (the case is in the 1a record); by the checklist, sandbox means `MAIL_ENABLED` unset (`false`) and the mail-off screens | **open** |
| `hardening` baselines: `drivers-with-several-driver-memberships` 0, every PIN `legacy_hmac`, `unverified-parent-accounts-by-age` all 0, `token-rows-by-purpose` `verify` all 0, `last-retention-run` empty | At 1a (2026-11-19 09:21Z): 0; Msingi Bora 4 and Voluntari Academy 1 `legacy_hmac`; 0; 0; empty. To be read again before the deploy | **open** |
| The active drivers of both production schools listed by name (outside the repository) with who gives each the PIN and the code | — | **open** |
| Five `aws logs test-metric-filter` runs: each matches only event 1 | — | **open** |
| The deploying identity can create the Scheduler group and schedule and pass the new role | — | **open** |
| `scripts/certify.sh` green at the release commit | Per unit in "Test evidence"; the run at the release commit is pending the code review | **open** |
| `release/1b` tagged on the certified commit and pushed on the user's go; HEAD on it | — | **open** |
| Gate dry run: the expected backend change set (15 `Add`, 5 `Modify`, no `Remove`, no `Database` row, no `WARN`) | — | **open** |
| No run on the deploy day (`open-runs-and-today` 0/0), clear of the backup and maintenance windows; manual snapshot `saferide-pre-1b-<date>` | — | **open** |
| Exports and the stray-variable check: `CONCURRENCY_ALARM_THRESHOLD=800`, `MAIL_ENABLED` only by decision, no `ALLOW_*` | — | **open** |
| Backend deploy: the change set as the dry run, migrate `applied` `[]`, seeds skipped, `app_role.synced` | — | **open** |
| First retention run by hand: `last-retention-run` `ok` or `stopped`; `RetentionHeartbeat` back to `OK` | — | **open** |
| Frontend deploy: one `Modify MonthlyCostBudget` row, no `WARN`; invalidation complete; **the reload time recorded** | — | **open** |
| Health `{"status":"ok","db":"ok","version":"<sha>"}` (GET); `/docs`, `/redoc`, `/openapi.json` 404; `X-Request-Id` (an inbound one not echoed) and the API headers | — | **open** |
| Which mail state shipped (`mailEnabled`) | — | **open** |
| PIN drill: `school-required` refused and counted; the driver code binds by typing and by link; a wrong PIN leaves a `pin-failure` line, a metric point and a `pin-lock-state` failure; the right PIN signs in and the row is salted | — | **open** |
| Mail drill (only inside a drill-only toggle while in the sandbox): a real reset to an operator's SES-verified address with `dkim=pass`, `spf=pass`, `dmarc=pass`; a forced send failure trips `MailSendFailed` end to end; the toggle switched back and the window recorded | — | **open** |
| Parent drill: a sign-up stays unlisted; it verifies from its own session and links (mail window); an office reset and a Confirm and reset leave their audit rows | — | **open** |
| `hardening` final, diffed against the baselines, every difference explained | — | **open** |
| Alarms after the smoke: `OK`/`INSUFFICIENT_DATA` apart from the drills' recorded ALARM/OK pairs | — | **open** |
| Rollback rehearsal: `release/1a` backend then frontend; 1a health and a legacy PIN sign-in; a salted driver refused under 1a and re-issued from 1a's Drivers page; `verify` tokens deleted on 1b just before the 1a backend deploy (`purge-verify-tokens`, D52); no student-email edits on 1a; back to `release/1b`; both durations | — | **open** |
| Six-digit PINs re-issued from the Drivers card to every active driver (D28): `salted_v2` = active drivers, `legacy_hmac` 0, `no_pin` 0 per school; Kuumbai's message, leading with the installed app and the typed code, sent to each driver by name | — | **open** |
| Alarm drill on `ApiServerErrors` and `RetentionHeartbeat`: ALARM and OK mails received | — | **open** |
| The old unrestricted Maps browser key restricted to localhost or deleted (D45) | — | **open** |
| The next morning: the first scheduled run in `last-retention-run`, a `RetentionRunSucceeded` point, the Scheduler metrics under the `ScheduleGroup` dimension, `MigrateFunctionErrors` and target errors `OK` | — | **open** |
| Guides match the live copy: `manualVocabulary` green | 36 tests green at U13 (uncommitted), "Test evidence" | **open** |
| Both operators confirm the drill mails (plan U13 verification; one operator address today, 1a record U1.7) | — | **open** |

### Baselines carried from Release 1a

Read on production at 2026-11-19 09:21Z, right after the 1a deploy (the 1a
record, "Deployed"), with the +95 h read of 2026-11-23:
`drivers-with-several-driver-memberships` 0 users;
`driver-pin-hash-versions-per-school` Msingi Bora 4 `legacy_hmac`, Voluntari
Academy 1 `legacy_hmac`, `salted_v2` 0, `no_pin` 0;
`unverified-parent-accounts-by-age` every bucket 0; `token-rows-by-purpose`
`verify` all 0; `notifications-past-90-days` 1; `stale-sessions`
`auth_sessions` 271 and `auth_preauth_tokens` 19 expired over 7 days;
`last-retention-run` empty; `push-subscriptions-by-kind` 0;
`instance-settings` `max_connections` 79, PostgreSQL 16.14;
`open-runs-and-today` 0/0. The first retention run starts from these sizes.

## Decisions taken during execution

**D1 holds** (Release 1a): verification is a flag, so accounts made by the
old code are grandfathered and the plan's migration 018 is not written. The
plan's "migrate output `018` applied" and "`hardening` shows how many
coexistence-week accounts 018 will promote" become "migrate `applied` `[]`"
and "`unverified-parent-accounts-by-age` all 0".

From the execution decisions, Release 1b:

- **D28** Six-digit PINs are re-issued on the 1b deploy day from the new
  Drivers card (salted); until then existing PINs keep working and are
  upgraded at sign-in (origin R13 holds).
- **D29** The office's "Confirm sign-up" becomes **Confirm and reset**:
  verify, link, and a temporary password through the shared helper, with the
  notice mail.
- **D30** Sign-up issues no session in either branch (new or existing
  address), both branches hash the password, and the card ends on a
  persistent "Check your email" state; the parent signs in to verify.
- **D31** A public `mailEnabled` flag: while false, forgot-password and
  sign-up say email is not switched on and point to the office, and Resend
  is hidden.
- **D32** The emailed reset returns a session only for an account without an
  active provider account; a provider is sent to sign-in and its second
  factor.
- **D33** The PIN lock is keyed on (school, source), the per-school total is
  alarmed, no copy claims an operator or the office can clear it, and the
  429 carries `Retry-After` with a countdown on screen.
- **D34** Change password for every role except PIN sessions.
- **D35** The office's email change voids tokens, revokes sessions and push,
  notifies the old address and writes `parent-email-changed`.
- **D36** A PIN holder holds an active driver membership at one school only;
  `drivers-with-several-driver-memberships` checks it.
- **D37** At most three mails per recipient per hour, counted from token
  rows.
- **D38** Error logging without `exc_info`; the traceback through
  `traceback.format_tb`; database errors log the SQL state, constraint and
  table; tested through the JSON formatter's shape. An inbound
  `X-Request-Id` is never trusted.
- **D39** CORS exposes `X-Request-Id` (and `Retry-After`); the client appends
  "(Reference: id)" for status 500 and above only.
- **D40** Seeds 002 and 003 and the e2e school helper carry fixed driver
  codes; the e2e `DRIVER` and `DRIVER_B` fixtures carry them.
- **D41** Tests that write audit rows with 017's new actions delete them (the
  013/016 double-apply rehearsals validate existing rows against the older
  CHECK lists).
- **D42** The nightly deletes include the GPS idempotency keys and run before
  the trail loop; the EMF line is verified under JSON logging; the migrate
  function's async config retries nothing (`MaximumRetryAttempts` 0);
  `--cli-read-timeout 0` was already in 1a.
- **D43** The rollback rehearsal includes a driver salted under 1b refused
  under 1a and re-issued from 1a's Drivers page, and a rollback to 1a deletes
  the `purpose='verify'` tokens.
- **D44** The design-lens copy and state specifications (DL1–DL17) are
  adopted; the screens and guides use their words.
- **D45** After the 1b smoke the operator restricts the old website-
  unrestricted Maps browser key to localhost, or deletes it.

Found and decided while building the units (from the task list and the
commit messages):

- **D46** (U7, the orchestrator) SES is tried **once** per mail with ten-
  second connect and read timeouts: the worst case, 20 seconds, stays under
  the API's 30-second Lambda timeout. A failed mail is logged and alarmed,
  never retried.
- **D47** (2026-11-23, the user) `ApiClientErrorsBurst` counts the app's
  own 4xx answers **other than 404**, from the access line's status
  (`ApiClientErrorsFilter`, `SafeRide`/`ApiClientErrorsNon404`), instead of
  API Gateway's `4xx`: in 1a's first week it fired twice on scanners
  probing for `/.env` and `/.git` files, every request a 404. The alarm is
  updated in place (same logical id). A 429 made by the gateway's route
  throttle never reaches the function and is no longer counted; the PIN
  alarms watch sign-in abuse.
- **D48** (U12) `PinFailureBurst` is one sum over every school and phone: a
  metric filter cannot split by the school code without a dimension per
  school. The lines name the school's public code for the first response.
- **D49** (U10, DL7) **Regenerate** is director-only, like Delete (a
  stepped-in provider counts as the director).
- **D50** (U10) A correct PIN does not clear that source's failure rows (the
  lock window simply ages out); the lock's e2e test stubs the 429 rather
  than making twenty-one real failures.
- **D51** (U10) `qrcode.react` 4.2.0, the one new dependency, draws the QR.
- **D52** (U13, resolved by the orchestrator) **The `verify`-token delete
  on a rollback is a migrate action on 1b.** Production has no ad-hoc SQL
  path (the verify function is read-only, 1a's migrate function runs only
  migrations and `gps-purge`, there is no bastion), so 1b's migrate function
  gains `{"action": "purge-verify-tokens"}` — one statement,
  `delete from password_reset_tokens where purpose = 'verify'`, reporting the
  count — invoked on 1b immediately before the 1a backend deploy (checklist,
  "Rollback rehearsal", step 1). The 1b frontend on a 1a backend creates no
  verification tokens, so only a sign-up in the minutes of that deploy can
  leave one, bounded by its 24-hour expiry. The exposure is narrow anyway: a
  `verify` token reaches only its address's own inbox, so under 1a it lets
  that inbox's owner set the account's password — what an emailed reset
  allows.
- **D53** (U13) Checklist choices the plan left open: a manual snapshot
  before the deploy (a fixed point before the first retention deletes,
  which a code rollback does not undo); a gate dry run as for 1a; the first
  retention run invoked by hand straight after the backend deploy; the
  drivers' messages sent after the rollback rehearsal (the re-issued PINs
  are refused while 1a is live); the sandbox's forced send failure through a
  recipient that is not SES-verified (with production access, a pause of
  the configuration set on the user's go); the expected backend change set
  derived from the template diff.
- **D54** (U13, the guides) With mail on, **Forgot password** also serves
  staff and providers (the code sends to any enabled account; a provider is
  sent back to sign-in for the second factor), so the staff guide's "no
  self-service e-mail reset for staff" and the provider guide's "no in-app
  reset for providers" are replaced; while mail is off both still go to a
  director or the deploy bootstrap.

**Pre-existing bug fixed in U9 (`de59f8d`).** Shared-parent detection was
blind under row security, in production since the tenancy release: the
runtime role cannot see another school's students, so a parent with
children at two schools read as not shared and stayed editable — and
deletable by a director — from either school. The check now reads the link
row's own school, and a link whose school cannot be seen counts as shared.

**Residuals carried** (not fixed in 1b; for the code review or later):

| Residual | Where it stands |
|---|---|
| The database pool has no `connect_timeout` or `tcp_user_timeout` (U11): a hung socket holds the health check until the 30-second Lambda timeout | the probe still reports it, late |
| `push_service.py` logs a web-push exception's text (U11), which may carry the subscription endpoint URL | for the code review |
| The temporary-password check's second step on a failure is a small timing tell (U9) | accepted for now |
| No per-staff rate limit on the office reset (U9) | the audit row names each issuer |
| "This account cannot be reset from the Parents page. Ask the parent to use Forgot password." points to a dead end while mail is off (U9) | UI copy unchanged in U13 (no behaviour change); the staff guide now says what to do (a director for staff at the school, else SafeRide support). A parent who is a driver elsewhere has no reset path while mail is off |
| DL12: the reset confirmation says "Their other devices are signed out", while every session of the parent ends | wording, for the code review |
| `infra/scripts/parity-params.sh` still comments "MAIL_ENABLED stays false until then" | comment only; U13 changed only the template's parameter description |
| `test_parent_feeds::test_display_status_matches_raw_at_school` fails on a second back-to-back run without a reseed (pre-existing, U12) | reseed between runs |
| The Scheduler's `TargetErrorCount` dimension (`ScheduleGroup`) is confirmed only after the first night | the checklist's next-morning step |
| The heartbeat reports a silent failure about 26 hours after the last success | stated in the checklist and infra/README.md |

## Code review

Pending: `ce-code-review` over the branch, then fixes and a full certify
(the task list's last step). Findings, fix batches and residuals go here.

## Test evidence

| Command | Result |
|---|---|
| `scripts/certify.sh` at the branch start (`main` `42a20b7`) | backend unit 756 passed, 561 skipped; integration 561 passed, 2 skipped; vitest 349; Playwright 102 |
| U11 (`48778e4`) | no certify counts recorded in the task list (U7+U8's run below covers the tree with U11 in it) |
| `scripts/certify.sh` after U7+U8 (`17dd95a`) | unit 837, integration 592, vitest 366, Playwright 103 — green |
| `scripts/certify.sh` after U9 (`de59f8d`) | unit 863, integration 606, vitest 381, Playwright 106 — green |
| `scripts/certify.sh` after U10 (`85030e3`) | unit 952, integration 625, vitest 452, Playwright 111 — green |
| `scripts/certify.sh` after U12 (`c2bd543`) | unit 1006, integration 631, vitest 452, Playwright 111 — green |
| U13: `npx vitest run tests/unit/manualVocabulary.test.ts` (frontend) | 36 passed (31 before U13) |
| U13: `npm test` (vitest, `tests/unit`) | 457 passed in 29 files |
| U13: `npx tsc --noEmit` | clean |
| U13: `../.venv/bin/python -m pytest -q tests/core` (backend, after the `MailEnabled` description change) | 636 passed |
| U13: `git diff --check` | clean |
| `scripts/certify.sh` at the release commit | **open** |

## Rollback position

- **Code rollback to `release/1a` (`a66d53b`, tag `25e7860`), backend then
  frontend, never the frontend alone** (infra/RESTORE.md, "Code rollback",
  the Release 1b paragraph; rehearsed by the checklist). The 1a frontend
  sends a driver PIN without a school and the 1b backend refuses it; the 1a
  backend accepts the 1b frontend's extra `schoolId` for the minutes between
  the two deploys. 1a's and 1b's deploy scripts are the same.
- **No schema to undo:** 1b has no migration. What the nightly retention job
  deleted stays deleted; the manual snapshot before the deploy is the fixed
  point for a data restore.
- **Salted PINs:** 1a checks only the legacy hash, so every driver counted
  under `salted_v2` is refused under 1a until re-issued from 1a's Drivers
  page. After the deploy-day re-issue that is every driver.
- **Decision window for a real rollback: 48 hours** from the deploy, with
  `driver-pin-hash-versions-per-school` as the count of drivers who would
  need a new PIN under 1a. After that, fix forward.
- **Verification tokens:** deleted on 1b just before the 1a backend deploy
  with the migrate function's `purge-verify-tokens` action (D52), since 1a's
  reset ignores `purpose`; a sign-up in the deploy's minutes is bounded by the
  24-hour expiry.
- **Unverified sign-ups:** under 1a an office edit of a student's parent
  email links an unverified account; no such edits while on 1a, and
  `unverified-parent-accounts-by-age` read after returning.
- **What reverts with 1a's templates:** the schedule, the heartbeat, the
  target-error, mail and PIN alarms and the five metric filters;
  `ApiClientErrorsBurst` back on the gateway's 4xx; the migrate timeout back
  to 120 seconds; health back to `{"status":"ok"}` without the database; the
  budget back to the whole account at 60 USD unless `MONTHLY_BUDGET_USD=100`
  is exported. 1a's API has no mail sender and no office parent reset.

## Deployed

Not deployed. Filled from the Release 1b deploy checklist: the window, the
release, the pre-checks, both change sets, the migrate result, the first
retention run, the reload time, the smoke and drills, the rollback
rehearsal and its durations, the PIN re-issue (counts per school), the alarm
drill, the Maps key, the next morning and the first 48 hours.
