# Validation record — production hardening Release 1b (code)

- **Status: deployed** 2026-11-26 (backend 12:22:08Z, frontend 12:45:51Z),
  **with mail on**; smoke, rollback rehearsal and drills done ("Deployed").
  The next-morning checks (2026-11-27) and the first 48 hours (to
  2026-11-28 12:22Z) are done, with no rollback: fix forward from here.
- **Date:** 2026-11-26, started 12:07Z (planned 09:30 GMT; first planned
  for 2026-12-04) ·
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
| Release 1a stable for a week (from 2026-11-26): alarms `OK` in both regions, both subscriptions confirmed, SES identity verified with DKIM and MAIL FROM `SUCCESS`, no unexplained ERROR line | Through 2026-11-23 (the 1a record, "+95 h"): all `OK` except two `ApiClientErrorsBurst` pairs from scanners (404s, D47); subscriptions confirmed; `true`/`SUCCESS`/`SUCCESS`; 0 ERROR lines. 2026-11-25 13:13Z (a partial read; the final one is on the deploy day): all 16 alarms `OK` (12 in af-south-1, 4 in us-east-1); one subscription per topic, each a full ARN (one operator address, 1a record U1.7); `true`/`SUCCESS`/`SUCCESS`; the search from 2026-11-24 00:00Z over the three groups: 0 ERROR lines, 2 `psycopg.pool` WARNING lines, explained below; no `5xx`. 2026-11-26 12:08:53Z (the final read): 16 alarms `OK`; one subscription per topic, full ARNs; `true`/`SUCCESS`/`SUCCESS`; since the 2026-11-25 read 0 ERROR or WARNING lines, the gateway 15,970 requests, 25 `4xx`, 0 `5xx` | **met** |
| SES production access state recorded and the `MAIL_ENABLED` decision taken, with the UI state that ships | 2026-11-23: still `DENIED` in the sandbox (the case is in the 1a record); by the checklist, sandbox means `MAIL_ENABLED` unset (`false`) and the mail-off screens. 2026-11-24: still `DENIED`; the user sent a follow-up on the case (about 50 mails a day). The user's decision, 2026-11-24: `MAIL_ENABLED` stays unset, the mail-off screens ship, and the mail drill and parent drill step 2 wait for production access (recorded as "not run: mail off"). **2026-11-26: production access GRANTED** (`production` true, `sending` true, review `GRANTED`, 50,000 a day). The user's new decision the same day: `MAIL_ENABLED=true` on this deploy, the mail-on screens ship, the mail drill and parent drill step 2 run; every later 1b backend deploy exports it | **met** (decision changed 2026-11-26: mail on) |
| `hardening` baselines: `drivers-with-several-driver-memberships` 0, every PIN `legacy_hmac`, `unverified-parent-accounts-by-age` all 0, `token-rows-by-purpose` `verify` all 0, `last-retention-run` empty | At 1a (2026-11-19 09:21Z): 0; Msingi Bora 4 and Voluntari Academy 1 `legacy_hmac`; 0; 0; empty. To be read again before the deploy. 2026-11-26 12:10Z: 0; Msingi Bora 4 and Voluntari Academy 1 `legacy_hmac`, `salted_v2` 0, `no_pin` 0; every bucket 0; `verify` all 0; empty; `notifications-past-90-days` 1, `stale-sessions` 272/23; `migrations` ends on `017` | **met** |
| The active drivers of both production schools listed by name (outside the repository) with who gives each the PIN and the code | 2026-11-26: Msingi Bora's 4 driver accounts are placeholders, not real people (the user); Voluntari Academy's is the test driver. Nobody to reach | **met** (n/a: no real drivers) |
| Five `aws logs test-metric-filter` runs: each matches only event 1 | 2026-11-24: each of the five matches only event 1 | **met** |
| The deploying identity can create the Scheduler group and schedule and pass the new role | 2026-11-24: the IAM user that deployed 1a, with `AdministratorAccess` attached | **met** |
| `scripts/certify.sh` green at the release commit | Per unit in "Test evidence". 2026-11-24 at `634224a`: exit 0; backend unit 1015 passed (642 skipped, the integration tests), integration 642 passed (2 skipped), `tsc` clean, vitest 470 passed, build ok, Playwright 111 passed | **met** |
| `release/1b` tagged on the certified commit and pushed on the user's go; HEAD on it | Annotated tag `1414ffc` on `634224a`, tagged 2026-11-25 13:12:29Z and pushed on the user's go (given 2026-11-24); the dry run ran with HEAD on it and `git status --short` showing only `.claude/` and `justfile` | **met** |
| Gate dry run: the expected backend change set (15 `Add`, 5 `Modify`, no `Remove`, no `Database` row, no `WARN`) | 2026-11-25 13:13–13:14Z: the 15 `Add` as expected; **6** `Modify`, the expected five plus `HttpApi` (`AWS::ApiGatewayV2::Api`, replacement `False`, `Body`), explained below; no `Remove`, no `Database` row, no `WARN`; answered `N`, the change set deleted, `list-change-sets` empty, the stack still `UPDATE_COMPLETE` from 2026-11-19 | **met** (the user accepted the `HttpApi` row, 2026-11-25; the checklist now expects six `Modify`) |
| No run on the deploy day (`open-runs-and-today` 0/0), clear of the backup and maintenance windows; manual snapshot `saferide-pre-1b-<date>` | 2026-11-26 12:10Z: 0/0 (holiday, D25); windows `23:00-23:30` and `sun:00:00-sun:01:00`, nothing pending, the deploy ran 12:18–12:46Z; snapshot `saferide-pre-1b-20261126` created 12:13:17Z, `available` 12:17:49Z (20 GB, encrypted) | **met** |
| Exports and the stray-variable check: `CONCURRENCY_ALARM_THRESHOLD=800`, `MAIL_ENABLED` only by decision, no `ALLOW_*` | The deploy shell printed only `CONCURRENCY_ALARM_THRESHOLD` and `MAIL_ENABLED` (`true`, by the 2026-11-26 decision); no `ALLOW_*` | **met** |
| Backend deploy: the change set as the dry run, migrate `applied` `[]`, seeds skipped, `app_role.synced` | 12:18:10–12:22:08Z: overrides `MailEnabled=true` and `ConcurrencyAlarmThreshold=800` only; the table row for row the dry run's (15 `Add`, 6 `Modify`); `y` 12:20:53Z on the user's yes; migrate `applied` `[]`, seeds skipped (`APP_ENV=production`), `app_role.synced` `true` | **met** |
| First retention run by hand: `last-retention-run` `ok` or `stopped`; `RetentionHeartbeat` back to `OK` | 12:22:33Z (202): `run_id` 1 `ok` at 12:22:38Z (272 sessions, 23 pre-auth tokens, 1 notification, 1 reset token, 39 action keys, 2 trail batches). `RetentionHeartbeat`: `ALARM` at its first evaluation, 12:59:21Z (complete hours only); deleted by the rehearsal before its next; the re-created alarm returned to `OK` at 15:27:22Z, an hour after the drill, on the 12:00Z point | **met** |
| Frontend deploy: one `Modify MonthlyCostBudget` row, no `WARN`; invalidation complete; **the reload time recorded** | 12:23:35–12:45:51Z: one `Modify MonthlyCostBudget` row, but `Conditional` with a `WARN` in the gate's summary view; property-level `False`, `CostFilters` added and `BudgetLimit` 60 → 100, both `RequiresRecreation: Never` (the live budget already held both); `y` 12:45:06Z on the user's yes; invalidation `I6C5CPBFDUJHZO5UGYS5TCT0GN` `Completed` at about 12:46Z (**the reload time**); 13 iCloud conflict copies uploaded and removed ("Deployed") | **met** (the user accepted the `Conditional` row; the checklist line needs it) |
| Health `{"status":"ok","db":"ok","version":"<sha>"}` (GET); `/docs`, `/redoc`, `/openapi.json` 404; `X-Request-Id` (an inbound one not echoed) and the API headers | 12:47:00Z: `{"status":"ok","db":"ok","version":"634224a"}`; three `404`; a fresh `x-request-id`, HSTS, `nosniff`, `no-store`, `default-src 'none'; frame-ancestors 'none'`; the site's headers unchanged | **met** |
| Which mail state shipped (`mailEnabled`) | `mailEnabled` `True` (mail on) | **met** |
| PIN drill: `school-required` refused and counted; the driver code binds by typing and by link; a wrong PIN leaves a `pin-failure` line, a metric point and a `pin-lock-state` failure; the right PIN signs in and the row is salted | 12:47:14Z `school-required` body, line and point; 13:10:17Z the typed code and 13:16:07Z the link both asked "Is this your school?"; 13:11:43Z a wrong PIN: `pin-failure school=VOL-001`, a `PinFailures` point, one `pin-lock-state` failure; 13:10:56Z the right PIN signed in, `salted_v2` 0 → 1 and `legacy_hmac` 1 → 0 at Voluntari Academy | **met** |
| Mail drill (only inside a drill-only toggle while in the sandbox): a real reset to an operator's SES-verified address with `dkim=pass`, `spf=pass`, `dmarc=pass`; a forced send failure trips `MailSendFailed` end to end; the toggle switched back and the window recorded | Production access, so no toggle. 13:19:23Z a real reset to the user's inbox: SPF, DKIM (`saferidelive.co.ke`) and DMARC `PASS`, the return path on `mail.saferidelive.co.ke`, the link to "Set a new password" signed in. The forced failure by pausing `saferide-transactional` 13:23:31–13:25:35Z on the user's go: `mail-send-failed kind=reset … code=SendingPausedException` (no address), a `MailSendFailed` point, `ALARM` 13:26:38Z → `OK` 13:41:38Z | **met** |
| Parent drill: a sign-up stays unlisted; it verifies from its own session and links (mail window); an office reset and a Confirm and reset leave their audit rows | Sign-up A 13:33:10Z stayed unlisted (the user; the count proven on sign-up B, `has_link` false `under_7d` 1); A verified from its own session 13:34:37Z, linked, Registered, the office alerted (the user); office reset 13:44:38Z and Confirm and reset 13:46:48Z left `parent-password-reset` and `parent-email-confirmed` rows (plus one reset on a wrongly chosen student, 13:47:49Z); both chose their own passwords | **met** |
| `hardening` final, diffed against the baselines, every difference explained | 13:49:07Z `--strict` exit 0; every difference explained ("Deployed", smoke 8) | **met** |
| Alarms after the smoke: `OK`/`INSUFFICIENT_DATA` apart from the drills' recorded ALARM/OK pairs | 13:49Z: all `OK` except `RetentionHeartbeat` (`ALARM` since its first evaluation, explained); the drill pair `MailSendFailed` 13:26:38Z → 13:41:38Z | **met** |
| Rollback rehearsal: `release/1a` backend then frontend; 1a health and a legacy PIN sign-in; a salted driver refused under 1a and re-issued from 1a's Drivers page; `verify` tokens deleted on 1b just before the 1a backend deploy (`purge-verify-tokens`, D52); no student saves or imports on 1a; back to `release/1b`; both durations | 13:53–14:26Z on the user's go: `purge-verify-tokens` deleted 2 (both used); to 1a 15 `Remove` / 6 `Modify`, health `{"status":"ok"}`, a staff sign-in, the salted PIN refused, a four-digit PIN set on 1a signed in; `verify` 0 live; no student saves; back to 1b with the deploy's table, `mailEnabled` `True`, the 1a PIN salted again, then a six-digit PIN. 1b → 1a 11 min 53 s, 1a → 1b 4 min 42 s | **met** |
| Six-digit PINs re-issued from the Drivers card to every active driver (D28): `salted_v2` = active drivers, `legacy_hmac` 0, `no_pin` 0 per school; Kuumbai's message, leading with the installed app and the typed code, sent to each driver by name | Msingi Bora 4 of 4 `salted_v2` (14:35:45–14:36:49Z; placeholder accounts, the PINs given to no one), Voluntari Academy 1 of 1 (the test driver, 14:25:39Z); `legacy_hmac` 0, `no_pin` 0. No message: no real drivers | **met** (no message needed) |
| Alarm drill on `ApiServerErrors` and `RetentionHeartbeat`: ALARM and OK mails received | 14:27:15Z on the user's go: `ApiServerErrors` `ALARM` 14:27:16Z → `OK` 14:27:54Z; `RetentionHeartbeat` `ALARM` 14:27:18Z → `OK` 15:27:22Z, an hour after the drill; the user received the mails | **met** |
| The old unrestricted Maps browser key restricted to localhost or deleted (D45) | 14:34:33Z through `gcloud` on the user's go: "SafeRide Web (browser)" allows only `localhost:5173`, `127.0.0.1:5173` and `localhost:4173`; the live bundle carries the production key; the Fleet Map drew (the user) | **met** |
| The next morning: the first scheduled run in `last-retention-run`, a `RetentionRunSucceeded` point, the Scheduler metrics under the `ScheduleGroup` dimension, `MigrateFunctionErrors` and target errors `OK` | 2026-11-27 10:29–10:45Z: `run_id` 2 `ok`, started by the schedule at 22:30:55Z, 11 h 58 min old; a `RetentionRunSucceeded` point in the night's hour, `RetentionHeartbeat` `OK`; `InvocationAttemptCount` under `ScheduleGroup`, the dimension the target-error alarm watches (`TargetErrorCount` not listed yet: no error so far); `MigrateFunctionErrors` and `RetentionScheduleTargetErrors` `OK`; the only ERROR line the mail drill's ("Deployed") | **met** |
| Guides match the live copy: `manualVocabulary` green | 36 tests green at U13 (uncommitted), "Test evidence". The certify run at `634224a` (2026-11-24) ran vitest 470, the `manualVocabulary` tests among them; the drills met the guides' words on the live screens | **met** |
| Both operators confirm the drill mails (plan U13 verification; one operator address today, 1a record U1.7) | 2026-11-26: the user confirmed the mail drill's and the alarm drill's mails (one operator address) | **met** |

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
| The Scheduler's `TargetErrorCount` dimension (`ScheduleGroup`) is confirmed only after the first night | confirmed 2026-11-27 through `InvocationAttemptCount` ("Deployed", the next morning); `TargetErrorCount` itself appears with the first target error |
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
| `scripts/certify.sh` at the release commit (`634224a`, 2026-11-24) | exit 0: unit 1015 passed, 642 skipped; integration 642 passed, 2 skipped; `tsc` clean; vitest 470; build ok; Playwright 111 |

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
- **Decision window for a real rollback: 48 hours** from the deploy (to
  about 2026-11-28 12:22Z), with `driver-pin-hash-versions-per-school` as
  the count of drivers who would need a new PIN under 1a: all five since
  the deploy-day re-issue. **The window closed at 2026-11-28 12:22Z with
  no rollback** ("Deployed", the first 48 hours): fix forward.
