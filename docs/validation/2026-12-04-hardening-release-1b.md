# Validation record — production hardening Release 1b (code)

- **Status: draft** (drafted 2026-11-23, before the code review; the gates
  before the deploy read on 2026-11-24 and 2026-11-25). `release/1b` is
  tagged and pushed; nothing is deployed.
- **Date:** planned 2026-11-26, 09:30 GMT (first planned for 2026-12-04) ·
  **Release commit:** `634224a`, tagged `release/1b`: `main` after PR #22
  (`feat/hardening-r1b`, cut from `main` at `42a20b7`, which holds all of
  Release 1a, live since 2026-11-19) and PR #23 (records only) · **Plan:**
  `docs/plans/2026-11-16-001-feat-production-hardening-plan.md`, U7–U12 and
  U13 (U16 shipped in Release 1a, D8), as amended by the execution decisions
  D1–D27 (the Release 1a record) and D28–D59 below.
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
| `17dd95a` | U7, U8 | `MailService` with an SES v2 transport (one attempt; ten-second timeouts at first, a 5 s connect and 8 s read after the code review, D59) and a simulated one (local dev routes for the last links); a failed send logs `mail-send-failed kind=…` without the recipient and never fails the request; reset (30 minutes) and verification (24 hours) tokens scoped by `purpose`, at most three mails per account per hour; forgot-password's neutral answer; sign-up issues no session — a new address gets an unverified account (D1) that links nothing and raises no incident until its signed-in owner verifies, an existing address gets the "you already have an account" mail; the emailed reset ends every session, drops push registrations, verifies the address and signs in (not providers, who still pass the second factor); the office's email change requires verification again and notifies the old address; `GET /api/push/config` carries `mailEnabled`, and the screens send people to the office while it is false |
| `de59f8d` | U9 | Either staff role issues a parent a 16-character temporary password (Crockford alphabet, read out in groups of four, typed any way) through a helper shared with the staff reset: forced change within 72 hours, sessions revoked, tokens voided, push registrations removed, a neutral notice mail and a `parent-password-reset` audit row with counts only; a neutral 409 for anyone with a membership at any school or a provider account; **Confirm and reset** verifies an unverified sign-up matching a slot email, links it and issues the same temporary password (`parent-email-confirmed` and `parent-password-reset` rows); **Change password** for every role except PIN sessions, marked in the session token, keeping the current session. Also fixes the pre-existing shared-parent detection under row security (below) |
| `85030e3` | U10 | The phone is set up once with the school's driver code (`GET /api/auth/schools/{code}`, typed, link or QR); a PIN without a school is refused with `school-required`; new PINs are six digits, refused when trivial, stored as `v2:k1:` + HMAC(pepper, school id + ":" + PIN), so the same PIN may exist at two schools and the in-school check no longer confirms another school's PINs; a legacy PIN signs in once and is rewritten to the salted form; more than twenty failures from one source at one school in fifteen minutes answer 429 `school-pin-locked` with `Retry-After`; every failure writes a row and a `pin-failure school=<code>` line; a director regenerates the code; one active driver membership per PIN holder; fixed driver codes in the seeds and e2e fixtures; `qrcode.react` 4.2.0 |
| `c2bd543` | U12 | EventBridge Scheduler invokes the migrate function at 01:30 Africa/Nairobi with `{"action": "retention"}` (no async retries, 600-second timeout); the run deletes in bounded skip-locked batches — feed rows past 90 days, used or expired tokens, expired sessions and pre-auth tokens, PIN-failure rows, idempotency keys, unverified parents older than 30 days with no link, membership, provider account or audit and incident reference — then purges the trail per school under a time budget; never incidents or the audit log; an `ops_job_runs` row and an EMF `RetentionRunSucceeded` point per run (a budget stop is success); the heartbeat, Scheduler target-error, mail, PIN, PIN-lock and `school-required` alarms on metric filters; `ApiClientErrorsBurst` moved in place to the app's non-404 4xx (D47) |
| `a500cdb` | U13 | The guides' final pass (parent, driver, staff, provider, index) in the app's words and five new `manualVocabulary` tests; the stale texts (index date, `agent-implementation-instructions.md`'s PIN lines, the seed-002 header); the `MailEnabled` parameter description; the Release 1b deploy checklist; the 1b paragraph of `infra/RESTORE.md`, "Code rollback"; this record |
| `a9655e0` | D52 | The migrate function's `{"action": "purge-verify-tokens"}`, invoked on 1b immediately before a rollback's 1a backend deploy |
| `fe0a5da` | review | Code review #1–#4, #6–#11 (below): Confirm and reset refuses cross-school addresses and accounts linked elsewhere; the Parents page refuses an email change for an identity with other access; the driver binding survives refused storage; the rollback notes on 1a's student saves; one-time passwords shown before the refetch (Parents and Staff pages); push re-attaches after the forced change; a failed config fetch is retried; one login-response type; two test fixes |
| `965d8ec` | review | The same reveal-first ordering on the provider's account and school creation |
| `00ea5d0` | review | Code review #5, #12–#14: retention never deletes an account the office changed the email of; the driver code is kept out of the API log; SES bounded at a 5 s connect and 8 s read; a dead helper removed |

## Go/No-Go gates (plan §Operational Notes, Release 1b checklist)

Each gate is a step of the
[Release 1b deploy checklist](../runbooks/hardening-release-checklists.md#release-1b-deploy-checklist).

| Gate | Observed | Verdict |
|---|---|---|
| Release 1a stable for a week (from 2026-11-26): alarms `OK` in both regions, both subscriptions confirmed, SES identity verified with DKIM and MAIL FROM `SUCCESS`, no unexplained ERROR line | Through 2026-11-23 (the 1a record, "+95 h"): all `OK` except two `ApiClientErrorsBurst` pairs from scanners (404s, D47); subscriptions confirmed; `true`/`SUCCESS`/`SUCCESS`; 0 ERROR lines. 2026-11-25 13:13Z (a partial read; the final one is on the deploy day): all 16 alarms `OK` (12 in af-south-1, 4 in us-east-1); one subscription per topic, each a full ARN (one operator address, 1a record U1.7); `true`/`SUCCESS`/`SUCCESS`; the search from 2026-11-24 00:00Z over the three groups: 0 ERROR lines, 2 `psycopg.pool` WARNING lines, explained below; no `5xx` | **open** |
| SES production access state recorded and the `MAIL_ENABLED` decision taken, with the UI state that ships | 2026-11-23: still `DENIED` in the sandbox (the case is in the 1a record); by the checklist, sandbox means `MAIL_ENABLED` unset (`false`) and the mail-off screens. 2026-11-24: still `DENIED`; the user sent a follow-up on the case (about 50 mails a day). The user's decision, 2026-11-24: `MAIL_ENABLED` stays unset, the mail-off screens ship, and the mail drill and parent drill step 2 wait for production access (recorded as "not run: mail off") | **met** |
| `hardening` baselines: `drivers-with-several-driver-memberships` 0, every PIN `legacy_hmac`, `unverified-parent-accounts-by-age` all 0, `token-rows-by-purpose` `verify` all 0, `last-retention-run` empty | At 1a (2026-11-19 09:21Z): 0; Msingi Bora 4 and Voluntari Academy 1 `legacy_hmac`; 0; 0; empty. To be read again before the deploy | **open** |
| The active drivers of both production schools listed by name (outside the repository) with who gives each the PIN and the code | — | **open** |
| Five `aws logs test-metric-filter` runs: each matches only event 1 | 2026-11-24: each of the five matches only event 1 | **met** |
| The deploying identity can create the Scheduler group and schedule and pass the new role | 2026-11-24: the IAM user that deployed 1a, with `AdministratorAccess` attached | **met** |
| `scripts/certify.sh` green at the release commit | Per unit in "Test evidence". 2026-11-24 at `634224a`: exit 0; backend unit 1015 passed (642 skipped, the integration tests), integration 642 passed (2 skipped), `tsc` clean, vitest 470 passed, build ok, Playwright 111 passed | **met** |
| `release/1b` tagged on the certified commit and pushed on the user's go; HEAD on it | Annotated tag `1414ffc` on `634224a`, tagged 2026-11-25 13:12:29Z and pushed on the user's go (given 2026-11-24); the dry run ran with HEAD on it and `git status --short` showing only `.claude/` and `justfile` | **met** |
| Gate dry run: the expected backend change set (15 `Add`, 5 `Modify`, no `Remove`, no `Database` row, no `WARN`) | 2026-11-25 13:13–13:14Z: the 15 `Add` as expected; **6** `Modify`, the expected five plus `HttpApi` (`AWS::ApiGatewayV2::Api`, replacement `False`, `Body`), explained below; no `Remove`, no `Database` row, no `WARN`; answered `N`, the change set deleted, `list-change-sets` empty, the stack still `UPDATE_COMPLETE` from 2026-11-19 | **met** (the user accepted the `HttpApi` row, 2026-11-25; the checklist now expects six `Modify`) |
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
| Rollback rehearsal: `release/1a` backend then frontend; 1a health and a legacy PIN sign-in; a salted driver refused under 1a and re-issued from 1a's Drivers page; `verify` tokens deleted on 1b just before the 1a backend deploy (`purge-verify-tokens`, D52); no student saves or imports on 1a; back to `release/1b`; both durations | — | **open** |
| Six-digit PINs re-issued from the Drivers card to every active driver (D28): `salted_v2` = active drivers, `legacy_hmac` 0, `no_pin` 0 per school; Kuumbai's message, leading with the installed app and the typed code, sent to each driver by name | — | **open** |
| Alarm drill on `ApiServerErrors` and `RetentionHeartbeat`: ALARM and OK mails received | — | **open** |
| The old unrestricted Maps browser key restricted to localhost or deleted (D45) | — | **open** |
| The next morning: the first scheduled run in `last-retention-run`, a `RetentionRunSucceeded` point, the Scheduler metrics under the `ScheduleGroup` dimension, `MigrateFunctionErrors` and target errors `OK` | — | **open** |
| Guides match the live copy: `manualVocabulary` green | 36 tests green at U13 (uncommitted), "Test evidence" | **open** |
| Both operators confirm the drill mails (plan U13 verification; one operator address today, 1a record U1.7) | — | **open** |

### Before the deploy: the gate dry run and the reads (2026-11-24 and 2026-11-25)

- **Gate dry run**, 2026-11-25, HEAD `634224a` on `release/1b`, no stray
  variables: `CONCURRENCY_ALARM_THRESHOLD=800 CONFIRM_CHANGESET=1
  ./scripts/deploy-backend.sh`, answered `N`. The script's lines before the
  build: a clean checkout (HEAD is `origin/main`),
  `ConcurrencyAlarmThreshold=800`, the live-parity parameters at their
  defaults, the alert address kept from the stack, `open-runs: 0`; the
  overrides carry `MailEnabled` `false` and `AppVersion` `634224a`. The
  script's table:

  ```text
    Action  LogicalResourceId                 ResourceType                    Replacement  Changed
    Modify  ApiClientErrorsBurst              AWS::CloudWatch::Alarm          False        MetricName, Dimensions, Namespace, AlarmDescription
    Add     ApiClientErrorsFilter             AWS::Logs::MetricFilter         -            -
    Modify  ApiFunction                       AWS::Lambda::Function           False        Code, Environment
    Modify  HttpApi                           AWS::ApiGatewayV2::Api          False        Body
    Add     MailSendFailedFilter              AWS::Logs::MetricFilter         -            -
    Add     MailSendFailed                    AWS::CloudWatch::Alarm          -            -
    Modify  MigrateFunctionErrors             AWS::CloudWatch::Alarm          False        AlarmDescription
    Add     MigrateFunctionEventInvokeConfig  AWS::Lambda::EventInvokeConfig  -            -
    Modify  MigrateFunction                   AWS::Lambda::Function           False        Code, Timeout
    Add     OpsScheduleGroup                  AWS::Scheduler::ScheduleGroup   -            -
    Add     PinFailureBurst                   AWS::CloudWatch::Alarm          -            -
    Add     PinFailureFilter                  AWS::Logs::MetricFilter         -            -
    Add     PinLockedFilter                   AWS::Logs::MetricFilter         -            -
    Add     PinLocked                         AWS::CloudWatch::Alarm          -            -
    Add     RetentionHeartbeat                AWS::CloudWatch::Alarm          -            -
    Add     RetentionScheduleTargetErrors     AWS::CloudWatch::Alarm          -            -
    Add     RetentionSchedulerRole            AWS::IAM::Role                  -            -
    Add     RetentionSchedule                 AWS::Scheduler::Schedule        -            -
    Add     SchoolRequiredBurst               AWS::CloudWatch::Alarm          -            -
    Add     SchoolRequiredFilter              AWS::Logs::MetricFilter         -            -
    Modify  VerifyFunction                    AWS::Lambda::Function           False        Code
  ```

- **The extra `HttpApi` row.** The expected table came from the template
  diff, and the 1b diff touches neither `HttpApi` nor the function's route
  events. `Body` is the OpenAPI document SAM generates from those events.
  Translated locally (SAM CLI 1.162.1, translator 1.110.0) from `release/1a`
  and from `release/1b`, it is identical to the deployed one (the stack's
  processed template, compared with sorted keys: no difference, the same five
  paths). Its only intrinsic reference is `${ApiFunction.Arn}`, in the
  integration URI. `ApiFunction` is a `Modify` row, so CloudFormation
  evaluates `Body` when the update runs (a dynamic change) and lists it; the
  function is not replaced, so `Body` resolves to the same ARN and the same
  document. Any release that modifies `ApiFunction` shows this row. In 1a it
  was also a real change: the two new routes.
- **The two WARNING lines** in the search, both `psycopg.pool` "error
  connecting in 'pool-1'": 2026-11-24 15:28:37Z ("SSL connection has been
  closed unexpectedly") and 2026-11-25 08:31:18Z ("connection timeout
  expired"). They are the pool's reconnect in the background. The first
  invocation still answered `GET /api/incidents/today-count` `200` in 30 ms;
  the second line falls between health probes, all `200`. The database's
  events show only the nightly backups. Responses from 2026-11-24 00:00Z to
  2026-11-25 13:15Z: 26,963 `2xx`, 28 `4xx`, no `5xx`. 1b's database-aware
  health check makes a lasting fault visible to the probe.

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

From the code review (2026-11-23):

- **D55** **Confirm and reset is single-school.** It refuses, with the
  neutral `nothing-to-confirm`, an address that also matches another
  school's students and an account already linked at another school (#1):
  otherwise one school's office could verify the account, hold its
  temporary password and accept the other school's pending child — a trust
  the plan grants the office reset only for a parent with an ACCEPTED link
  at that school. A family listed at two schools verifies through its own
  email link; while mail is off it stays "Awaiting signup" until mail is
  on. (Open for the user: relax this if a cross-school family needs the
  office path before mail is on.)
- **D56** **The Parents page refuses an email change** (`not-editable-here`,
  "This account's email cannot be changed from the Parents page") for an
  identity holding any non-removed membership at any school or a provider
  account — the office reset's own rule (#2): with mail on, a redirected
  email plus Forgot password would otherwise take over a director, driver
  or provider. Name and phone edits still save.
- **D57** **Retention never deletes an account the office changed the email
  of** (#5): the 30-day grace counts from creation, so only accounts that
  were never verified qualify; the `parent-email-changed` audit row, now
  recording the stored id, names the exceptions.
- **D58** **The driver code is kept out of the API log** (#13): the public
  lookup's path is logged as `/api/auth/schools/{code}` in the access line
  and the unexpected-error line (`%2F`-decoded paths included); the status
  stays last for the 4xx metric filter. The local stack's uvicorn access
  log still prints it (local only).
- **D59** **SES sends are bounded at a 5 s connect and 8 s read**, one
  attempt (#14): the office's email change sends two mails after its
  commit, and two hung sends must stay inside the API function's 30 s
  timeout.

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
| `push_service.py` logs a web-push exception's text (U11), which may carry the subscription endpoint URL | not raised by the code review; still open |
| The temporary-password check's second step on a failure is a small timing tell (U9) | accepted for now |
| No per-staff rate limit on the office reset (U9) | the audit row names each issuer |
| "This account cannot be reset from the Parents page. Ask the parent to use Forgot password." points to a dead end while mail is off (U9) | UI copy unchanged in U13 (no behaviour change); the staff guide now says what to do (a director for staff at the school, else SafeRide support). A parent who is a driver elsewhere has no reset path while mail is off |
| DL12: the reset confirmation says "Their other devices are signed out", while every session of the parent ends | wording; not raised by the code review |
| `infra/scripts/parity-params.sh` still comments "MAIL_ENABLED stays false until then" | comment only; U13 changed only the template's parameter description |
| `test_parent_feeds::test_display_status_matches_raw_at_school` fails on a second back-to-back run without a reseed (pre-existing, U12) | reseed between runs |
| The Scheduler's `TargetErrorCount` dimension (`ScheduleGroup`) is confirmed only after the first night | the checklist's next-morning step |
| The heartbeat reports a silent failure about 26 hours after the last success | stated in the checklist and infra/README.md |

## Code review

`ce-code-review` over `main..a9655e0` (141 files; the code part about
5,800 added lines), 2026-11-23, with the plan and the execution decisions as
intent. Twelve reviewers: correctness, testing, maintainability and
project standards (always on), security, performance, API contract,
reliability, adversarial and frontend async races (conditional), plus the
agent-native and learnings agents. No migration in 1b, so no data-migration
or deployment-verification reviewer; no PR yet, so no previous-comments
reviewer. Every finding at the confidence bar was re-checked by an
independent validator (one per finding; the storage-fallback finding, at
full confidence after two reviewers agreed, was verified directly).

| # | Sev | Finding | Reviewers | Validated | Fix |
|---|---|---|---|---|---|
| 1 | P1 | Confirm and reset let one school's office take over a cross-school family's unverified account | security, adversarial, correctness | yes | `fe0a5da` (D55) |
| 2 | P1 | The Parents page email change bypassed the not-resettable-here guard | correctness, adversarial | yes | `fe0a5da` (D56) |
| 3 | P2 | The driver binding was lost when the browser refuses storage | frontend races, correctness | yes (direct) | `fe0a5da` |
| 4 | P2 | The rollback notes said only email edits link unverified sign-ups under 1a; every student save does | adversarial | yes | `fe0a5da` |
| 5 | P2 | Retention would delete a long-standing parent the office made unverified, the night their last link went | adversarial | yes | `00ea5d0` (D57) |
| 6 | P2 | An existing test left a `parent-email-changed` audit row behind | testing | yes | `fe0a5da` |
| 7 | P2 | The login-response user shape was declared three times | maintainability | yes | `fe0a5da` |
| 8 | P2 | One-time passwords were revealed only after a list refetch | frontend races | yes | `fe0a5da`, `965d8ec` (Staff and provider screens too) |
| 9 | P2 | Push never re-attached after an office reset's forced change | correctness | yes | `fe0a5da` |
| 10 | P2 | A failed config fetch was cached as "mail off" for the session | frontend races | yes | `fe0a5da` |
| 11 | P3 | No test for the concurrent sign-up branch | testing | yes | `fe0a5da` |
| 12 | P3 | An unused `format_driver_code` | maintainability | yes | `00ea5d0` |
| — | P3 | No test that a disabled account's live reset token is refused | testing | **no** — `test_staff_accounts.py` covers it | — |
| — | P2 | PIN rules mirrored between backend and frontend | maintainability | **no** — the denylist is cross-checked; the rest is intentional | — |

Below the confidence bar but applied as hardening: the driver code in the
access log (#13, security, also noted by correctness; D58) and the two
inline mails of the office's email change against the 30 s timeout (#14,
reliability and adversarial; D59). Performance, API contract and project
standards had no findings; the agent-native and learnings agents found no
gap (the one learnings check — new template parameters outside the parity
maps — holds: 1b adds no parameter).

**Residual risks the review recorded, not fixed in 1b:**

- The PIN lock reads the window and writes the failure row in separate
  transactions, so a concurrent burst from one source can make up to about
  the route burst (40) attempts before the count crosses 20 (security,
  reliability, adversarial). Bounded by the route throttle and alarmed
  (D33); an insert-first count is the fix if it matters.
- A retention step that fails stops the later steps and the trail purge for
  that night (reliability); the run raises and the errors alarm fires.
- Verification is stamped before linking runs: if linking fails, the
  account is verified but unlinked and a retry answers "already verified";
  the office's next student save links it (correctness, adversarial).
- Forgot-password answers an existing address after a token write and an
  SES call, an unknown one at once — a timing tell once mail is on; login
  already has the same tell (pre-existing).
- With mail off, Confirm and reset trusts the office's identity check of
  whoever phones in (adversarial); the staff guide now says to ring the
  parent back on the number on the student's record before confirming.
- The health check opens the database pool in every warm Lambda (four
  connections by default; performance). Fine at the pilot's scale; a
  smaller pool is the fix if connections grow.
- A driver app left open on the 1a bundle through the deploy gets "Invalid
  PIN" for `school-required`; the drivers' message says to close and
  reopen the app (API contract).

Fixes verified by the suites each batch ran and by a full certify (below).

## Test evidence

| Command | Result |
|---|---|
| `scripts/certify.sh` at the branch start (`main` `42a20b7`) | backend unit 756 passed, 561 skipped; integration 561 passed, 2 skipped; vitest 349; Playwright 102 |
| U11 (`48778e4`) | backend unit 791 passed, 578 skipped; tsc clean; vitest 355 (U7+U8's full run below covers the tree with U11 in it) |
| `scripts/certify.sh` after U7+U8 (`17dd95a`) | unit 837, integration 592, vitest 366, Playwright 103 — green |
| `scripts/certify.sh` after U9 (`de59f8d`) | unit 863, integration 606, vitest 381, Playwright 106 — green |
| `scripts/certify.sh` after U10 (`85030e3`) | unit 952, integration 625, vitest 452, Playwright 111 — green |
| `scripts/certify.sh` after U12 (`c2bd543`) | unit 1006, integration 631, vitest 452, Playwright 111 — green |
| U13: `npx vitest run tests/unit/manualVocabulary.test.ts` (frontend) | 36 passed (31 before U13) |
| U13: `npm test` (vitest, `tests/unit`) | 457 passed in 29 files |
| U13: `npx tsc --noEmit` | clean |
| U13: `../.venv/bin/python -m pytest -q tests/core` (backend, after the `MailEnabled` description change) | 636 passed |
| U13: `git diff --check` | clean |
| `scripts/certify.sh` after the code-review fixes (`00ea5d0`, after a reseed) | unit 1015 passed, 642 skipped; integration 642 passed, 2 skipped; vitest 470; Playwright 111 — green |
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
- **Unverified sign-ups:** under 1a every student create, edit or import
  re-runs the parent-link sync and links an unverified account whose email
  matches; no student saves or imports of any kind while on 1a, and
  `unverified-parent-accounts-by-age` read after returning (code review #4).
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
