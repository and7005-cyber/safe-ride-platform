# SafeRide AWS infrastructure

Production deployment for SafeRide Kenya.

- **Frontend** — `frontend/dist` (Vite build) → private **S3** bucket served by
  **CloudFront** (Origin Access Control) at `https://saferidelive.co.ke` (+ `www`).
- **Backend** — FastAPI wrapped with **Mangum** on **Lambda**, behind an
  **API Gateway HTTP API**, with **RDS PostgreSQL** in an isolated VPC, at
  `https://api.saferidelive.co.ke`. The app's existing bearer-token auth is
  unchanged; API Gateway is a transparent proxy.
- **DNS** — `saferidelive.co.ke` stays registered at **Truehost** (`.co.ke`
  cannot move to Route 53) but is **delegated** to a Route 53 hosted zone.

| Thing | Region | Why |
|-------|--------|-----|
| Route 53 hosted zone `Z0459820URO1AH08IO8T` | global | DNS for the domain |
| CloudFront + its ACM cert | us-east-1 | CloudFront certs must live in us-east-1 |
| API Gateway, Lambda, RDS, its ACM cert | af-south-1 | closest region to Kenya |

## One-time manual step (Truehost)

Set the domain's nameservers at Truehost to the four Route 53 values
(`./scripts/check-delegation.sh` prints them and tells you when it's live).

## Deploy order

```bash
# 0. Confirm the domain is delegated to Route 53
./scripts/check-delegation.sh

# 1. Issue + DNS-validate the ACM certs (needs delegation live)
./scripts/request-certs.sh

# 2. ONCE, before the first tenancy deploy: TOTP pepper + provider bootstrap
../scripts/provider-bootstrap.sh <email1> "<Name 1>" <email2> "<Name 2>"

# 3. Release 1a only: every secret the backend deploy reads is in af-south-1
#    (five "values MATCH" lines; docs/runbooks/hardening-release-checklists.md,
#    operator checklist step 4)
./scripts/copy-ssm-to-backend-region.sh

# 4. Build + deploy backend (alert-address guard, open-run refusal, verify-target
#    check, change-set gate), run DB migrations
#    ALERT_EMAIL_PRIMARY / ALERT_EMAIL_SECONDARY until the stacks hold them
./scripts/deploy-backend.sh          # CONFIRM_CHANGESET=1 to answer y/N first

# 5. Build frontend against the API (Maps key + Map ID from SSM), deploy
#    S3+CloudFront + headers, probes, alarms, budget through the change-set
#    gate; upload (refused while dist holds iCloud conflict copies), invalidate
./scripts/deploy-frontend.sh         # CSP_MODE=report-only (default) | enforce; CONFIRM_CHANGESET=1
```

Backend first, then frontend — a release deploys both, in that order, and a
rollback redeploys the previous release tag the same way ([RESTORE.md](RESTORE.md),
"Code rollback"). `scripts/config.sh` holds the shared config (domain, zone
id, regions, stack names). Cert ARNs land in `.state/certs.env` (gitignored).

### SSM parameters (SecureString; names only — values never in the repo)

| Parameter | Region | Origin | Purpose |
|---|---|---|---|
| `/saferide/db-password` | af-south-1 | operator; **must pre-exist** | RDS master password (migrations, seeds, verify) |
| `/saferide/db-app-password` | af-south-1 | operator; **must pre-exist** | password of the `saferide_app` runtime role |
| `/saferide/pin-pepper` | af-south-1 | operator; **must pre-exist** | HMAC pepper for driver PINs |
| `/saferide/totp-pepper` | af-south-1 | **`scripts/provider-bootstrap.sh` — must pre-exist** | derives every provider's TOTP secret |
| `/saferide/google-maps-api-key` | af-south-1 | operator; **must pre-exist** | server-side Google Maps key (geocoding, places, routes) |
| `/saferide/google-maps-browser-key` | af-south-1 | operator (Google Cloud); **must pre-exist** | browser Maps key restricted to the production origins; built into the frontend by `deploy-frontend.sh` |
| `/saferide/google-maps-map-id` | af-south-1 | operator (Google Cloud); **must pre-exist** | production Map ID; built into the frontend by `deploy-frontend.sh` |
| `/saferide/provider-bootstrap` | af-south-1 | **`scripts/provider-bootstrap.sh` — must pre-exist** | versioned payload the migrate Lambda uses to create/re-key the Kuumbai provider accounts |
| `/saferide/provider-initial-password/<email-slug>` | af-south-1 | `scripts/provider-bootstrap.sh` | each bootstrap account's initial password, retrieved out-of-band by the operator |
| `/saferide/firebase-*`, `/saferide/vapid-*` | af-south-1 | seeded once from `backend/.env` when absent ("Deploy safety") | push (see docs/push-notifications.md) |

The first five also have older copies in the operator's default CLI region
(the copy helper prints which), where the deploy script read them before the
hardening work. `scripts/copy-ssm-to-backend-region.sh` copies them to
af-south-1 and proves each pair equal by digest without printing a value
([operator checklist](../docs/runbooks/hardening-release-checklists.md#operator-checklist-before-release-1a),
step 4). The old copies stay until one week after Release 2 is stable (D15),
because `release/0`'s deploy script still reads them there — and mints a new
database password, PIN pepper or app-role password when one is missing — so
once they are deleted `release/0` must never be deployed again. The current
scripts read only af-south-1.

`deploy-backend.sh` **fails fast** if any parameter marked "must pre-exist"
is missing — none is ever minted. A silently re-minted TOTP pepper would
invalidate every provider's enrolled second factor at once; rotating it
deliberately has the same consequence (every provider needs a peer TOTP reset
and a fresh enrolment — see the provider guide's rotation warning).
Re-running `provider-bootstrap.sh` bumps the payload version so the next
deploy re-keys (or newly creates) the accounts in the payload — the
password-lockout recovery path, and the way to introduce a fresh unenrolled
account after a pepper incident. It mints a TOTP pepper only when both
af-south-1 and the default CLI region answer `ParameterNotFound`: a copy in
the default region must be copied, never replaced, and any other read error
(credentials, permissions, network, no region) stops it before it mints.

## Deploy safety

Both deploy scripts refuse to ship anything but a released, committed tree,
and the backend deploy never generates a secret.

**Parameter region rule.** Every `/saferide/*` parameter a deploy reads lives
in **af-south-1** (`BACKEND_REGION` in `scripts/config.sh`), whatever the
operator's default CLI region. There are two classes:

- *Passed through CloudFormation parameters* — `deploy-backend.sh` reads the
  value at deploy time and hands it to the stack as a `NoEcho` parameter that
  lands in a function's environment: `db-password`, `db-app-password`,
  `pin-pepper`, `totp-pepper`, `google-maps-api-key`, `firebase-vapid-key`
  and the `vapid-*` values.
- *Read from SSM by the Lambda at runtime* — the function's IAM policy names
  the parameter in its own region, so a copy anywhere else is invisible to
  it: `firebase-service-account-json` and `firebase-web-config-json` (API),
  `provider-bootstrap` (migrate).

The six backend "must pre-exist" parameters (the five secrets and
`provider-bootstrap`) are read with `ssm_required` (`scripts/config.sh`,
shared by both deploy scripts): a missing one stops the deploy before
`sam build` with
`ERROR: SSM <name> (af-south-1) is missing — copy or create it in af-south-1 …`,
and any other read error (credentials, permissions, network) stops it naming
AWS's error. Copy a missing secret from where it lives today as the
[operator checklist](../docs/runbooks/hardening-release-checklists.md#operator-checklist-before-release-1a)
describes; never re-create it, because a new pepper silently invalidates
every driver PIN or provider second factor and a new database password
rotates the master password under the running stack. `deploy-frontend.sh`
reads the two Maps values the same way before it builds (see "Security
headers").

The push parameters (`firebase-*`, `vapid-*`) are optional. When one is
missing in af-south-1 the deploy seeds it once from `backend/.env`: python3
writes the `put-parameter` input (SecureString, no `Overwrite`) into a 0600
file in a private temporary directory — the value never reaches an argv —
and the deploy reads the parameter back. A read error other than
`ParameterNotFound` aborts the deploy instead of seeding over it or deploying
an empty value; a value missing from both SSM and `.env` leaves that push
channel off (the in-app feed still works).

**Clean checkout.** `require_clean_checkout` (`scripts/config.sh`) runs first
in both deploy scripts. It fetches `origin`, refuses modified or staged
tracked files, and passes only when HEAD is `origin/main` or carries a
`release/*` tag. Untracked files are ignored (`.claude/` and `justfile` stay
untracked on purpose). Emergency hatch: `ALLOW_UNRELEASED_DEPLOY=1` waives
the main-or-tag rule with a loud warning — for a fix that cannot wait for a
merge; tag the commit afterwards and record why in the release's validation
record. It never waives the modified/staged check.

**Untracked-file guard.** SAM packages the whole `backend/` directory, so
`deploy-backend.sh` also aborts, listing the files, when
`backend/db/migrations` or `backend/db/seeds` holds any file git does not
track, or when `git ls-files --others --exclude-standard backend/app
backend/db` prints anything. Ignored files (`__pycache__`, `.env`) are
outside this guard.

**Alert-address guard.** Before building, both deploy scripts work out the
primary alert address their stack will have: the exported
`ALERT_EMAIL_PRIMARY`, else the stack's current `AlertEmailPrimary` value
(`describe-stacks`). That value is empty when the stack or the parameter
does not exist — SAM drops `UsePreviousValue` for a parameter the stack
lacks, so after a rollback to `release/0` the template's empty Default
applies. With no address every alarm would email no one, so the deploy
refuses ("no alert address: alarms would email no one — export
ALERT_EMAIL_PRIMARY"); `ALLOW_NO_ALERTS=1` deploys anyway behind a loud
warning. When nothing is exported and the stack holds an address, the
script passes that address explicitly, so a later change to the parameter
set cannot drop it. `deploy-backend.sh` reads the backend stack,
`deploy-frontend.sh` the frontend stack.

**Open-run refusal.** A deploy restarts the API and migrates the database
under any run in progress. Before it builds anything, `deploy-backend.sh`
invokes the verify function the stack has today with its `tenancy-preflight`
set and reads the `open-runs` row — the `live_runs` not yet completed, from
any day, so a run left open on an earlier day blocks too. It refuses the
deploy when that count is above 0, and also when it cannot read one: a stack
with no `VerifyFunctionName` output (the first-ever deploy of a stack), a
failed invoke, an answer whose status is not `ok`, or a missing or failed
row. `ALLOW_OPEN_RUNS=1` deploys anyway behind a loud warning — for that
first deploy, or an emergency fix or rollback that cannot wait for the runs
to end. Only the count is printed. The check runs a second time immediately
before the change set executes — after the gate and any `CONFIRM_CHANGESET`
prompt, which can take minutes — with the same rule and override: a run
opened meanwhile deletes the change set and stops the deploy.

**Verify-target check.** The count means something only if the verify
function reads production. The open-run check in `deploy-backend.sh`, and
`verify-db.sh` before every invoke, compare the host of the verify
function's `DATABASE_URL` with the stack's `DbEndpoint` output and refuse
when they differ — the function still points at a scratch restore
([RESTORE.md](RESTORE.md), step B). Only the host is read
(`get-function-configuration --query Environment.Variables.DATABASE_URL`
piped into python3's `urlsplit`); the URL, which carries the master
password, is never printed. `ALLOW_VERIFY_TARGET_MISMATCH=1` overrides it,
for the runbook's reads against a scratch instance; a deploy has no reason
to.

**Change-set gate.** Every backend deploy runs
`sam deploy --no-execute-changeset` with `--stack-name "$BACKEND_STACK"
--region "$BACKEND_REGION"` named explicitly, so `samconfig.toml` cannot aim
it at another stack than the one reviewed. It pipes `describe-change-set`
into `scripts/changeset_gate.py review` (system python3, stdlib only), which
prints each change (action, logical id, type, replacement, changed
properties) and exits 2 — the deploy then deletes the change set, so nothing
executes — when:

- the change set is not `CREATE_COMPLETE` and `AVAILABLE`;
- the `Database` resource lists `MasterUserPassword` among its changes,
  whatever its replacement value (the af-south-1 `db-password` copy does not
  match the live password);
- any resource has `Replacement: True`, except
  - the stateless types `AWS::SNS::Subscription`, `AWS::CloudWatch::Alarm`
    and `AWS::Logs::MetricFilter`, rebuilt from the template with nothing to
    lose: a `WARN` line, and the deploy goes on (changing an alert address
    replaces its subscription this way — see "Alarms");
  - the logical ids named in `ALLOW_REPLACE=<LogicalId,...>`, for this
    deploy only: a loud `WARN` block, and the deploy goes on. An
    `ALLOW_REPLACE` id the change set does not replace is a `WARN` too.
    `ALLOW_REPLACE` never applies to the never-replaceable types
    `AWS::RDS::DBInstance`, `AWS::CloudFront::Distribution` and
    `AWS::S3::Bucket`: their replacement aborts even when named. A replaced
    database is a new, empty instance (`DeletionPolicy: Snapshot` only
    snapshots the old one) — a restore follows [RESTORE.md](RESTORE.md)
    instead; a replaced distribution gets a new domain name under the
    site's records, and a replaced bucket starts empty.

`Replacement: Conditional` prints a `WARN` and continues. With
`CONFIRM_CHANGESET=1` exported the script asks `y/N` before executing (the
Release 1a deploy runs this way); otherwise it executes once the checks pass.
"No changes" skips straight to the migrate step. When sam's output does not
name the change set, `changeset_gate.py newest-since` picks the newest
`samcli-deploy*` change set created since the deploy started (allowing 5 s of
clock skew) that is `CREATE_COMPLETE` and `AVAILABLE`. A failed stack update
prints the latest stack events and exits before migrating.

**Database stack policy.** `saferide-backend` carries a CloudFormation stack
policy (set 2026-11-18, decision D26 in the Release 1a validation record)
that allows every update except replacing or deleting `Database`:

```json
{"Statement": [
  {"Effect": "Allow", "Action": "Update:*", "Principal": "*", "Resource": "*"},
  {"Effect": "Deny", "Action": ["Update:Replace", "Update:Delete"], "Principal": "*", "Resource": "LogicalResourceId/Database"}
]}
```

It is the guard the gate cannot be for a `Conditional` row: changing
`BackupRetentionPeriod` or `PreferredMaintenanceWindow` (as Release 1a does)
always shows `Database` as `Replacement: Conditional`, because the
`AWS::RDS::DBInstance` schema lists both as conditionally create-only, and the
gate only warns. Should CloudFormation decide at execution to replace the
instance anyway, the policy fails the update and it rolls back, before an
empty database exists. A throwaway-stack rehearsal (2026-11-18) applied
exactly 1a's five database changes under this policy: updated in place, same
instance, in about 35 seconds. The policy governs stack updates only, not
`delete-stack` (see "Teardown"), and is unaffected by deploys; read it with
`aws cloudformation get-stack-policy --region af-south-1 --stack-name
saferide-backend`. A deliberate replacement of `Database` would need
`--stack-policy-during-update-body` on that one update, on the user's explicit
go — but the restore runbook's primary path (the rename swap) never updates
the stack.
`backend/tests/core/test_changeset_gate.py` runs the gate on table-driven
payloads and `deploy-backend.sh` end to end against fake `aws`, `sam` and
`git`.

**Frontend change-set gate.** `deploy-frontend.sh` reviews its stack the
same way. After the build it runs `aws cloudformation deploy
--no-execute-changeset` with the same parameters, finds the change set (the
ARN the command prints, else the newest one created since the deploy
started, from `list-change-sets`) and pipes `describe-change-set` into
`changeset_gate.py review`. The backend's rules apply except the
`Database` master-password rule, which has nothing to match here:
`Replacement: True` aborts except for the stateless types, `ALLOW_REPLACE`
names ids to let through for one deploy, the `Distribution` and
`SiteBucket` are never replaceable, and `CONFIRM_CHANGESET=1` asks `y/N`
before executing. An aborted or declined change set is deleted, never
executed. The script then executes it and waits for
`stack-update-complete` (`stack-create-complete` on a first deploy),
printing the latest stack events on a failure; "no changes" goes straight
to the upload.

**iCloud conflict copies.** The checkout lives under `~/Documents`, which
iCloud "Desktop & Documents" syncs. On 2026-11-26 (Release 1b) iCloud wrote
13 conflict copies of an older build (`index 2.html`, `sw 2.js`,
`assets/index.esm-CDWZK7Vo 2.js`, `icons/icon-192 2.png`, …) into
`frontend/dist` about 19 minutes after `vite build`, while the stack update
ran, and the sync uploaded them. `deploy-frontend.sh` now runs
`conflict_copy_guard` (`scripts/config.sh`) after the build and again just
before the upload: any path under `frontend/dist` whose name has a space and
a number before the extension (or at the end) stops the deploy with
`ERROR: sync-conflict copies in …/frontend/dist …`, one line per path. A
refusal after the build has deployed nothing; one before the upload leaves
the stack update live and uploads nothing. Delete the listed files and run
the deploy again: the build rewrites `dist`, and the stack, already
deployed, has no changes, so the re-run goes from the build straight to the
upload. The sync has no `--exclude` for such names on purpose: `sync
--delete` never deletes an object an exclude matches, so copies already in
the bucket (the 13 of 2026-11-26, unless removed by hand) are deleted by the
next deploy, one `delete: s3://…` line each. A deploy from a release tag cut
before this change (`release/1a`, `release/1b`, e.g. a rollback) runs that
tag's script, without the guard. `backend/tests/core/test_conflict_copy_guard.py`
pins the pattern and both refusals.

**Live-parity parameters.** SAM CLI (1.162.1) and `aws cloudformation
deploy` both send `UsePreviousValue` for every parameter a deploy does not
pass to an existing stack, so a changed template Default never reached
production and a one-off export stuck until the next export. Both deploy
scripts therefore source `scripts/parity-params.sh` and pass their template
and list to `build_parity_params` on every deploy — `PARITY_PAIRS` for the
backend (the GPS defaults, the API, pings, sign-in and school-lookup
throttles, the four mail settings, the two alert addresses and
`ConcurrencyAlarmThreshold`) and `FRONTEND_PARAMS` for the frontend
(`DomainName`, `HostedZoneId`, `ApiOrigin`, `CspMode`, `MonthlyBudgetUsd` and
the two alert addresses):

- **Non-empty template Default:** always passed — the exported variable (e.g.
  `GPS_CUSTODY_THRESHOLD_M=200`, `MONTHLY_BUDGET_USD=80`) when it is set and
  non-empty, otherwise the Default read from the template. An export lasts
  one deploy: repeat it on every deploy, or change the Default in a release.
  On the frontend, `DomainName` and `HostedZoneId` follow `scripts/config.sh`
  (`DOMAIN`, `HOSTED_ZONE_ID`), `ApiOrigin` is the origin the build calls,
  and `CspMode` the validated `CSP_MODE` (report-only unless `enforce`). The
  deploy log prints each override and the list of Defaults it passed.
- **Empty template Default** (`AlertEmailPrimary`, `AlertEmailSecondary`,
  `MailReplyTo`): passed when exported, never empty, because SAM refuses an
  empty override. Otherwise the stack keeps its current value, so a stale
  value is replaced only by exporting a new one; no deploy can clear it back
  to empty. `AlertEmailPrimary` is the exception to "keeps": with nothing
  exported the alert-address guard passes the stack's value explicitly, and
  refuses when there is none.

The certificate parameters stay outside the lists: `ApiCertificateArn` and
the frontend's `CertificateArn` are passed only when a certificate ARN is
known (`API_CERT_ARN` / `CF_CERT_ARN`, or `.state/certs.env`). The backend
parameters outside the list (`CorsOrigins`, `AppBaseUrl`, `ApiDomainName`,
`HostedZoneId`, `MailDomain`, `DbEngineVersion`, `DbInstanceClass`,
`DbAllocatedStorage`, `DbMasterUsername`) still keep their previous stack
value: a change to one of their Defaults reaches production only after the
parameter joins the list. A template Default the reader cannot take as a
plain one-line value (`""` for empty) stops the deploy before the build.
`backend/tests/core/test_deploy_params.py` runs `build_parity_params` on both
templates (and `deploy-frontend.sh` end to end) to pin the rule. The variable
names are the ones `backend/.env` uses, so deploy from a shell that has not
loaded that file (direnv or similar): its development values would otherwise
go to production for that deploy.

**Version and seeds.** The stack gets `AppVersion=$(git rev-parse --short
HEAD)`, exposed to the API function as `APP_VERSION`. The migrate function
runs with `APP_ENV=production`, so it applies migrations only and reports the
seed files it skipped; seeds apply only on the local stack (`APP_ENV=local`).
The deploy invokes it with `--cli-read-timeout 0` so a long migration is not
cut off by the CLI.

**Release tags.** `release/0` marks the production commit from before the
hardening work; `release/1a`, `release/1b` and `release/2` mark the commit
each release deployed. A code rollback is redeploying the previous tag —
backend first, then frontend, never the frontend alone — which is safe
because every migration is additive ([RESTORE.md](RESTORE.md)).
`release/0`'s scripts still read the default-region parameter copies, which
is why those copies stay until one week after Release 2 is stable.

## Alarms

The backend stack sends every alarm, and its return to OK, to the SNS topic
`saferide-ops` in af-south-1 (stack output `OpsTopicArn`); SES bounce,
complaint, reject, rendering-failure and delivery-delay events land there
too. Each address exported as `ALERT_EMAIL_PRIMARY` / `ALERT_EMAIL_SECONDARY`
on a deploy gets an email subscription that must be confirmed from that
inbox. Later deploys keep the addresses: their template Default is empty, so
the deploy passes them when exported and keeps the stack's value otherwise
— the primary one passed explicitly, and a deploy with no primary address
at all refused ("Alert-address guard", "Live-parity parameters" above). An
unconfirmed subscription delivers nothing: until its inbox confirms, the
alarms do not reach that address. Changing one means exporting the
new address on the next deploy, and it *replaces* that email subscription:
the change-set gate prints a `WARN` for the replaced `AWS::SNS::Subscription`
(a stateless type) and lets it through, and the new inbox must confirm the
fresh subscription mail. Thresholds are counts, not rates, and missing data
counts as OK — except for `RetentionHeartbeat`, where silence is the failure.

| Alarm | Means | First response |
|---|---|---|
| `ApiServerErrors` | at least one API 5xx in 5 minutes | find the ERROR lines in `/saferide/api` at that time; roll back if a deploy just landed |
| `ApiClientErrorsBurst` | 50 or more API 4xx **other than 404** in 5 minutes, counted from the app's own access lines (since Release 1b; bots scanning for `/.env` and `/.git` answered 404 used to trip it). A 429 the gateway's route throttle makes never reaches the function and is not counted — the PIN alarms below watch sign-in abuse | read `/saferide/api` for the paths and whether one client repeats (broken release, looping phone, sign-in probing): Logs Insights `filter logger = "mangum.http" and message like / 4\d\d$/ and message not like / 404$/ \| stats count() by message`; for throttling, read the API's own `4xx` metric (`AWS/ApiGateway`) and ask the drivers |
| `ApiLatencyP95` | p95 over 3 s in 3 of 5 five-minute periods | check the database CPU and credit alarms and the API function's duration |
| `ApiFunctionErrors` | the API function failed (init crash or timeout) | read the latest `/saferide/api` errors; roll back if a deploy just landed |
| `ApiFunctionThrottles` | the API function was throttled in the last minute | check `AccountConcurrency` and the pings route for a looping client; raise the quota |
| `MigrateFunctionErrors` | the migrate function failed: a migration, the nightly retention run or an on-demand action raised, or it timed out | read `/saferide/migrate`; a retention failure logs `retention-failed` with the step and its `ops_job_runs` row reads `failed` (`verify-db.sh hardening`, `last-retention-run`); fix, then re-run the migrate step (the live API may need it) or invoke the retention action ("Nightly retention" below) |
| `AccountConcurrency` | Lambda concurrency at `ConcurrencyAlarmThreshold` (80% of the account limit, 8 today) | find the runaway client; request a quota increase, then export `CONCURRENCY_ALARM_THRESHOLD` (80% of the new limit) on every backend deploy until a release raises the Default |
| `DbFreeStorageLow` | database free storage under 2 GiB | check the storage graph and that the position-trail purge runs |
| `DbFreeableMemoryLow` | freeable memory under 100 MB for 15 minutes | compare the `DatabaseConnections` graph in RDS with the API's request count for a surge of connections |
| `DbCpuHigh` | database CPU over 80% for 15 minutes | line the CPU graph up with the API's request count and latency and with any migrate or verify run at that time |
| `DbCpuCreditsLow` | under 30 CPU credits for 15 minutes; the instance will be held to baseline | find what burns CPU (`DbCpuHigh`, the API's request count, any migrate or verify run); consider a larger class |
| `ApiCertificateExpiry` | the API certificate expires in under 20 days (custom domain only) | check the api validation CNAME in Route 53 and the renewal status in ACM |
| `RetentionHeartbeat` | no nightly retention run reported success for 26 hours (`SafeRide` / `RetentionRunSucceeded`, missing data **breaching**): the 01:30 EAT run was not started or did not finish. It fires about 26 hours after the last good run — around 04:00 EAT the morning after the first missed night — and it is in ALARM from the deploy that creates it until the first run | read `last-retention-run` in `verify-db.sh hardening` (status, age, counts) and `/saferide/migrate` for the night; check `RetentionScheduleTargetErrors` and that the schedule is enabled; then invoke the retention action by hand ("Nightly retention" below) |
| `RetentionScheduleTargetErrors` | EventBridge Scheduler could not start the run: its invoke of the migrate function failed (the role, a throttle, a missing function); the schedule retries twice within the hour | read the schedule's target and role in the Scheduler console (group from `OpsScheduleGroup`) and the migrate function's state; invoke the retention action by hand once fixed |
| `MailSendFailed` | a reset, verification or notice mail failed to send (`mail-send-failed` in `/saferide/api`) at least once in 5 minutes; the request still answered its neutral message | read the `mail-send-failed` lines for the `kind` and the SES `code` (sandbox, paused sending, throttling) and the SES account state; until mail works, parents get an office reset |
| `PinFailureBurst` | 30 or more failed driver PIN sign-ins in 5 minutes, every school and phone together (`pin-failure school=<code>`; `school=unknown` is a school id that does not exist) | read the `pin-failure` lines for the school codes; call the office of the school that repeats (a phone holding an old PIN after a re-issue, or someone guessing) |
| `PinLocked` | at least one PIN sign-in refused by a school's lock (`pin-locked school=<code>`): more than 20 failures from one source at that school in 15 minutes | read the `pin-locked` lines for the school code and call that office. Signed-in drivers are unaffected, and the lock lifts by itself: each refused phone is told how long to wait (`Retry-After`, counted down on screen) and can sign in once enough of its failures are older than 15 minutes. Nobody clears it by hand |
| `SchoolRequiredBurst` | 20 or more PIN sign-ins in 5 minutes from phones with no school set (`school-required`): phones not yet set up with the driver code, or an old app still cached | have the offices give their drivers the driver code from the Drivers page; check that phones load the current app |

Application exceptions surface as API 5xx: FastAPI answers an unhandled
exception with a 500 response, so `ApiServerErrors` is the application alarm
and Lambda `Errors` means init failures or timeouts. **Lambda errors zero is
not no errors:** a schedule that never fires, or a run that never starts,
raises nothing, which is why the retention run has a heartbeat as well as
`MigrateFunctionErrors`. There is no `DatabaseConnections` alarm yet: its
threshold waits for `max_connections`, read from the `hardening` set's
`instance-settings` row after the 1a deploy.

**Metric filters (Release 1b).** Five `AWS::Logs::MetricFilter` resources
count lines of `/saferide/api` into the `SafeRide` namespace. The functions
log JSON (the formatted text in `message`, the logger in `logger`), so each
pattern selects both; a quoted value with `*` is a whole-value wildcard, and
the 4xx pattern is a regex anchored on the status, the access line's last
word. `backend/tests/core/test_metric_filters.py` matches every pattern
against lines the code really writes; CloudWatch is the authority, so run
these once before the Release 1b deploy (read-only; each prints `matches`,
and only event 1 may appear there):

```bash
aws logs test-metric-filter --region af-south-1 \
  --filter-pattern '{ ($.logger = "mangum.http") && ($.message = %\s40[0-35-9]$|\s4[1-9]\d$%) }' \
  --log-event-messages '{"timestamp": "2026-12-04T05:12:09Z", "level": "INFO", "message": "GET /api/auth/me 401", "logger": "mangum.http", "requestId": "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"}' \
    '{"timestamp": "2026-12-04T05:12:10Z", "level": "INFO", "message": "GET /.env 404", "logger": "mangum.http", "requestId": "4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e"}' \
    '{"timestamp": "2026-12-04T05:12:11Z", "level": "INFO", "message": "GET /files/report-401 404", "logger": "mangum.http", "requestId": "5f3e8b4c-0d6a-4b2c-9e4f-7a8b9c0d1e2f"}' \
    '{"timestamp": "2026-12-04T05:12:12Z", "level": "INFO", "message": "GET /api/health 200", "logger": "mangum.http", "requestId": "6a4f9c5d-1e7b-4c3d-8f5a-8b9c0d1e2f3a"}'
aws logs test-metric-filter --region af-south-1 \
  --filter-pattern '{ ($.logger = "saferide.mail") && ($.message = "mail-send-failed kind=*") }' \
  --log-event-messages '{"timestamp": "2026-12-04T05:12:09Z", "level": "ERROR", "message": "mail-send-failed kind=reset request_id=3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d error=ClientError code=MessageRejected", "logger": "saferide.mail", "requestId": "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"}' \
    '{"timestamp": "2026-12-04T05:12:10Z", "level": "INFO", "message": "mail-capped kind=reset request_id=4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e", "logger": "saferide.auth", "requestId": "4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e"}'
aws logs test-metric-filter --region af-south-1 \
  --filter-pattern '{ ($.logger = "saferide.auth") && ($.message = "pin-failure school=*") }' \
  --log-event-messages '{"timestamp": "2026-12-04T05:12:09Z", "level": "WARNING", "message": "pin-failure school=GFA-001", "logger": "saferide.auth", "requestId": "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"}' \
    '{"timestamp": "2026-12-04T05:12:10Z", "level": "WARNING", "message": "pin-locked school=GFA-001", "logger": "saferide.auth", "requestId": "4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e"}'
aws logs test-metric-filter --region af-south-1 \
  --filter-pattern '{ ($.logger = "saferide.auth") && ($.message = "pin-locked school=*") }' \
  --log-event-messages '{"timestamp": "2026-12-04T05:12:09Z", "level": "WARNING", "message": "pin-locked school=GFA-001", "logger": "saferide.auth", "requestId": "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"}' \
    '{"timestamp": "2026-12-04T05:12:10Z", "level": "WARNING", "message": "pin-failure school=GFA-001", "logger": "saferide.auth", "requestId": "4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e"}'
aws logs test-metric-filter --region af-south-1 \
  --filter-pattern '{ ($.logger = "saferide.auth") && ($.message = "school-required") }' \
  --log-event-messages '{"timestamp": "2026-12-04T05:12:09Z", "level": "WARNING", "message": "school-required", "logger": "saferide.auth", "requestId": "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"}' \
    '{"timestamp": "2026-12-04T05:12:10Z", "level": "WARNING", "message": "pin-failure school=unknown", "logger": "saferide.auth", "requestId": "4e2d7a3b-9c5f-4a1b-8d3e-6f7a8b9c0d1e"}'
```

The `ApiClientErrorsBurst` change is an in-place update of the alarm (same
logical id; namespace, metric and dimensions change without replacement), so
the change-set gate lists it as a `Modify` with no `WARN`. The gate warns,
and lets through, a replaced alarm or metric filter (stateless types), and
prints removals in its table without stopping on them; Release 1b removes
nothing.

**Nightly retention.** EventBridge Scheduler (`RetentionSchedule`, in the
`OpsScheduleGroup` group) invokes the migrate function asynchronously at
01:30 Africa/Nairobi with `{"action": "retention"}`; the function's async
config retries nothing (a failed run alarms and the next night runs), and its
timeout is 600 s, of which the job leaves 30 s unused. It opens an
`ops_job_runs` row, then deletes in batches of 2,000 (50 for accounts), each
under a 2-second statement timeout and skipping locked rows: notification
feed rows older than 90 days, reset and verify tokens used or expired over a
day ago, sessions and pre-auth tokens expired over 7 days ago, PIN-failure
rows older than a day, GPS idempotency keys older than 7 days, and parent
accounts still unverified 30 days after sign-up that hold no student link,
membership or provider account and that no incident, audit entry, run or
other record names. Then it runs the position-trail purge school by school
(oldest backlog first; a school whose purge lock a Start Run holds is skipped
until the next night) until 30 seconds remain: a stop on that budget is
status `stopped`, which is success — the backlog carries over. Incidents and
the audit log are never touched. Every finished or stopped run prints one
embedded-metric line (`RetentionRunSucceeded` = 1, the counts beside it);
an error closes the row `failed` and raises. To run it by hand (the first
run after the 1b deploy, or after a fix), asynchronously as the schedule
does, then read the witness:

```bash
aws lambda invoke --region af-south-1 --function-name <MigrateFunctionName> \
  --invocation-type Event --cli-binary-format raw-in-base64-out \
  --payload '{"action": "retention"}' /dev/null
./scripts/verify-db.sh hardening   # last-retention-run: status ok or stopped, counts
```

**us-east-1 (frontend stack).** Route 53 health-check, CloudFront and
CloudFront-certificate metrics exist only in us-east-1, so the frontend stack
has its own topic, `saferide-ops-us-east-1` (output `OpsTopicUsEast1Arn`),
with the same two addresses: export `ALERT_EMAIL_PRIMARY` /
`ALERT_EMAIL_SECONDARY` on a `deploy-frontend.sh` run too, so the two topics
make four confirmations. Later deploys keep them (`aws cloudformation deploy`
reuses unpassed parameters, and the alert-address guard passes the held
primary address explicitly); changing one replaces the us-east-1
subscription (a fresh confirmation) and the budget below. Two Route 53 health
checks (outputs `ApiHealthCheckId`, `SiteHealthCheckId`) probe over HTTPS
every 30 seconds from us-east-1, eu-west-1 and ap-southeast-1 and count any
2xx or 3xx as healthy. In December nothing calls the API, so only the probes
can report a dead stack: their alarms treat missing data as breaching.

| Alarm | Means | First response |
|---|---|---|
| `ApiProbeFailing` | no healthy answer from `https://api…/api/health` for 3 minutes: the API function is not answering anyone, or (since Release 1b) the database is not answering it. The health check runs a one-second `select 1` and answers 503 with `"db": "error"` when that fails; before 1b it did not touch the database | curl the health endpoint: `"db": "error"` points at the database (RDS events and metrics), no answer at the function; read the af-south-1 alarms and `/saferide/api` (`health-db-error` lines name the error class); roll back if a deploy just landed |
| `SiteProbeFailing` | the site's home page has not loaded for 3 minutes | open the site; check the distribution and its S3 bucket; redeploy the frontend from the last release tag if a deploy just landed |
| `SiteServerErrors` | over 5% of CloudFront responses were 5xx for 10 minutes | read the distribution's error graphs and the AWS Health Dashboard for CloudFront and S3 |
| `SiteCertificateExpiry` | the CloudFront certificate expires in under 20 days (custom domain only) | check the apex and www validation CNAMEs in Route 53 and the renewal status in ACM (us-east-1) |

The monthly cost budget (`MonthlyBudgetUsd`, Default 100 USD before tax)
counts SafeRide's regions only: `af-south-1`, `us-east-1` and `global`
(Route 53). The account runs other projects in other regions, and tax has no
region, so neither is counted. SafeRide ran about 67 USD a month in October
and November 2026. The budget mails the same addresses directly when actual
spend passes 80% and when the month's forecast passes 100%; no confirmation is needed, and it
exists only when at least one address is set. `MONTHLY_BUDGET_USD` overrides
the amount for one frontend deploy (live parity: repeat it, or change the
Default in a release). CloudFormation generates the budget's name (D23): a
changed alert address replaces the budget, and the generated name lets the
new one be created before the old one is deleted.

**Logs and retention by hand (operator step, once after the Release 1a
deploy).** The functions log JSON to `/saferide/api`, `/saferide/migrate` and
`/saferide/verify` (90 days, retained), application and platform lines both
at INFO: the platform's START, END and REPORT lines carry the duration, init
and timeout diagnostics the alarm descriptions point to. The app pins the
`httpx` and `httpcore` loggers to WARNING at import (D20), so the geocoder's
request URLs — the server Maps key and searched home addresses — never reach
these groups. There is no PostgreSQL log export (D21): error DETAIL lines can
carry email addresses and the app-role password. The functions' older
`/aws/lambda/*` groups never expire until set; `<…FunctionName>` are the
stack outputs of those names:

```bash
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<ApiFunctionName>
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<MigrateFunctionName>
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<VerifyFunctionName>
```

## Security headers

CloudFront adds a response-headers policy (output `ResponseHeadersPolicyId`)
to every response of the site: HSTS for one year **including subdomains**
(`api.`, `mail.`) and **not preloaded**, `nosniff`, `X-Frame-Options: DENY`,
`strict-origin-when-cross-origin`, `Permissions-Policy: geolocation=(self),
camera=(), microphone=()`, and the content-security policy written in
`frontend/template.yaml` (self, the Maps and Fonts hosts, the API origin,
inline styles, no framing).

- **`CspMode`** picks the CSP header. `report-only` (the default, Release 1a)
  sends `Content-Security-Policy-Report-Only`: violations appear in the browser
  console and nothing is blocked. `enforce` sends `Content-Security-Policy`;
  it is switched on in Release 2 after the Android Chrome and iOS Safari
  standalone checks against the production Map ID, with the policy widened
  only by what the report-only weeks showed.
- **Switch back:** `export CSP_MODE=report-only` (or leave it unset) and run
  `./scripts/deploy-frontend.sh` again. The script passes `CspMode` on every
  deploy, so the header is always the mode asked for, never a leftover.
  CloudFront takes a few minutes to propagate a policy change.
- **Maps values:** before building, `deploy-frontend.sh` reads
  `/saferide/google-maps-browser-key` and `/saferide/google-maps-map-id` from
  af-south-1 with `ssm_required` and aborts naming whichever is missing. The
  values are never printed. Vite gives variables already in the environment
  priority over `.env` files, so `frontend/.env.local` (the development key,
  and the demo Map ID by default) never reaches a production build.

## Release checklists

The operator checklist before Release 1a (SES production access, the Lambda
quota, the `release/0` tag, the secret copy to af-south-1, the production
Maps key and Map ID, the Route 53 preflight, the alert addresses, the deploy
day and window) and the Release 1a deploy checklist (the gates before the
deploy — the 017 rehearsal on a production copy, the gate dry run, the
fixed-name pre-checks, the baselines — what to watch in both deploys, the
verification after it and the first 48 hours) and the Release 1b deploy
checklist (the mail decision and baselines, the metric-filter tests, the
expected change sets, the first retention run, the mail, PIN and parent
drills, the rollback rehearsal to `release/1a`, the PIN re-issue, the alarm
drill and the next morning) are in
[docs/runbooks/hardening-release-checklists.md](../docs/runbooks/hardening-release-checklists.md).

## Restore and rollback

The restore and rollback runbook is [RESTORE.md](RESTORE.md): the decision
between a code rollback, a data restore and a pepper rotation; code rollback
by release tag (what rolling back Release 1b to `release/1a` means for
salted PINs, verification tokens and unverified sign-ups; what rolling back
to `release/0` takes away, the checks immediately before and after it); point-in-time restore into a scratch
instance checked through the verify function; the rename swap that replaces
production; the rehearsal, with 017 applied to the production copy; retained
resources after a failed or reverted Release 1a; pepper rotation; who may run
each step and what to record.

## Database checks (`verify-db.sh`)

`./scripts/verify-db.sh [--strict] <set> [parameter]` runs a named read-only
check set through the verify function (the database has no public endpoint)
and prints its answer. The parameter is a school uuid for the school sets and
the restore target, with a UTC offset, for `restore-compare`; it is sent
as both `school_id` and `timestamp`, and the set reads the one it binds.
Before the invoke it runs the verify-target check ("Deploy safety"): it
refuses, with a non-zero exit, while the verify function points at another
database than the stack's `DbEndpoint`, unless
`ALLOW_VERIFY_TARGET_MISMATCH=1`. The invoke
passes `--cli-read-timeout 0`: the CLI's default 60-second read timeout
equals the verify function's `Timeout`, so a slow set would otherwise be
cut off by the CLI and read as a failure. The exit status comes after the
answer (D24):

| Exit | Means |
|---|---|
| 0 | the verify function answered status `ok` |
| 1 | no verify function on the stack, or it failed (`FunctionError`) |
| 2 | usage |
| 3 | the answer's status is not `ok` (unknown set, missing or invalid parameter), or the answer is not JSON |
| 4 | `--strict` only: status `ok`, but at least one check returned an `error` row instead of rows (the labels are named on stderr) |
| 5 | the verify-target check refused: the verify function points at another database than the stack's `DbEndpoint` (overridden by `ALLOW_VERIFY_TARGET_MISMATCH=1`) |
| other | the `aws` call itself failed: the CLI's own exit code (for example 255) |

**In a checklist gate, any non-zero exit is a No-Go.** `--strict` goes
first. An `error` row is an observation, not always a fault — on a database
before 017, `hardening`'s 017 rows come back as errors, which is how "017 is
not applied" reads — so the checklists say where `--strict` applies. The expected values live in the
[release checklists](../docs/runbooks/hardening-release-checklists.md), the
comments of each set in `backend/app/verify_handler.py` and the plans'
Operational Notes; the script reports, the operator judges.

On production `verify-db.sh migrations` also lists two cosmetic marker rows,
`015_tenancy_constraints_rls 2` and `015_tenancy_constraints_rls 3`
(2026-09-20): duplicate copies of the 015 file that were in the package at the
multi-tenant Release 5 deploy, before the untracked-file guard existed, and
were applied by the migrate Lambda. 015 is idempotent, so the schema is
unaffected; the rows are expected and stay.

## Tenancy runtime role and scope header

- **The API runs as `saferide_app`**, a `LOGIN NOBYPASSRLS` role that is not
  the database owner, so migration 015's row-level-security policies actually
  bind every query. The migrate Lambda creates/refreshes the role and its
  grants on every deploy (`DB_APP_PASSWORD` from `/saferide/db-app-password`);
  migrations, seeds and the verify Lambda stay on the master role. The local
  stack mirrors the split (`docker-compose.local.yml` overrides the API
  container's `DATABASE_URL` onto `saferide_app`). The production flip of the
  API function's `DATABASE_URL` onto the runtime role is the Release 5 step —
  rolling back past it means pointing it at the master user again.
- **`SCOPE_HEADER_REQUIRED`** (API env, default `false`): while `false`, a
  school-scoped request without an `X-School-Id` header falls back to deriving
  the school server-side — the compatibility window for pre-tenancy clients.
  Release 5 flips it to `true` so every school-scoped request must name its
  school; the current frontend always sends the header.

## Release order caveat

`deploy-backend.sh` deploys the **API code first and runs migrations after**,
so there is a brief window where new code runs against the previous schema —
and a failed migrate leaves the live API broken (the script aborts loudly;
fix and re-run immediately). Schema changes therefore always land **one
release ahead** of the code that needs them. The multi-tenant rollout is
staged as **five gated releases** (role wiring → additive schema → data move →
scoped code → constraints + RLS) with per-release go/no-go checks and
rollback lines — see the Operational
Notes of `docs/plans/2026-08-23-001-feat-multi-tenant-schools-plan.md`, and
use `./scripts/verify-db.sh` (check sets `tenancy-preflight`,
`tenancy-post-move`, `tenancy-rls`) as the read-only SQL door for the gates.

Before any release that rewrites data, take a manual snapshot and record its
id; restoring and rolling back follow [RESTORE.md](RESTORE.md) (point-in-time
restore and rename swap; the template's `DBSnapshotIdentifier` only as the
alternative).

## Early smoke test (before DNS / certs)

Both stacks deploy without a cert: the backend exposes its
`*.execute-api.af-south-1.amazonaws.com` URL and the frontend its
`*.cloudfront.net` URL, so the infra can be validated before the Truehost
nameserver change propagates.

## Teardown

The database has deletion protection on (Release 1a), so `delete-stack` fails
until it is turned off. Turn it off first:

```bash
DB_ID="$(aws cloudformation describe-stack-resource --region af-south-1 --stack-name saferide-backend \
  --logical-resource-id Database --query StackResourceDetail.PhysicalResourceId --output text)"
aws rds modify-db-instance --region af-south-1 --db-instance-identifier "$DB_ID" \
  --no-deletion-protection --apply-immediately
```

```bash
aws cloudformation delete-stack --region us-east-1   --stack-name saferide-frontend
aws cloudformation delete-stack --region af-south-1  --stack-name saferide-backend   # RDS snapshots on delete
```

`delete-stack` leaves behind what the template retains: the database's final
snapshot (`DeletionPolicy: Snapshot`), the `/saferide/*` log groups and the
SES identity (`RetainExceptOnCreate`: deleted only when the update that
created them rolls back, kept on a stack delete) and the CloudTrail bucket
(`Retain`). Delete them by hand when the teardown is meant to be complete —
the commands are in [RESTORE.md](RESTORE.md), "Retained resources after a
failed or reverted Release 1a"; the bucket with
`aws s3 rb s3://<TrailBucketName> --force`. The `/saferide/*` SSM parameters,
in af-south-1 and any default-region copies, are outside both stacks.