- **Mail is on** (`MAIL_ENABLED=true` since the deploy): a rollback's 1a
  backend deploy leaves it unset, and every 1b backend deploy exports it.
- **The iCloud guard is on `main` only** (PR #25): a deploy from
  `release/1a` or `release/1b` checks `frontend/dist` for conflict copies
  by hand before answering `y`.
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

Release 1b is live: backend 2026-11-26 12:22:08Z, frontend 12:45:51Z, from
`release/1b` (`634224a`), **with mail on**. Started at 12:07Z, 2 h 40 min
after the planned 09:30 GMT; the user chose to keep the whole day on the
deploy day. Every time below is UTC; the commands are the Release 1b deploy
checklist's, run from `infra/` with `AWS_PAGER=""`, answers to the deploy
gates written through a pipe after the user's yes in chat (the Release 1a
method). The schools are on holiday (D25): no runs all day.

### Before the deploy (12:08–12:18Z)

- **Item 1, 12:08:53Z:** all 16 alarms `OK` (12 in af-south-1, 4 in
  us-east-1); one subscription per topic, each a full ARN; the SES identity
  `true`/`SUCCESS`/`SUCCESS`; since the 2026-11-25 13:13Z read, 0 ERROR or
  WARNING lines over the three groups, and the gateway answered 15,970
  requests: 25 `4xx`, 0 `5xx`.
