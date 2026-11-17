# Validation record — production hardening Release 1a (schema and platform)

- **Date:** 2026-11-25 (planned deploy day; record drafted 2026-11-17) ·
  **Release commit:** to be tagged `release/1a` at the `feat/hardening-r1a`
  tip (cut from `main` at `a59756d`, with the requirements and plan in
  `e20f3d5`) · **Plan:**
  `docs/plans/2026-11-16-001-feat-production-hardening-plan.md`, U1–U6, with
  U16 moved into 1a (D8), as amended by the execution decisions D1–D19.
- **Surface:** migration 017 (additive account-hardening schema) and the
  read-only `hardening` and `restore-compare` verify sets; deploy fail-fast on
  a missing secret in af-south-1, clean-checkout rule, change-set gate,
  production seed gate and version stamp; the backend stack's database
  hardening, JSON logs in retained 90-day groups, passive alarms to two
  operators, SES identity and DNS, CloudTrail; the frontend stack's security
  headers (CSP report-only), Route 53 probes, CloudFront and certificate
  alarms and the cost budget; the production Map ID and browser key from the
  parameter store; every live-parity parameter passed explicitly on each
  deploy; the SSM copy helper; the operator checklist, Release 1a deploy
  checklist and restore and rollback runbook in `infra/README.md`. No
  user-facing behaviour changes: nothing reads 017's objects until
  Release 1b.
- **Not deployed.** Local evidence only. Every operator and production row
  below is open; fill them in at the deploy.

## Commits

`git log --oneline e20f3d5..HEAD`, plus this record.

| SHA | Unit | Summary |
|---|---|---|
| `635c7db` | U2 | Migration 017 (D1–D5): `email_verification_required` (default false, no backfill) and `email_verified_at`, consent columns, token `purpose`, `saferide_new_driver_code()` and `live_schools.driver_code` not null unique defaulted, `pin_login_failures`, `ops_job_runs`, notifications index, audit CHECK widened from 016's list by four actions; verify registry parameter kinds (D14) with the `hardening` (18 entries) and `restore-compare` sets |
| `d2f69b8` | U3 | `ssm_required` reads the five secrets only from af-south-1 and aborts on a miss; `require_clean_checkout` (main or a `release/*` tag, `ALLOW_UNRELEASED_DEPLOY=1` hatch); untracked-file guard over `backend/app` and `backend/db` (D17); change-set gate (D7); `AppVersion`; seeds applied only when `APP_ENV=local`; migrate invoke with `--cli-read-timeout 0` |
| `00e7eac` | U3 fix | `provider-bootstrap.sh` reads and writes the TOTP pepper in af-south-1 and refuses to mint one while a default-region copy exists |
| `9fcc9d5` | U4 | Database property set (D18), three retained JSON log groups, pin-login and school-lookup route throttles (D6), `saferide-ops` topic with conditional subscriptions (D19) and twelve count-based alarms (D9, D16), SES identity, configuration set and mail records with DMARC `p=none` (D11), retained CloudTrail bucket and trail (D10), mail settings under live parity |
| `2bafe24` | U5, U16 | CloudFront response-headers policy with `CspMode` (report-only default), us-east-1 topic (D12), two Route 53 probes, site 5xx and certificate alarms, 60 USD budget; `deploy-frontend.sh` builds with the Maps browser key and Map ID read from af-south-1 |
| `0d953eb` | U3/U4 fixes, U1 tool | `parity-params.sh`: every parity parameter with a template Default passed on every deploy (the SAM `UsePreviousValue` finding below); `ConcurrencyAlarmThreshold` mapped; `copy-ssm-to-backend-region.sh` with its fake-`aws` test |
| this record | U1, U6 docs | `infra/README.md`: operator checklist before Release 1a, Release 1a deploy checklist, restore and rollback runbook (replacing the snapshot sketch), live-parity rule, SSM, alarms and teardown notes; this record |

## Go/No-Go gates (plan §Operational Notes, Release 1a checklist)