- **Item 2:** SES production access **granted** since the last read:
  `production` true, `sending` true, review `GRANTED`, 50,000 mails a day.
  The user's decision, 2026-11-26: `MAIL_ENABLED=true` on this deploy, so the
  mail-on screens ship; the mail drill and parent drill step 2 run today
  (replacing the 2026-11-24 "not run: mail off"). From now on every 1b
  backend deploy exports `MAIL_ENABLED=true` (live parity) until a release
  changes the Default; a rollback's 1a deploy leaves it unset.
- **Item 3, 12:10Z** (`verify-db.sh --strict`, exit 0): `migrations` ends on
  `017_account_hardening`; `drivers-with-several-driver-memberships` 0;
  `driver-pin-hash-versions-per-school` Msingi Bora 4 `legacy_hmac`,
  Voluntari Academy 1, `salted_v2` 0, `no_pin` 0;
  `unverified-parent-accounts-by-age` every bucket 0; `token-rows-by-purpose`
  `verify` all 0 (`reset` 0 live, 1 used); `last-retention-run` empty;
  `notifications-past-90-days` 1; `stale-sessions` 272 sessions and 23
  pre-auth tokens expired over 7 days; `pin-lock-state` no failures;
  `temporary-passwords-outstanding` none; `consent-state` 6 parents;
  `open-runs-and-today` 0/0.
- **Item 4:** Msingi Bora's 4 driver accounts are placeholders, not real
  people (the user, 2026-11-26); Voluntari Academy's is the test driver.
  Nobody had to be reached; the re-issue below gave the PINs to no one.
- **Item 10:** `open-runs-and-today` 0/0; the database windows
  `23:00-23:30` and `sun:00:00-sun:01:00`, nothing pending; the deploy ran
  12:18–12:46Z, clear of both.
- **Item 11:** manual snapshot `saferide-pre-1b-20261126`, created 12:13:17Z,
  `available` at 12:17:49Z (20 GB, encrypted). Keep it at least 35 days.
- **Items 12–13:** the stray-variable check in the deploy shell printed only
  `CONCURRENCY_ALARM_THRESHOLD` and `MAIL_ENABLED`; HEAD `634224a` tagged
  `release/1b`, `git status --short` only `.claude/` and `justfile`.

### Backend (12:18:10–12:22:08Z)

`CONCURRENCY_ALARM_THRESHOLD=800 MAIL_ENABLED=true CONFIRM_CHANGESET=1
./scripts/deploy-backend.sh`: a clean checkout (HEAD tagged `release/1b`);
overrides `MailEnabled=true` and `ConcurrencyAlarmThreshold=800` only, every
other parity value at its Default; the alert address kept from the stack;
`open-runs: 0`. The change-set table was **row for row the dry run's**
(above): 15 `Add`, 6 `Modify` including `HttpApi` `Body`, no `Remove`, no
`Database` row, no `WARN` (`ApiFunction` changed `Environment, Code`, as in
the dry run). The user said yes; `y` at 12:20:53Z; the open-run re-check 0;
`Stack saferide-backend deployed` at 12:22:08Z. Migrate: `applied` `[]` (17
skipped, the last `017_account_hardening`), seeds skipped by name
(`APP_ENV=production`), `app_role.synced` `true`, provider bootstrap already
applied. Health: `{"status":"ok","db":"ok","version":"634224a"}`.

### The first retention run (12:22:33Z)

Invoked asynchronously (`StatusCode` 202). `run_id` 1, `status` `ok`,
12:22:38.24–12:22:38.38Z, counts: `live_notifications` 1,
`password_reset_tokens` 1, `auth_sessions` 272, `auth_preauth_tokens` 23,
`driver_action_keys` 39, `trail_batches` 2, every other count 0. Afterwards
`notifications-past-90-days` 0 and `stale-sessions` 0/0, the drop the
baselines predicted. `RetentionRunSucceeded` 1 in that hour.

`RetentionHeartbeat` (1 h period, 26 of 26, missing data breaching) was
created at 12:21:02Z `INSUFFICIENT_DATA` and went to `ALARM` at its first
evaluation, 12:59:21Z ("no datapoints were received for 26 periods"): that
evaluation covered the complete hours up to 11:00–12:00 only, not the hour
of the run. The rehearsal deleted the alarm at 13:57:07Z, before its next
evaluation, so that first alarm never showed `OK`. The alarm re-created at
14:13:22Z was `INSUFFICIENT_DATA` until
the alarm drill set it to `ALARM` at 14:27:18Z, and returned to `OK` at
15:27:22Z, one hour after the drill: "1 out of the last 26 datapoints
[1.0 (26/11/26 12:00:00)] was not less than the threshold", the 25 missing
ones treated as breaching; its OK mail was sent.