| Gate | Observed | Verdict |
|---|---|---|
| Migration 017 applies and double-applies (psql and the Lambda's single-transaction path) | Recorded in `635c7db`: `reset-local-db.sh` clean with 017; psql and Lambda-path re-applies clean; `test_migration_017`, 017 double-apply in `test_tenancy_schema` | **met locally** |
| `hardening` and `restore-compare` sets run on a local database | Integration `test_verify_handler` (`hardening` clean at 017; `restore-compare` refuses a malformed timestamp and returns one row per table) and unit `test_verify_registry`, in the run recorded in `635c7db` (integration 560 passed, 2 skipped) | **met locally** |
| Seeds skipped outside local | Recorded in `d2f69b8` (migrate-handler unit tests, `APP_ENV=production` reports the skipped files) | **met locally** |
| Deploy shell checks by hand (one parameter removed from af-south-1 aborts before `sam build` naming it; a modified tracked file aborts; a non-main HEAD without a release tag aborts) | Plan U3 asks for these before 1a; no hand run is recorded yet | **open** |
| Every parity parameter reaches the stack on each deploy | `test_deploy_params.py` 25 passed under `/bin/bash` 3.2.57 at `0d953eb` | **met locally** |
| SSM copy helper never writes in report mode, never overwrites, never exposes a value | `test_copy_ssm_script.py` 25 passed against a fake `aws` at `0d953eb` | **met locally** |
| Templates lint clean | `sam validate --lint` (backend) at `0d953eb`: only the pre-existing W1011; frontend template cfn-lint and `validate-template` recorded in `2bafe24` | **met locally** |
| `scripts/certify.sh` green at the release commit | pending | **open** |
| U1.1 SES production access requested (case id); both operator addresses verified | | **open** |
| U1.2 Lambda concurrency quota (L-B99A9384) at least 100, or the request id | | **open** |
| U1.3 `release/0` on `a59756d`, pushed | | **open** |
| U1.4 five `values MATCH` lines from `copy-ssm-to-backend-region.sh` | | **open** |
| U1.5 production Map ID and restricted browser key stored in af-south-1 | | **open** |
| U1.6 Route 53 preflight clean on the deploy day | clean on 2026-11-17 (D11); re-check due on the day | **open** |
| U1.7 alert addresses confirmed and exported for both deploys | | **open** |
| U1.8 no-run day agreed; `open-runs` 0 at the deploy (`tenancy-preflight` before 1a) | | **open** |
| Scratch point-in-time restore and the 1a property set as a dry run, with timings | | **open** |
| Live parity values read before the first explicit-parameter deploy | | **open** |
| Manual pre-1a snapshot id recorded | | **open** |
| `release/1a` tagged at the certified commit; HEAD on it | | **open** |
| Change set: no `MasterUserPassword`, no replacement, no unexplained removal | | **open** |
| Migrate result: `017_account_hardening` applied; seeds skipped by name; `app_role.synced` | | **open** |
| Frontend deployed with `CSP_MODE=report-only` | | **open** |
| `verify-db.sh migrations` lists 017; `hardening`: `017-objects-present` all true, `schools-without-driver-code` 0 | | **open** |
| Headers on apex, www, a deep route and `/sw.js` (report-only CSP, no enforcing CSP) | | **open** |
| RDS properties as set, `PendingModifiedValues` empty | | **open** |
| Alarms `OK`/`INSUFFICIENT_DATA` in both regions; four subscriptions confirmed | | **open** |
| Alarm drill: one alarm per topic reaches both inboxes within five minutes, OK follows | | **open** |
| SES DKIM and MAIL FROM `SUCCESS` (checked at +24 h and +72 h) | | **open** |
| CloudTrail logging, objects in the bucket | | **open** |
| Driver PIN, provider TOTP and staff password sign-ins after the deploy | | **open** |
| Phone checks (Android Chrome, Samsung Internet, iOS Safari standalone): sign-in, Track, Fleet Map, planner autocomplete, push enable, driver Start Run; every CSP console line copied | | **open** |
| 90-day retention set on the old `/aws/lambda/*` groups and the RDS export group | | **open** |
| Restore rehearsal with `restore-compare` against both databases, after 1a and before 1b (R21) | | **open** |

## Decisions taken during execution

From the execution decisions (2026-11-17), all for Release 1a:

- **D1** Verification is a flag, not a backfill: `email_verification_required`
  (default false) and `email_verified_at`; accounts made by old code are
  grandfathered by the default, so no migration 018.
- **D2** `live_schools.driver_code`: eight Crockford base32 characters from
  `gen_random_uuid()` (40 bits, no modulo bias), stored uppercase, matched
  after normalisation; the column default covers every creation path.
- **D3** `pin_login_failures` records the source, so 1b can key the lock on
  (school, source) and alarm on the per-school total. **D4** `ops_job_runs`
  is the retention witness.
- **D5** The audit CHECK is widened from the current (016) list plus four
  actions.
- **D6** Sign-in and school-lookup routes throttled at 20 rps, burst 40, as
  template parameters with deploy overrides.
- **D7** Change-set gate on every backend deploy: abort on a
  `MasterUserPassword` change or any replacement.
- **D8** U16 (production Map ID and browser key) ships in 1a, so the
  report-only weeks observe the policy against it.
- **D9** The migrate function's errors alarm ships in 1a.
- **D10** CloudTrail bucket: CloudFormation-named, retained, 90-day expiry,
  log-file validation, encryption, public access blocked.
- **D11** DMARC `p=none` without `rua`; the zone had no `_dmarc`, MX, SPF or
  `mail.` records on 2026-11-17.
- **D12** Two topics: af-south-1 for the backend, us-east-1 for probes,
  CloudFront, the certificate and the budget.
- **D13** Retained resources are imported or deleted before re-deploying
  after a rollback — now the runbook's "Retained resources" section.
- **D14** `restore-compare` binds a validated timestamp with an offset.
- **D15** Secrets move between regions by pipe and file only, matched by
  digest; the default-region copies are deleted one week after Release 2 is
  stable.
- **D16** The concurrency alarm threshold is a parameter (8 of 10).
- **D17** The untracked-file guard covers `backend/app` and `backend/db`.
- **D18** No Performance Insights (not offered on `db.t4g.micro`);
  Permissions-Policy keeps `geolocation=(self)`; driver Start Run joins the
  phone checks.
- **D19** Alert addresses are parameters with empty defaults; a subscription
  exists only for a set address.

Found and decided while finishing the unit (`0d953eb` and this record):

- **SAM `UsePreviousValue`.** SAM CLI 1.162.1 sends `UsePreviousValue` for
  every parameter a deploy does not pass, so a template Default change never
  reached the live stack and a one-off exported override stuck until another
  export — the opposite of the live-parity rule the GPS and mail tests
  assume. Fix: `build_parity_params` passes every map parameter with a
  non-empty Default on every deploy (the export, else the Default read from
  the template); empty-Default parameters stay pass-when-set, so a stale alert
  address or reply-to is replaced only by exporting a new one. Parameters
  outside the maps (`CorsOrigins`, `AppBaseUrl`, the `Db*` sizing and so on)
  still keep their previous value; documented. The first deploy with the fix
  resets any value an earlier export left behind, hence the "live parity
  values read" gate.
- **`ConcurrencyAlarmThreshold` joins the alert map** (as
  `CONCURRENCY_ALARM_THRESHOLD`) instead of a documented manual override: the
  deploy script had no way to pass it, and the gate-less alternatives (a
  hand-run `sam deploy` or `update-stack`) bypass the change-set review.
- **The copy helper's source region** resolves like an un-regioned CLI call
  (`AWS_REGION`, `AWS_DEFAULT_REGION`, then `aws configure get region`),
  because that is what the pre-hardening deploy and `release/0` read; the
  brief named only the last. It refuses to run with the CLI's `cli_history`
  enabled, and cleans up on HUP and QUIT as well as INT and TERM (found by an
  independent review).
- **The pre-1a verify function has neither `hardening` nor
  `restore-compare`.** Before the deploy, the no-run check reads `open-runs`
  from `tenancy-preflight`, and the restore rehearsal compares with
  `migrations`, `gps` and `baseline`; the `restore-compare` comparison runs
  once after 1a and before 1b, which still meets R21.
- **The rename swap does not get its properties back from a deploy.**
  CloudFormation compares templates, not the live instance, so the
  properties a point-in-time restore does not carry are re-applied by hand
  and checked with drift detection.
- **A manual snapshot before the deploy** is in the checklist: a rollback to
  `release/0` would cut automated backups from 35 to 7 days.
- **Retained resources after a failed or reverted 1a:** the runbook's
  default is to delete the log groups (after copying what the incident
  needs) and the SES identity; importing them is described but unrehearsed.

## Test evidence

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q` (backend unit, from `backend/`, at `0d953eb`) | 578 passed, 560 skipped (integration self-skips without `RUN_INTEGRATION`) |
| `pytest tests/core/test_copy_ssm_script.py` | 25 passed — fake `aws` first on PATH; report, apply, mismatch, read errors in either region, lost put race, mixed statuses, region resolution, `cli_history`, `bash -x` trace, HUP/INT/QUIT/TERM during a put |
| `pytest tests/core/test_deploy_params.py` | 25 passed under `/bin/bash` 3.2.57 |
| Mutation checks (by hand, reverted) | removing the explicit Default, adding `--overwrite`, printing a read, dropping the cleanup trap, or dropping the HUP/QUIT traps each fails the tests |
| `bash -n` on every `infra/scripts/*.sh` | clean |
| `git diff --check` | clean |
| `sam validate --lint` (infra/backend) at `0d953eb` | only the pre-existing W1011 |
| `RUN_INTEGRATION=1 pytest tests/integration` | last recorded at `9fcc9d5`: 560 passed, 2 skipped; `backend/app` and `backend/db` unchanged since |
| Frontend (`tsc`, vitest, build) | last recorded at `2bafe24`: tsc clean, vitest 346 passed in 23 files, build passes |
| `scripts/certify.sh` at the release commit | pending |

## Rollback position

- **Code rollback to `release/0` (`a59756d`),** backend then frontend, with
  that tag's own scripts (runbook, "Code rollback"). Migration 017 stays: it
  is additive, every new column is nullable or defaulted, the audit CHECK is
  a superset, and `release/0`'s code never reads the new objects.
- **Preconditions:** the default-region secret copies exist and match
  (`copy-ssm-to-backend-region.sh` in the deploying shell), because
  `release/0`'s script reads them there and mints a missing one. After the
  D15 deletion `release/0` is no longer a rollback target.
- **What reverts with `release/0`'s templates:** the alarms, topics and
  subscriptions, the SES configuration set and mail records, the trail, the
  JSON logging, the headers policy, probes and budget; the database returns
  to 7-day backups with deletion protection off, so RDS drops automated
  backups older than 7 days (the manual pre-1a snapshot stays). The frontend
  build returns to `frontend/.env.local`'s development Maps key and demo Map
  ID.
- **Retained resources (D13):** the `/saferide/*` log groups and the SES
  identity have fixed names and must be deleted or imported before 1a is
  deployed again; the CloudFormation-named trail bucket is left behind.
- **Data:** nothing in 1a rewrites existing rows except filling the new
  `driver_code` column; a data problem follows the runbook's point-in-time
  restore and rename swap.

## Deployed

Not deployed. At the deploy, record here: the window (UTC and EAT), the
release commit and tag, the change-set table, the migrate result, the
post-deploy rows above with their outputs, the alarm-drill times, the
phone-check CSP lines, and the SES status at +24 h and +72 h.