### Frontend (12:23:35–12:45:51Z)

`CONFIRM_CHANGESET=1 CSP_MODE=report-only ./scripts/deploy-frontend.sh`:
overrides `DomainName`, `HostedZoneId`, `ApiOrigin`, `CspMode=report-only`;
`MonthlyBudgetUsd=100` at its Default; the alert address kept. One row, as
expected, but **not as written in the checklist**:

```text
  Action  LogicalResourceId  ResourceType          Replacement  Changed
  Modify  MonthlyCostBudget  AWS::Budgets::Budget  Conditional  Budget

WARN: MonthlyCostBudget (AWS::Budgets::Budget) may be replaced (Replacement: Conditional)
```

The checklist expected `False` with no `WARN`. The gate reads
`describe-change-set` without property values, where CloudFormation judges
the whole `Budget` property ("Conditionally", since one of its
sub-properties, the name, would replace). With `--include-property-values`
the same change set reads `Replacement` `False` and two changes, both
`RequiresRecreation: Never`: `CostFilters` added (`Region` af-south-1,
us-east-1, global) and `BudgetLimit.Amount` 60 → 100, the values the live
budget already held since 2026-11-20 (PR #19). The name is generated and
unchanged. The user accepted the row; `y` at 12:45:06Z; the stack updated in
place (the live budget read at 14:42Z, after the rehearsal: the same
generated name, 100 USD, the region filter). The build uploaded; invalidation `I6C5CPBFDUJHZO5UGYS5TCT0GN`
created 12:45:51Z and `Completed` when read at about 12:46Z: **the reload
time**. The site serves `index-DJ0EAJZ3.js`; `sw.js` is byte-identical to the
build.

**iCloud conflict copies.** The working copy lives in a Documents folder that
iCloud syncs. At 12:42Z, while the deploy waited at its prompt, iCloud put 13
stale copies of a 2026-11-24 build into `frontend/dist` ("`index 2.html`",
"`sw 2.js`", four assets, five icons, the manifest and the push payload),
and the sync uploaded them at 12:45:43–48Z. Nothing linked to them and the
live `index.html` and assets were the new build. On the user's go they were
removed at 12:52:09Z (the 13 keys exactly; the bucket back to its 14 files)
with the local copies. The guard that now refuses such copies before an
upload is on `main` (PR #25, `a59ed5f`), not in `release/1b` or
`release/1a`: a deploy from either tag checks `frontend/dist` by hand before
answering `y` (done at each frontend deploy of the rehearsal: none).

### Smoke (12:47–13:49Z)

1. **Health:** `{"status":"ok","db":"ok","version":"634224a"}`.
2. **No API docs:** `docs 404`, `redoc 404`, `openapi.json 404`.
3. **Request id and headers:** `x-request-id` a fresh UUID (not
   `drill-not-echoed`); `strict-transport-security: max-age=31536000;
   includeSubDomains`; `x-content-type-options: nosniff`; `cache-control:
   no-store`; `content-security-policy: default-src 'none'; frame-ancestors
   'none'`. The site's headers over the four URLs identical to before the
   deploy (CSP report-only).
4. **Mail state:** `mailEnabled` `True`.
5. **PIN drill**, on the user's phone (installed app) and a private window:
   1. 12:47:14Z: the PIN without a school answered the `school-required`
      body (401); a `school-required` line and a `SchoolRequired` point.
   2. 13:10:17Z: the typed driver code looked up (200), "Is this your
      school?" with Voluntari Academy, **Yes**; 13:16:07Z the card's link in
      a private window asked the same.
   3. 13:11:43Z: a wrong PIN, "Invalid PIN" (401), a `pin-failure
      school=VOL-001` line, a `PinFailures` point, `pin-lock-state` one
      failure at the test school.
   4. 13:10:56Z: the right (legacy) PIN signed in;
      `driver-pin-hash-versions-per-school` at Voluntari Academy
      `salted_v2` 0 → 1, `legacy_hmac` 1 → 0.

   Two operator slips worth the drivers' and offices' messages: the user's
   first try typed the PIN into the new **Driver code** box (refused on the
   phone, no request sent), and the computer's tab, opened before 12:45Z,
   still ran the 1a screens (no Driver code card) until it was closed and
   reopened.
6. **Mail drill**, with production access (no drill-only toggle):
   1. Not needed: mail was on from the deploy.
   2. 13:19:23Z: Forgot password for a drill parent with the user's own
      inbox: "Reset your SafeRide password" from `no-reply@saferidelive.co.ke`;
      Gmail's original: SPF `PASS` (from an Amazon SES address), DKIM `PASS`
      for `saferidelive.co.ke`, DMARC `PASS`; the return path on
      `mail.saferidelive.co.ke`; the link opened "Set a new password" and
      landed signed in. The three-an-hour cap was not exercised.
   3. Parent drill step 2: below.
   4. The forced failure: on the user's go the configuration set
      `saferide-transactional` was paused at 13:23:31Z; Forgot password at
      13:25:25Z answered the neutral card (200) and logged `mail-send-failed
      kind=reset request_id=38ad7964-… error=SendingPausedException
      code=SendingPausedException`, with no address in it (a paused set
      answers `SendingPausedException`, not the sandbox's
      `MessageRejected`); sending resumed at 13:25:35Z (read back
      `SendingEnabled` true; the window 2 min 4 s). A `MailSendFailed`
      point at 13:25; the alarm `ALARM` 13:26:38Z, `OK` 13:41:38Z (it needs
      three empty five-minute periods to clear).
   5. Not applicable: mail stays on.
7. **Parent drill** at Voluntari Academy (two test students given drill
   parent emails at 13:30:56Z and 13:31:27Z, before the rehearsal):
   1. 13:33:10Z: sign-up A (the user's inbox): "Check your email"; the
      Parents page still **Awaiting signup**; no "Parent signup auto-linked"
      (the user). The `has_link` false `under_7d` count was read only after
      step 2; it was proven on sign-up B instead: 13:42:04Z, sign-up B
      (`success+…@simulator.amazonses.com`, the SES mailbox simulator, so
      no bounce), `has_link` false `under_7d` 1, one live `verify` token.
   2. 13:34:37Z: A signed in and verified from its own session ("Email
      verified", 200); the child on Home without a reload; **Registered**;
      the office's "Parent signup auto-linked" (the user). Unverified
      buckets back to 0. (A self-service email reset of A at
      13:40:32–13:40:58Z also passed.)
   3. 13:44:38Z: **Reset password** on A: a `parent-password-reset` row;
      A signed in with the temporary password at 13:45:55Z and chose their
      own at 13:46:16Z.
   4. 13:46:48Z: **Confirm and reset** on B's Awaiting signup row:
      `parent-email-confirmed` and `parent-password-reset` rows. A second
      **Reset password** at 13:47:49Z (the user had first chosen the wrong
      student) left a fourth row; B signed in at 13:47:56Z and chose their
      own at 13:48:21Z. `temporary-passwords-outstanding` back to none.
8. **`hardening` final**, 13:49:07Z, `--strict` exit 0, diffed against the
   12:10Z read. Unchanged: `017-objects-present`,
   `audit-check-widening-017`, `unverified-parent-accounts-by-age` (all 0),
   `drivers-with-several-driver-memberships`, `schools-without-driver-code`,
   `pin-lock-state`, `temporary-passwords-outstanding`,
   `push-subscriptions-by-kind`, `instance-settings`,
   `open-runs-and-today` 0/0. Changed, each explained:
   `token-rows-by-purpose` (`reset` 1 live, the failed send's token, 30
   minutes; `verify` 0 live); Voluntari Academy `salted_v2` 1, `legacy_hmac`
   0; the retention deletions and `last-retention-run` `ok`;
   `parent-reset-audit-recent` the four drill rows, all `actor_kind`
   provider; `consent-state` 6 → 8 parents (the two drill parents);
   `driver-last-sign-in-per-school` — see the findings.
9. **Alarms**, 13:49Z: every alarm `OK` except `RetentionHeartbeat`
   (`ALARM` since 12:59:21Z, above). The drills' pairs: `MailSendFailed`
   13:26:38Z → 13:41:38Z.

One `psycopg.pool` WARNING ("error connecting in 'pool-1': connection
timeout expired") at 13:13:27Z, the same background reconnect as the two of
2026-11-24: no failed request, 0 `5xx` since the deploy.

### Rollback rehearsal (13:53–14:26Z)

On the user's go; no student saves or imports from step 1 to step 8.

1. 13:53Z: `open-runs-and-today` 0/0; the salted test driver at Voluntari
   Academy; `verify` 0 live. `purge-verify-tokens` at 13:53:53Z:
   `{"status": "ok", "action": "purge-verify-tokens", "deleted": 2}` (the
   two used drill tokens; none live), `FunctionError` `None`.
2. **Backend to `release/1a`** (`a66d53b`), 13:54:13Z, `MAIL_ENABLED` unset,
   `CONCURRENCY_ALARM_THRESHOLD=800`: 15 `Remove` (the 1b additions) and 6
   `Modify` (`ApiClientErrorsBurst`, `ApiFunction`, `HttpApi` `Body` — the
   same dynamic row — `MigrateFunctionErrors`, `MigrateFunction`,
   `VerifyFunction`), no `WARN`; the user's yes; deployed 13:58:49Z; migrate
   `[]`; health `{"status":"ok"}`.
3. **Frontend to `release/1a`**, 13:59:04Z, `MONTHLY_BUDGET_USD=100`: the
   budget row `Conditional` again, property-level `False` (`CostFilters`
   removed, `Never`); no conflict copies; the user's yes; done 14:05:37Z;
   invalidation `I1WLFI8R9QO9TQL8DM98082I2D` `Completed` 14:06:06Z; the site
   on `index-CtM7Urah.js`.
4. On 1a (tabs and the app closed and reopened): a staff password and TOTP
   sign-in, 14:09:17–39Z.
5. The salted PIN refused (`pin-login` 401, 14:10:17Z); a four-digit PIN set
   on 1a's Drivers page (14:10:53Z) signed in (14:11:02Z): **a legacy PIN
   sign-in on 1a**; Voluntari Academy back to `legacy_hmac` 1.
6. `verify` 0 live on 1a.
7. No student saves or imports.
8. **Back to `release/1b`:** backend 14:11:39Z, `MAIL_ENABLED=true`
   exported again, the table identical row for row to the deploy's, the
   user's yes, deployed 14:14:27Z, migrate `[]`, health `634224a` `db` ok,
   `mailEnabled` `True`; frontend 14:14:40Z, the budget row (`CostFilters`
   added, `Never`), no conflict copies, the user's yes, done 14:16:04Z,
   invalidation `I2TMQT0DRD1NOTG4C3BHMAZCMS` `Completed` 14:16:21Z,
   `index-DJ0EAJZ3.js`.
9. On 1b: the four-digit PIN signed in at 14:19:00Z and was salted again
   (`salted_v2` 1). The six-digit re-issue was refused twice (PUT 400 at
   14:22:02Z and 14:22:56Z): the computer's tab still ran the 1a bundle,
   whose form sends four digits, which 1b refuses ("PIN must be 6 digits").
   After closing and reopening it: saved 14:25:39Z, signed in with the
   six-digit PIN at 14:26:17Z.
10. **Durations** (backend start to invalidation complete, including the
    pauses for the user's answers): 1b → 1a **11 min 53 s**, 1a → 1b
    **4 min 42 s**. Everything went back; the only residue is the first
    heartbeat alarm's history (deleted while in `ALARM`).

### PINs, the alarm drill and the Maps key (14:27–14:37Z)

- **PIN re-issue:** at Msingi Bora, six-digit PINs generated and saved for
  the 4 placeholder drivers at 14:35:45–14:36:49Z (four PUT 200) and given to
  no one; no message was sent (no real drivers). Voluntari Academy's test
  driver re-issued in rehearsal step 9.
  `driver-pin-hash-versions-per-school`: Msingi Bora `salted_v2` 4 of 4,
  Voluntari Academy 1 of 1, `legacy_hmac` 0, `no_pin` 0;
  `drivers-with-several-driver-memberships` 0. A rollback to 1a would now
  need a new PIN for all five.
- **Alarm drill**, on the user's go, 14:27:15Z: `ApiServerErrors` `ALARM`
  14:27:16Z → `OK` 14:27:54Z; `RetentionHeartbeat` `ALARM` 14:27:18Z →
  `OK` 15:27:22Z, an hour after the drill; each transition's SNS action succeeded. The user
  confirmed the ALARM and OK mails arrived (one operator address, the 1a
  record's U1.7).
- **The old Maps key** (D45), on the user's go, through `gcloud` at
  14:34:33Z: "SafeRide Web (browser)" now allows only
  `http://localhost:5173/*`, `http://127.0.0.1:5173/*` and
  `http://localhost:4173/*` (the two `saferidelive.co.ke` entries removed);
  its API list unchanged. The live bundle carries the "Saferide production"
  key (apex and www only), not the old one (compared without printing
  either), and the Fleet Map drew (the user).

### The next morning (2026-11-27, 10:29–10:45Z)

- `last-retention-run`: `run_id` 2, `ok`, started by the schedule at
  22:30:55Z and finished in the same second, 11 h 58 min old at the read;
  `trail_batches` 2, every other count 0. `RetentionRunSucceeded` 1 in the
  night's hour; `RetentionHeartbeat` `OK` (since 15:27:22Z).
- The Scheduler: `InvocationAttemptCount` (1 at 22:30Z) under
  `ScheduleGroup` = the stack's `OpsScheduleGroup`, the dimension
  `RetentionScheduleTargetErrors` watches (`AWS/Scheduler`
  `TargetErrorCount`, the sum over 5 minutes, missing data not breaching).
  `TargetErrorCount` is not listed yet (no target error so far), so the
  sibling metric proves the dimension.
- All 22 alarms `OK` (18 in af-south-1, 4 in us-east-1),
  `MigrateFunctionErrors` and `RetentionScheduleTargetErrors` among them.
- ERROR lines over the three groups since 12:18Z: one, the mail drill's
  `SendingPausedException` (13:25:25Z). WARNING lines: the drills'
  `school-required` and two `pin-failure`, and two `psycopg.pool`
  reconnects (13:13:27Z on the deploy day, 06:36:17Z; "Findings").
- Also read: the gateway since the smoke, 15,290 requests, 266 `4xx` (251
  `404` from scanners probing for secret files; the 15 others all in the
  14:10–14:35Z drill and rehearsal session) and 0 `5xx`; database
  connections at most 24 of 79 (14:10Z, the drills); the automated backup
  23:11–23:15Z, nothing pending; CloudTrail logging, no delivery error. No
  app use overnight (school holiday, no runs).

### The first 48 hours (to 2026-11-28 12:22Z)

Read at 11:30–11:36Z (+47 h) and at 12:23Z (+48 h):

- **Subscriptions** (+47 h): one per topic, `email`, a full ARN in both
  regions; none `PendingConfirmation`.
- **Alarms:** all 22 `OK` at both reads; no state change since 15:27:22Z
  on the deploy day (`describe-alarm-history` since 12:18Z holds only the
  deploy's and the rehearsal's creations, the heartbeat's first evaluation
  and the two drills). `SchoolRequiredBurst`, `PinFailureBurst`, `PinLocked`
  and `ApiClientErrorsBurst` never fired.
- **The second nightly run:** `run_id` 3, `ok` at 22:30:55Z on 2026-11-27:
  `auth_sessions` 8, `password_reset_tokens` 3, `pin_login_failures` 2,
  `trail_batches` 2, every other count 0; `stale-sessions` 0/0 after it.
  `RetentionRunSucceeded` has a point in the hour of each of the three runs.
- **`hardening`** (11:31Z): `salted_v2` 4 and 1, `legacy_hmac` 0, `no_pin`
  0; no driver with several memberships; no school without a driver code;
  no PIN failures in the last 15 minutes; no live token; no unverified
  account; no temporary password; no open run.
- **ERROR lines** over the three groups since the deploy: still only the
  mail drill's (read at 12:23Z); no WARNING line since the 2026-11-27 read;
  no failed invocation since the deploy.
- **The gateway** since the 2026-11-27 read: 17,407 requests, 4 `4xx` (no
  non-404 one in the API log), 0 `5xx`; 0 `5xx` since the deploy. One app
  request apart from the health checks (05:08Z, `GET /api/push/config`,
  200).
- **The database:** at most 8 connections in the last 24 hours (of 79);
  the automated backups at 23:11Z each night, `available`; no pending
  maintenance. CloudTrail logging, no delivery error. Health
  `{"status":"ok","db":"ok","version":"634224a"}`; both stacks
  `UPDATE_COMPLETE` from the deploy day.
- **Spend** (Cost Explorer, the whole account): 5.33–5.76 USD a day from
  2026-11-19 to 2026-11-25, 5.37 USD on 2026-11-26 and 4.81 USD on
  2026-11-27 (estimated, the day not yet complete); no new service line.
- **Not read:** the first school morning (database connections at the
  peak, drivers' sign-ins, the `gps` trail rows): a school holiday, no runs.
  Blank-map reports: no app use in the window apart from the deploy day's
  drills, so nothing to report.
- **Rollback decision:** none needed. The window closed at 12:22Z; fix
  forward from here.

### Findings and follow-ups

- **The checklist's frontend row** reads the gate's summary view: expect
  `Conditional` with its `WARN`, and confirm `False` / `Never` with
  `describe-change-set --include-property-values` (here and in the
  rehearsal's two frontend deploys).
- **`RetentionHeartbeat` timing:** a new heartbeat goes `ALARM` at its first
  evaluation even after a successful run, because that evaluation covers
  complete hours only, so the checklist's "back to `OK` at its next
  evaluation" does not hold for the first one. A drilled heartbeat comes
  back about one period after the drill, not within a minute as the 1a
  alarms do: `ALARM` 14:27:18Z, `OK` 15:27:22Z, on the run's point. The
  checklist's alarm-drill line needs "about an hour" for it.
- **The forced mail failure in production** logs `SendingPausedException`,
  not `MessageRejected`; the checklist's Go line names only the latter.
- **`driver-last-sign-in-per-school`** reads sessions, which the retention
  job deletes once expired for 7 days: after the first run every Msingi Bora
  driver reads `null`, which the checklist calls "never signed in". The
  check or its wording needs fixing before it is used to find drivers.
- **Stale tabs:** a tab or app opened before a frontend deploy keeps the old
  screens until it is closed. A 1a tab on 1b cannot set a PIN; offices and
  drivers need the "close it completely and open it again" line.
- **Dashboard requests after step-out and sign-out:** a few queries for the
  school just left answer 403 (13:19:06Z, 14:19:17Z) or 401 after sign-out
  (14:24:48Z). Harmless, but they count toward the non-404 4xx alarm.
- **iCloud:** above; the guard is PR #25 on `main`.
- **`psycopg.pool` reconnect warnings:** "error connecting in 'pool-1'"
  (a connection timeout, or "SSL connection has been closed
  unexpectedly") at 13:13:27Z on the deploy day and 06:36:17Z on
  2026-11-27, with no failed request: the health check in the same second
  answered 200. Not new in 1b: five under 1a (three on 2026-11-19, one
  each on 2026-11-24 and 2026-11-25). A WARNING the pool recovers from;
  worth a look only if a request fails with it.
- **Retention and sessions:** the second night deleted the 8 sessions
  that had passed 7 days expired since the first run, so `stale-sessions`
  stays near 0, and `driver-last-sign-in-per-school` reads `null` for a
  driver 7 days after their last session expires (the finding above).

### Cost

No fixed-cost resource is added. 1b adds six CloudWatch alarms, five log
metric filters, one Scheduler schedule (one run a night) and SES sending at
production rates; it was not priced on the deploy day. The SafeRide-only
budget (100 USD a month, PR #19) is live and tracks it. The spend read in
the first 48 hours (above) shows no new service line and the account's
daily spend unchanged.

### Still open

- **Before the first route day** (the checklist's freeze readiness
  check, two working days before the term; it carries into Release 2's
  record), with the first school morning's reads: database connections
  at the peak, the drivers' sign-ins and the `gps` trail rows. Fix
  `driver-last-sign-in-per-school` first ("Findings").
- The checklist follow-ups in "Findings and follow-ups".
- The Samsung Internet check before Release 2 enforces the CSP (the 1a
  record).
