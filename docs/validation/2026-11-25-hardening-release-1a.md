# Validation record — production hardening Release 1a (schema and platform)

- **Date:** 2026-11-25 (planned deploy day; record drafted 2026-11-17) ·
  **Release commit:** to be tagged `release/1a` at the `feat/hardening-r1a`
  tip (cut from `main` at `a59756d`, with the requirements and plan in
  `e20f3d5`) · **Plan:**
  `docs/plans/2026-11-16-001-feat-production-hardening-plan.md`, U1–U6, with
  U16 moved into 1a (D8), as amended by the execution decisions D1–D27
  (D20–D24 from the code review below, D25 the holiday deploy window, D26
  the database stack policy, D27 the 017 rehearsal method).
- **Surface:** migration 017 (additive account-hardening schema) and the
  read-only `hardening` and `restore-compare` verify sets; deploy fail-fast on
  a missing secret in af-south-1, clean-checkout rule, open-run refusal,
  change-set gate (`infra/scripts/changeset_gate.py`) on both stacks,
  alert-address guard, verify-target check, production seed gate and
  version stamp; `verify-db.sh` exit codes and `--strict`; the backend
  stack's database hardening, JSON logs in retained 90-day groups (`httpx`
  and `httpcore` held at WARNING), passive alarms to two operators, SES
  identity and DNS, CloudTrail; the frontend stack's security headers (CSP
  report-only), Route 53 probes, CloudFront and certificate alarms and the
  cost budget; the production Map ID and browser key from the parameter
  store; every live-parity parameter passed explicitly on each deploy of
  either stack; the SSM copy helper; the operator checklist and the
  Release 1a deploy checklist in
  `docs/runbooks/hardening-release-checklists.md` and the restore and
  rollback runbook in `infra/RESTORE.md` (both moved out of
  `infra/README.md` in `ca92d4e`). No user-facing behaviour
  changes: nothing reads 017's objects until Release 1b.
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
| `ebdc123` | U1, U6 docs | `infra/README.md`: operator checklist before Release 1a, Release 1a deploy checklist, restore and rollback runbook (replacing the snapshot sketch), live-parity rule, SSM, alarms and teardown notes; the first draft of this record |
| `566b136` | U4 fix | The fixed-name log groups and SES identity move from `Retain` to `RetainExceptOnCreate`, so a rolled-back first 1a create leaves nothing to clash on the retry; the trail bucket stays plain `Retain` |
| `639954e` | U1 tool test | The copy helper's signal tests reset inherited signal dispositions in the child, so they pass under `nohup` and as a background job |
| `54ca7ba` | review A1 | `httpx`/`httpcore` pinned to WARNING at app import (D20) with `test_logging_hygiene`; import-time registry check dropped from the verify handler; 017 reordered so `live_schools` is locked last; 017 rehearsed over a populated pre-017 shape through the Lambda path |
| `a12bc01` | review A2 | No RDS log export (D21); maintenance `sun:00:00-sun:01:00` (D22); `SystemLogLevel` INFO; generated budget name (D23); `ApiProbeFailing` wording; shared `template_text.py` helper; per-alarm property table, topic-policy and trail tests |
| `a305a50` | review B.5, B.6 | `FRONTEND_PARAMS` and one `PARITY_PAIRS` list, `deploy-frontend.sh` passes every frontend parameter with a Default (incl. `MonthlyBudgetUsd`); `verify-db.sh` exits 3 on a status other than `ok` and 4 under `--strict` on an error row (D24); fake-CLI tests |
| `104985d` | review B.1–B.4, B.7, B.8 | Change-set gate as `changeset_gate.py` with the stateless-replacement rule and `ALLOW_REPLACE`; explicit `--stack-name`/`--region` on `sam deploy`; `provider-bootstrap` read with `ssm_required`; push seeding through a 0600 file; `provider-bootstrap.sh` refuses to mint on a read error; open-run refusal with `ALLOW_OPEN_RUNS`; behavioural deploy-guard tests |
| `a16327e` | review B | `ALLOW_REPLACE` never covers an `AWS::RDS::DBInstance`; the superseded frontend parameter text test removed |
| `ca92d4e` | review D, U1, U6 docs | Checklists moved to `docs/runbooks/hardening-release-checklists.md` and the runbook to `infra/RESTORE.md`, with pointers left in `infra/README.md` and every reference re-pointed; the README, checklists and runbook describe the review fixes; this record brought up to date |
| `f41c5f3` | review E-code | E1 alert-address guard (`ALLOW_NO_ALERTS`), E2 open-run re-check before execution, E3 frontend change-set gate (Distribution and Bucket never replaceable), E4 `verify-db.sh --cli-read-timeout 0`, E5 verify-target check (`ALLOW_VERIFY_TARGET_MISMATCH`, `verify-db.sh` exit 5); change-set helpers shared in `config.sh`; fake-CLI tests |
| this record | review E-docs | The deployment-verification review's corrections and the adversarial residuals in the checklists, `infra/RESTORE.md` and `infra/README.md`; E1–E5 documented; the gate dry run and the 017 production-copy rehearsal added as open gates |

## Go/No-Go gates (plan §Operational Notes, Release 1a checklist)

| Gate | Observed | Verdict |
|---|---|---|
| Migration 017 applies and double-applies (psql and the Lambda's single-transaction path) | Recorded in `635c7db`: `reset-local-db.sh` clean with 017; psql and Lambda-path re-applies clean; `test_migration_017`, 017 double-apply in `test_tenancy_schema` | **met locally** |
| `hardening` and `restore-compare` sets run on a local database | Integration `test_verify_handler` (`hardening` clean at 017; `restore-compare` refuses a malformed timestamp and returns one row per table) and unit `test_verify_registry`, in the run recorded in `635c7db` (integration 560 passed, 2 skipped) | **met locally** |
| Seeds skipped outside local | Recorded in `d2f69b8` (migrate-handler unit tests, `APP_ENV=production` reports the skipped files) | **met locally** |
| 017 over a populated pre-017 shape, through the Lambda's single-transaction path | `test_migration_017` (rolled back): distinct Crockford codes for existing schools, NOT NULL/default/unique restored, the existing parent reads (False, NULL), a second apply changes nothing; integration 561 passed, 2 skipped at `54ca7ba` | **met locally** |
| Deploy shell checks by hand (one parameter removed from af-south-1 aborts before `sam build` naming it; a modified tracked file aborts; a non-main HEAD without a release tag aborts) | Behaviour covered against fake `aws`/`git` by `test_deploy_guards.py` (37) at `104985d`; plan U3's hand run against the real account is not recorded yet | **open** |
| Change-set gate: master-password change and non-stateless replacement abort; stateless replacement, `Conditional` and `ALLOW_REPLACE` warn; `ALLOW_REPLACE` never covers the database; an aborted or declined change set is deleted, never executed | `test_changeset_gate.py` 49 passed (table-driven payloads plus `deploy-backend.sh` end to end against fake `aws`/`sam`/`git`) at `a16327e`; 80 at `f41c5f3` with the frontend gate and the pre-execute re-check | **met locally** |
| Open-run refusal before `sam build`; `ALLOW_OPEN_RUNS=1` overrides | Same file: `open-runs` 1 refuses, 0 proceeds, the override proceeds, and every way the count can be unreadable refuses | **met locally** |
| `verify-db.sh` exit codes 0/1/2/3/4/5 and `--strict` (D24) | `test_verify_db_script.py` 15 passed against a fake `aws` at `a305a50`; 23 at `f41c5f3` (exit 5, the CLI's own exit code, the read timeout) | **met locally** |
| Batch E-code: alert-address guard (`ALLOW_NO_ALERTS`), open-run re-check before execution, frontend change-set gate (Distribution and Bucket never replaceable), `verify-db.sh` read timeout and CLI-failure exit, verify-target check (`ALLOW_VERIFY_TARGET_MISMATCH`) | At `f41c5f3` against fake `aws`/`sam`/`git`: `test_changeset_gate.py` 80, `test_deploy_params.py` 43, `test_verify_db_script.py` 23, `test_deploy_guards.py` 37 passed; backend unit suite 756 passed, 561 skipped (integration tests skip without `RUN_INTEGRATION`) | **met locally** |
| Maps key and searched addresses kept out of INFO logs (D20) | `test_logging_hygiene.py` 6 passed (fresh interpreter per entry path, with a control showing bare `httpx` logs the key) at `54ca7ba` | **met locally** |
| Every parity parameter reaches the stack on each deploy, both stacks | `test_deploy_params.py` 32 passed (backend and frontend templates, `deploy-frontend.sh` end to end) at `a305a50`; first recorded as 25 at `0d953eb`; 43 at `f41c5f3` | **met locally** |
| SSM copy helper never writes in report mode, never overwrites, never exposes a value | `test_copy_ssm_script.py` 25 passed against a fake `aws` at `0d953eb`, again at `639954e` under `nohup` | **met locally** |
| Templates lint clean | `sam validate --lint` (backend) at `a12bc01`: only the pre-existing W1011; frontend `validate-template` and SAM lint clean at `a12bc01` | **met locally** |
| `scripts/certify.sh` green at the release commit | Green at `156ace9` on 2026-11-18: backend unit 756 passed, 561 skipped; API integration 561 passed, 2 skipped; typecheck clean; vitest 346 passed in 23 files; build passes; Playwright 102 passed. The one later code commit, `c7b0271`, changes only two test stubs; `tests/core` 514 passed there. Re-run at the commit that is tagged `release/1a` | **met locally** |
| U1.1 SES production access requested (case id); both operator addresses verified | Requested 2026-11-18 14:31 UTC (`put-account-details`, af-south-1, mail type TRANSACTIONAL, one additional contact): `ReviewDetails.Status` `PENDING`, case id not yet assigned. Later that day AWS's automated first review asked for more detail (URL, mail type, volume, recipient source, bounce and complaint handling, a sample); the account then read `DENIED`, case `179501229800147`; the answer goes in that case. Account-level suppression list on for `BOUNCE` and `COMPLAINT`. One operator address verified (`SUCCESS`); no second operator chosen yet (`ALERT_EMAIL_SECONDARY` is optional). Record the case id and the outcome | **open** |
| U1.2 Lambda concurrency quota (L-B99A9384) at least 100, or the request id | Requested 2026-11-18 13:42 UTC for 1000: request `b7c5f73ce466470791de552c6e2831ffTzsJCgjU`, support case `179500939000289`, status `CASE_OPENED`; the limit is still 10. Record the granted value; once granted, export `CONCURRENCY_ALARM_THRESHOLD` (80% of it) on every backend deploy | **open** |
| U1.3 `release/0` on `a59756d`, pushed | 2026-11-18 on the user's go: annotated tag `f337b64` pushed; `git ls-remote --tags origin 'refs/tags/release/*'` shows `release/0` peeled to `a59756d7ac9379e068b4801968646dce515c6629` (Merge pull request #16, the head of `origin/main`) | **met** |
| U1.4 five `values MATCH` lines from `copy-ssm-to-backend-region.sh` | 2026-11-18 on the user's go. The five secrets were only in **eu-west-2** (this machine's CLI default is `eu-central-1`, which holds none), so the helper ran with `SOURCE_REGION=eu-west-2`. Report: all five `missing in af-south-1`, exit 1. `--apply`: five `Copied … (SecureString)`, exit 0. Proof run: header `source eu-west-2 -> target af-south-1 (report only, read-only)`, five `present in both, values MATCH`, `All 5 parameters MATCH.`, exit 0; no temporary directory left. Cross-check by fingerprint against the live stack: the af-south-1 `pin-pepper`, `totp-pepper` and `google-maps-api-key` equal the API function's environment (eu-west-2's `pin-pepper` is at version 2, and its current value is the live one), `db-app-password` equals the API function's `DATABASE_URL` password and the migrate function's `DB_APP_PASSWORD`, `db-password` equals the migrate function's `DATABASE_URL` password. The eu-west-2 copies stay until one week after Release 2 is stable | **met** |
| U1.5 production Map ID and restricted browser key stored in af-south-1 | 2026-11-18 14:47 UTC: `/saferide/google-maps-browser-key` and `/saferide/google-maps-map-id` stored as SecureString version 1 in af-south-1, read back: the key matches "Saferide production" (project `atomic-monument-499505-s4`; websites exactly `https://saferidelive.co.ke/*` and `https://www.saferidelive.co.ke/*`; API restriction only Maps JavaScript API, `maps-backend.googleapis.com` — the app loads only the `geometry` and `marker` libraries, address search and routing run server-side), the Map ID matches "Saferide" `72fcaa99aa3cbcc49ce6776f` (VECTOR). The unused, website-unrestricted "Maps Platform API Key" was deleted. Still in use: "SafeRide Web (browser)" (live site, `frontend/.env.local`) and "SafeRide Server (backend)" (live API, eu-west-2 SSM). The restriction itself is proved by the Release 1a phone checks | **met** |
| U1.6 Route 53 preflight clean on the deploy day | clean on 2026-11-17 (D11); re-check due on the day | **open** |
| U1.7 alert addresses confirmed and exported for both deploys; the four confirmations within 48 hours, proved after the deploy (+1 h and +47 h) | 2026-11-18, the user: one address for now, the operator's own (the SES-verified identity); `ALERT_EMAIL_SECONDARY` stays unset, so each deploy subscribes one address and **two** confirmations arrive (one per topic) instead of four. A second person can be added later with a fresh confirmation | **open** (address **met**; the confirmations come after the deploy) |
| U1.8 no-run day agreed; window 16:30–22:30 UTC Monday to Saturday, the instance's current windows read first; `open-runs` 0 (`tenancy-preflight`) and `trail-rows-per-run-today` empty (`gps`) with the schools' confirmation, at the deploy (the deploy's own open-run refusal reads `open-runs` before building and before executing) | Day agreed 2026-11-18 (D25): schools closed until 2027-01-04, any day works. Read 2026-11-18: `open-runs` 0; current windows backup `05:59-06:29` UTC daily, maintenance `tue:09:23-tue:09:53` UTC, `PendingModifiedValues` empty, retention 7. Re-read both, and `gps` `trail-rows-per-run-today`, on the deploy day | **open** (day **met**; the deploy-day reads remain) |
| Scratch point-in-time restore and the 1a property set as a dry run, with timings | 2026-11-18 on the user's go (infra/RESTORE.md A, the property dry run, B, C; E deferred so the 017 rehearsal can reuse the copy). Target `2026-11-18T15:26:38+00:00` (the latest restorable time; schools on holiday, so nothing ran). Decision 15:32:07Z → `saferide-restore-test` available 15:56:48Z (**24 min 41 s**) → 1a property set applied and read back 15:57:43Z (deletion protection on, retention 35, max storage 100, backup `23:00-23:30`, maintenance `sun:00:00-sun:01:00`, no log export, nothing pending; then deletion protection off) → verify function on the copy, first query answered 15:58:08Z: **restore to first query 26 min**, inside the one-hour target. Comparison, copy against production (production read after C, without the override): `migrations` (22 rows, up to `016_gps_tracking`, the two cosmetic 015 rows included), `gps` (9 checks) and `baseline` for Msingi Bora and Voluntari Academy (5 checks each): every check identical, no error rows | **met** |
| **017 production-copy rehearsal:** migrate function pointed at the scratch instance, `migrations.applied` exactly `["017_account_hardening"]`, duration recorded; environment read back `MATCH`, code hash back to its first value. Needs the user's go, and first an agreed way to put 1a's migrate package on the function (`infra/RESTORE.md`, "017 on the production copy") | 2026-11-18 on the user's go, by D27 (a throwaway stack with 1a's migrate and verify code against the scratch copy; no production function changed). Build at `aecab54`, no `.env` in the package. `migrations` on the copy before: 22 rows, last `016_gps_tracking`. Migrate #1 16:05:21Z → 16:05:26Z (**4.9 s**, timeout 120 s): `status` `ok`, `migrations.applied` exactly `["017_account_hardening"]`, seeds skipped by name (`APP_ENV=production`), `app_role.synced` true, provider bootstrap skipped (already applied). After: 23 rows, last `017_account_hardening`; `hardening` 18 checks, no error rows: `017-objects-present` 15 of 15 true, `audit-check-widening-017` all six true, `schools-without-driver-code` 0, `drivers-with-several-driver-memberships` 0, `open-runs-and-today` 0/0. Migrate #2: `applied` `[]` (017 skipped). Production migrate function `CodeSha256` `nQzJ18OK…PtE=` (`LastModified` 2026-09-22T17:52:46Z) identical before and after. Cleanup: rehearsal stack and its log group deleted; scratch instance deleted 16:08:06Z (skip final snapshot, automated backups deleted), none retained | **met** |
| Both functions back on production after the rehearsal: verify and migrate read-backs `MATCH`, both `DATABASE_URL` hosts equal `DbEndpoint` | Verify function: on the copy 15:57:43Z–15:58:08Z, put back by the script's always-run step C, read-back `MATCH`, and the production reads after it passed the verify-target check without the override. Migrate function: never pointed elsewhere (D27 used a temporary function); `CodeSha256` unchanged. Read after both rehearsals: each function's `DATABASE_URL` host equals `DbEndpoint` (`saferide-backend-database-rto9a7nrm9ux…`) | **met** |
| **Gate dry run** on the real stack (`CONFIRM_CHANGESET=1`, answered `N`, on the user's go): `Database` row without `MasterUserPassword`, change set deleted, stack status unchanged; the gate output pasted here | Run 2026-11-18 on the user's go ("Gate dry run, 2026-11-18" below): the `Database` row lists exactly the five 1a properties and **no `MasterUserPassword`**; change set deleted, none left; stack `UPDATE_COMPLETE`, `LastUpdatedTime` 2026-09-22T17:52:33Z before and after. One `WARN`: `Database` replacement `Conditional`, from `BackupRetentionPeriod` and `PreferredMaintenanceWindow` (the only two of the five in the resource schema's `conditionalCreateOnlyProperties`) | **met** — `MasterUserPassword` absent; the `WARN` closed by D26: rehearsed in place on a throwaway stack, stack policy denying `Database` replacement set on `saferide-backend` |
| Fixed-name pre-checks: `saferide-ops`, `saferide-ops-us-east-1`, `saferide-trail`, `saferide-transactional`, the `saferidelive.co.ke` identity, `/saferide/` log groups, `saferide-frontend-security-headers` — all NotFound or empty | | **open** |
| Baselines before the deploy: `--strict migrations` (seed markers, the two cosmetic 015 rows, no 017), `tenancy-preflight`, database properties (retention 7, no deletion protection, no max storage) | | **open** |
| Stray-variable check prints only the intended exports; no `ALLOW_*` | | **open** |
| Live parity values read before the first explicit-parameter deploy | | **open** |
| Manual pre-1a snapshot id recorded | | **open** |
| `release/1a` tagged at the certified commit and pushed on the user's go; HEAD on it | | **open** |
| Change set: no `MasterUserPassword`, no replacement and no `WARN` line, no unexplained removal, the same table as the dry run; override lines reconciled (only the addresses and a granted threshold); open-run re-check passed before execution | | **open** |
| Migrate result: `017_account_hardening` applied; seeds skipped by name; `app_role.synced` | | **open** |
| Frontend change set: `Distribution` and the four alias records replacement `False`, no `WARN`; deployed with `CSP_MODE=report-only` | | **open** |
| `verify-db.sh --strict migrations` exits 0 and, against the baseline, adds only 017 (the seed markers and the two cosmetic `015_tenancy_constraints_rls 2`/`3` rows remain); `tenancy-preflight` `school-rows`, `users-by-role`, `audit-by-school` identical; `--strict hardening` exits 0: `017-objects-present` all true, `schools-without-driver-code` 0; `instance-settings` and the 1b baselines recorded | | **open** |
| Headers on apex, www, a deep route and `/sw.js` (report-only CSP, no enforcing CSP) | | **open** |
| RDS properties as set (maintenance `sun:00:00-sun:01:00`, no log export), `PendingModifiedValues` empty | | **open** |
| Alarms `OK`/`INSUFFICIENT_DATA` in both regions (the probe alarms re-read after 10 minutes); `AccountConcurrency` threshold 800 or 8; four subscriptions confirmed | | **open** |
| Alarm drill: one alarm per topic reaches both inboxes within five minutes, OK follows | | **open** |
| SES DKIM and MAIL FROM `SUCCESS` (checked at +24 h and +72 h) | | **open** |
| CloudTrail logging, objects in the bucket | | **open** |
| Driver PIN, provider TOTP and staff password sign-ins after the deploy | | **open** |
| Phone checks (Android Chrome, Samsung Internet, iOS Safari standalone): sign-in, Track, Fleet Map, planner autocomplete, push enable, driver Start Run; every CSP console line copied | | **open** |
| 90-day retention set on the three old `/aws/lambda/*` groups | | **open** |
| First 48 hours: subscriptions at +1 h and +47 h; `DatabaseConnections` against `max_connections`; ERROR searches at +1/+4/+24/+48 h; first automated backup, events, nothing pending; trail delivery error empty; blank-map reports; daily spend | | **open** |
| Restore rehearsal with `restore-compare` against both databases, after 1a and before 1b (R21) | | **open** |


### Gate dry run, 2026-11-18

On the user's go, from `feat/hardening-r1a` at `07856c5` with
`ALLOW_UNRELEASED_DEPLOY=1` (the checklist runs it from the `release/1a` tag,
which is not cut yet), `ALERT_EMAIL_PRIMARY` set to the one verified operator
address, no other override exported, `open-runs` 0; `printf 'N\n'` answered the
prompt. The build used the cached `sam/build-python3.12` arm64 image. The log
(kept 0600 outside the repository) was scanned for every `/saferide/*` secret
value: none present; SAM printed the `NoEcho` parameters as `*****`. The
gate's output:

```text
  Action  LogicalResourceId                  ResourceType                                Replacement  Changed
  Add     AccountConcurrency                 AWS::CloudWatch::Alarm                      -            -
  Add     ApiCertificateExpiry               AWS::CloudWatch::Alarm                      -            -
  Add     ApiClientErrorsBurst               AWS::CloudWatch::Alarm                      -            -
  Add     ApiFunctionErrors                  AWS::CloudWatch::Alarm                      -            -
  Add     ApiFunctionPinLoginPermission      AWS::Lambda::Permission                     -            -
  Modify  ApiFunctionRole                    AWS::IAM::Role                              False        Policies
  Add     ApiFunctionSchoolLookupPermission  AWS::Lambda::Permission                     -            -
  Add     ApiFunctionThrottles               AWS::CloudWatch::Alarm                      -            -
  Modify  ApiFunction                        AWS::Lambda::Function                       False        Environment, Code, LoggingConfig, Role
  Add     ApiLatencyP95                      AWS::CloudWatch::Alarm                      -            -
  Add     ApiLogGroup                        AWS::Logs::LogGroup                         -            -
  Add     ApiServerErrors                    AWS::CloudWatch::Alarm                      -            -
  Add     AuditTrail                         AWS::CloudTrail::Trail                      -            -
  Modify  Database                           AWS::RDS::DBInstance                        Conditional  DeletionProtection, BackupRetentionPeriod, MaxAllocatedStorage, PreferredBackupWindow, PreferredMaintenanceWindow
  Add     DbCpuCreditsLow                    AWS::CloudWatch::Alarm                      -            -
  Add     DbCpuHigh                          AWS::CloudWatch::Alarm                      -            -
  Add     DbFreeStorageLow                   AWS::CloudWatch::Alarm                      -            -
  Add     DbFreeableMemoryLow                AWS::CloudWatch::Alarm                      -            -
  Add     DmarcRecord                        AWS::Route53::RecordSet                     -            -
  Modify  HttpApiApiGatewayDefaultStage      AWS::ApiGatewayV2::Stage                    False        RouteSettings
  Modify  HttpApi                            AWS::ApiGatewayV2::Api                      False        Body
  Add     MigrateFunctionErrors              AWS::CloudWatch::Alarm                      -            -
  Modify  MigrateFunction                    AWS::Lambda::Function                       False        Environment, Code, LoggingConfig
  Add     MigrateLogGroup                    AWS::Logs::LogGroup                         -            -
  Add     OpsSubscriptionPrimary             AWS::SNS::Subscription                      -            -
  Add     OpsTopicPolicy                     AWS::SNS::TopicPolicy                       -            -
  Add     OpsTopic                           AWS::SNS::Topic                             -            -
  Add     SesConfigSetEvents                 AWS::SES::ConfigurationSetEventDestination  -            -
  Add     SesConfigSet                       AWS::SES::ConfigurationSet                  -            -
  Add     SesDkimRecord1                     AWS::Route53::RecordSet                     -            -
  Add     SesDkimRecord2                     AWS::Route53::RecordSet                     -            -
  Add     SesDkimRecord3                     AWS::Route53::RecordSet                     -            -
  Add     SesIdentity                        AWS::SES::EmailIdentity                     -            -
  Add     SesMailFromMx                      AWS::Route53::RecordSet                     -            -
  Add     SesMailFromSpf                     AWS::Route53::RecordSet                     -            -
  Add     TrailBucketPolicy                  AWS::S3::BucketPolicy                       -            -
  Add     TrailBucket                        AWS::S3::Bucket                             -            -
  Modify  VerifyFunction                     AWS::Lambda::Function                       False        Environment, Code, LoggingConfig
  Add     VerifyLogGroup                     AWS::Logs::LogGroup                         -            -
WARN: Database (AWS::RDS::DBInstance) may be replaced (Replacement: Conditional)
Deploy cancelled by the operator.
    change set deleted; nothing was executed.
```

Why `Database` reads `Conditional`: of the five properties 1a changes, the
`AWS::RDS::DBInstance` resource schema (`describe-type`) lists
`BackupRetentionPeriod` and `PreferredMaintenanceWindow` in
`conditionalCreateOnlyProperties`; `DeletionProtection`,
`MaxAllocatedStorage` and `PreferredBackupWindow` are plain updates. The
schema's own text says a maintenance-window update through CloudFormation "is
applied immediately", and nothing in it makes a retention change from 7 to 35
replace a standalone instance; the conditional cases are instances restored
from a snapshot or acting as replicas, and this one is neither (no
`DBSnapshotIdentifier`, no `SourceDBInstanceIdentifier`). The flag is static:
it shows on every change set that touches either property, so it cannot be
removed by applying them out of band. Were CloudFormation to replace the
instance anyway, the template names no `DBInstanceIdentifier`, so it would
create a new, empty database and snapshot then delete the old one
(`UpdateReplacePolicy: Snapshot`); the live instance has no deletion
protection yet, and the stack had no stack policy.

**Closed (D26, the user's choice "rehearse and lock").** A throwaway stack
`saferide-rds-rehearsal-20261118` (af-south-1, 15:10–15:21 UTC) created a
`db.t4g.micro` PostgreSQL 16.14 instance in the live shape (retention 7, no
explicit windows, no `MaxAllocatedStorage`, deletion protection off; password
random, never printed), under the stack policy below, then applied a change
set with exactly 1a's five database properties. The same gate printed the
same row and `WARN`; the change set's details: `BackupRetentionPeriod` and
`PreferredMaintenanceWindow` `RequiresRecreation: Conditionally`, the other
three `Never`, all `Evaluation: Static`, `DirectModification`. Executed:
`Database` `UPDATE_IN_PROGRESS` 15:18:23Z → `UPDATE_COMPLETE` 15:18:56Z, stack
`UPDATE_COMPLETE`, no policy denial. The instance kept its `DbiResourceId`
and `InstanceCreateTime` (the same instance, not a replacement), status
`available`, `PendingModifiedValues` empty, and read retention 35, backup
`23:00-23:30`, maintenance `sun:00:00-sun:01:00`, `MaxAllocatedStorage` 100,
deletion protection on. Cleanup: deletion protection off, stack deleted,
instance gone, no snapshot and no retained automated backup left.

Then, on the same choice, the policy was set on `saferide-backend` and read
back (`get-stack-policy`); the stack stayed `UPDATE_COMPLETE`,
`LastUpdatedTime` 2026-09-22T17:52:33Z:

```json
{"Statement": [
  {"Effect": "Allow", "Action": "Update:*", "Principal": "*", "Resource": "*"},
  {"Effect": "Deny", "Action": ["Update:Replace", "Update:Delete"], "Principal": "*", "Resource": "LogicalResourceId/Database"}
]}
```


### Production copy after 017, 2026-11-18 (a preview of the 1b baselines)

From the `hardening` set on the restored copy (target 15:26:38Z) after 017;
the real baselines are read on production after the 1a deploy. Driver PIN
hashes: Msingi Bora 4 active driver memberships, Voluntari Academy 1, all
`legacy_hmac` (none `salted_v2`, none without a PIN). Parents: 5, all with
`consent_version` null; no unverified parent accounts (D1: existing parents
are not asked to verify). Tokens: `reset` 1 used and unpurged over a day,
TTL 60 minutes; no `verify` tokens. Stale sessions: `auth_sessions` 271
expired over 7 days, `auth_preauth_tokens` 18. Notifications older than 90
days: 1. Push subscriptions: none. `last-retention-run`,
`parent-reset-audit-recent` and `temporary-passwords-outstanding` empty (new
in 017). Instance: PostgreSQL 16.14, `max_connections` 79.

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
  `MasterUserPassword` change on the database or any replacement. After the
  review, a replacement of a stateless type (`AWS::SNS::Subscription`,
  `AWS::CloudWatch::Alarm`, `AWS::Logs::MetricFilter`) is a `WARN`, and
  `ALLOW_REPLACE=<LogicalId,...>` downgrades the named ids to a loud `WARN`
  for one deploy — never an `AWS::RDS::DBInstance`.
- **D8** U16 (production Map ID and browser key) ships in 1a, so the
  report-only weeks observe the policy against it.
- **D9** The migrate function's errors alarm ships in 1a.
- **D10** CloudTrail bucket: CloudFormation-named, retained, 90-day expiry,
  log-file validation, encryption, public access blocked.
- **D11** DMARC `p=none` without `rua`; the zone had no `_dmarc`, MX, SPF or
  `mail.` records on 2026-11-17.
- **D12** Two topics: af-south-1 for the backend, us-east-1 for probes,
  CloudFront, the certificate and the budget.
- **D13** The fixed-name resources (the three `/saferide/*` log groups, the
  SES identity) carry `RetainExceptOnCreate` (`566b136`): a failed 1a update
  deletes what it had just created — though Lambda can create a
  `/saferide/*` group again if a function runs after that deletion, so the
  log groups are checked and deleted before the retry (corrected after the
  deployment-verification re-run); a deliberate rollback to `release/0` or a
  stack delete leaves them behind, to be deleted or imported before 1a is
  deployed again (`infra/RESTORE.md`, "Retained resources"). The
  CloudFormation-named trail bucket stays plain `Retain`.
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

From the code review (2026-11-17/18):

- **D20** The `httpx` and `httpcore` loggers are pinned to WARNING at app
  import: with INFO logging, the geocoder's request URLs (the server Maps key
  and searched home addresses) would reach CloudWatch.
- **D21** No PostgreSQL log export in 1a: error DETAIL lines can carry email
  addresses or the app-role password, and a terser parameter group needs a
  reboot. Revisit later.
- **D22** Maintenance window `sun:00:00-sun:01:00` UTC (Sunday 03:00–04:00
  EAT), half an hour clear of the 23:00–23:30 backup window instead of on its
  end.
- **D23** The budget's name is generated by CloudFormation, so a subscriber
  change (a replacement) can create the new budget before deleting the old.
- **D24** `verify-db.sh` exits non-zero when the verify Lambda answers a
  status other than `ok` (3), and under `--strict` when a check returns an
  error row (4).
- **D25** (2026-11-18, the user) The schools are on holiday until
  2027-01-04 and the provider is the operator, so no run is expected on any
  day before then: Release 1a (and 1b and 2, while the holiday lasts) can
  deploy on any day, at any hour clear of the database's backup window and
  maintenance window as read on the day. The checklist's 16:30–22:30 UTC
  window protects route hours, which do not occur in the holiday. The
  open-run checks still run at the deploy, and the deploy's own refusal
  stays on.
- **D26** (2026-11-18, the user chose "rehearse and lock") The `Database`
  row's `Replacement: Conditional` is accepted as the one expected `WARN` of
  the 1a change set, closed by evidence and a guard rather than by a code
  change: a throwaway-stack rehearsal applied exactly 1a's five database
  changes in place, and `saferide-backend` now carries a stack policy denying
  `Update:Replace` and `Update:Delete` on `Database` (infra/README.md,
  "Database stack policy"). The checklist expects that `WARN` and no other,
  and checks the policy before the deploy.
- **D27** (2026-11-18, the user chose "temporary function") Migration 017
  is rehearsed on the production copy through a throwaway stack
  (`saferide-017-rehearsal-<date>`): two functions built from the 1a commit
  with production's migrate and verify handlers, runtime, timeout, memory,
  subnets, Lambda security group and the migrate function's SSM policy, their
  environment set afterwards from a 0600 file to point at the scratch
  instance; invoked, then the stack is deleted. It runs exactly 1a's code path
  without changing production's migrate function's code or configuration
  outside CloudFormation (the open question in infra/RESTORE.md, "017 on the
  production copy"), and adds 1a's `hardening` set on the post-017 copy.

Found and decided while finishing the unit (`0d953eb` and `ebdc123`):

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
- **Retained resources after a failed or reverted 1a:** a failed 1a update
  cleans up after itself (`RetainExceptOnCreate`), except a `/saferide/*`
  group Lambda re-creates after the rollback deleted it; after a deliberate
  rollback to `release/0` or a stack delete, the runbook's default is to
  delete the log groups (after copying what the incident needs) and the SES
  identity; importing them is described but unrehearsed.

## Code review (2026-11-17/18)

The branch through `639954e` was reviewed by eleven reviewers: correctness,
security, adversarial, testing, maintainability, project-standards,
data-migration, reliability, agent-native, learnings and
deployment-verification. A reboot on 2026-11-18 interrupted the adversarial
and deployment-verification reviewers; both were re-run against the fixed
tree. The other nine reviewers' findings were synthesised into four fix
batches, applied in order:

| Batch | Commits | Fixed |
|---|---|---|
| A1 — backend app, migration, integration test | `54ca7ba` | `httpx`/`httpcore` at WARNING (D20) with a fresh-interpreter test; the verify handler's import-time registry check removed (its unit test is the single home); 017 locks the most-referenced tables last (`live_schools` group last); 017 rehearsed over a populated pre-017 shape through `pgconn.exec_`; seeded-parent comment corrected |
| A2 — templates and their tests | `a12bc01` | No RDS log export and no alarm description pointing at one (D21); maintenance `sun:00:00-sun:01:00` (D22); platform logs at INFO; generated budget name (D23); `ApiProbeFailing` says the probe is database-aware only from 1b; shared `template_text.py` reader with a vacuity guard; per-alarm property table, topic-policy and trail tests |
| B — deploy scripts and behavioural tests | `a305a50`, `104985d`, `a16327e` | `changeset_gate.py` with the stateless-replacement rule and `ALLOW_REPLACE` (never the database); explicit stack and region on `sam deploy`; `provider-bootstrap` read with `ssm_required`; push seeding through a 0600 file, aborting on read errors; `provider-bootstrap.sh` refuses to mint on any read error; `ssm_required` always af-south-1; one `PARITY_PAIRS` list plus `FRONTEND_PARAMS` with `MONTHLY_BUDGET_USD`; `verify-db.sh` exit codes and `--strict` (D24); open-run refusal with `ALLOW_OPEN_RUNS`; fake-CLI tests for all of it |
| D — documentation | `ca92d4e` | Checklists moved to `docs/runbooks/hardening-release-checklists.md` and the runbook to `infra/RESTORE.md`, references re-pointed; README alarms, budget, logging, probe, gate, open-run, parity, seeding and `verify-db.sh` text brought in line with A1–B; the runbook checks `cli_history` first, re-applies the new property set without a log export and reads the `no_pin` column during a pepper rotation; the source-region delete uses the region the copy helper printed; this record |

The re-run adversarial and deployment-verification reviewers (2026-11-18,
against `ca92d4e`) were synthesised into a fifth batch:

- **Adversarial:** no actionable findings. Its residuals are adopted: the
  gate dry run on the real stack before the deploy day (how CloudFormation
  reports the `NoEcho` master password), the open-run re-check immediately
  before execution, a rollback's logging check (`release/0` has no `httpx`
  pin), the `/saferide/*` groups Lambda can re-create after a failed 1a, and
  the never-rehearsed HTTP API route removal on a rollback.
- **Deployment verification:** its corrections are adopted (sections 1–4 of
  its report): the post-deploy proofs of operator steps 2 and 7 and the
  48-hour confirmation rule; "none created today" before 1a from `gps`;
  017 rehearsed on a production copy through the migrate function, and the
  "migrate fails" branch; the fixed-name pre-checks; both functions read
  back on production; the stray-variable check and the override
  reconciliation; the 16:30–22:30 UTC window; baselines before and diffs
  after; the frontend change-set review; the tag push on the user's go;
  the probe-alarm settling time; the 1b baselines; the `verify-db.sh` exit
  table; the rollback preconditions, snapshot, read-backs and redeploy
  exports; the first 48 hours' monitoring.

| Batch | Commits | Fixed |
|---|---|---|
| E-code — deploy guards | `f41c5f3` | E1 alert-address guard on both deploys (`ALLOW_NO_ALERTS`; the held primary address passed explicitly); E2 open-run re-check before `execute-change-set`; E3 frontend change-set gate through `changeset_gate.py`, with `AWS::CloudFront::Distribution` and `AWS::S3::Bucket` never replaceable; E4 `verify-db.sh` invokes with `--cli-read-timeout 0` and exits with the CLI's code when the call fails; E5 verify-target check in the open-run check and `verify-db.sh` (`ALLOW_VERIFY_TARGET_MISMATCH`) |
| E-docs — documentation | this record | The corrections above in the checklists, `infra/RESTORE.md` and `infra/README.md` ("Alert-address guard", "Verify-target check", "Frontend change-set gate", the `verify-db.sh` exit table); two new open gates: the gate dry run and the 017 production-copy rehearsal |

The 017 rehearsal as reviewed swaps only the migrate function's
`DATABASE_URL`, but the deployed migrate function is `release/0`'s package,
which has no 017 file: it must also carry 1a's migrate package for the
rehearsal. How that package reaches the function and goes back is an open
decision for the user, recorded with the gate (`infra/RESTORE.md`, "017 on
the production copy").

Residuals deferred:

| Residual | Why deferred |
|---|---|
| A `verify-db.sh` assert mode with expectations per label | The expectations differ per release and per school (counts that fall to 0 only after a job runs, rows that are informative); `--strict` already fails on error rows and the checklists carry the expectations. Revisit once 1b's expectations settle. |
| `verify-target` and `compare-restore` scripts for the runbook's verify-function swap and the `restore-compare` reading | The rehearsal runs the runbook's commands by hand first; it shows which steps are worth scripting. |
| A CSP report endpoint | Report-only violations are read from the phone consoles during the 1a weeks; an endpoint is new public surface and storage. Decide with Release 2's enforce step. |
| A `check-headers` script | The checklist's curl loop over four URLs is the check, run once per release. |
| A parity-diff script (live stack parameters against the template Defaults) | The checklist reads the live parameters once before the first explicit-parameter deploy; from then on every deploy passes every Default. |
| An SES status script | One `get-email-identity` command, run at +24 h and +72 h. |
| A Maps key restriction check through `gcloud` | `gcloud` is not in the deploy toolchain; the phone checks prove the restriction (no `RefererNotAllowedMapError` on either origin). |
| Identity fields in `restore-compare` for the populated restore rehearsal | It compares counts and newest times per append-only table, not which rows; enough to judge a restore point. Revisit with the 1b rehearsal. |
| `provider-bootstrap.sh` still reads the bootstrap payload with errors suppressed (a read error yields version 1) and mints the TOTP pepper and the initial passwords with `--value` in argv | A one-time operator script, already run in production and re-run only for lockout recovery on the operator's machine; the 0600-file treatment comes in a later pass. |
| `ssm_seeded`'s temporary directory has no signal trap | It is removed on every return path and holds one push value for the length of one `put-parameter`; each parameter is seeded once. |
| `samconfig.toml` and stack-name drift beyond B.2 | `sam deploy` now names the stack and region; `samconfig.toml` still supplies the capabilities, `resolve_s3` and the build image and names `saferide-backend` itself, which matters only if `BACKEND_STACK` is overridden. |
| An RDS `DatabaseConnections` alarm | Its threshold needs `max_connections`, read from `hardening` → `instance-settings` after the 1a deploy. |

## Test evidence

| Command | Result |
|---|---|
| `../.venv/bin/python -m pytest -q tests/core` (backend unit, from `backend/`, at `ca92d4e`) | 464 passed |
| `../.venv/bin/python -m pytest -q tests/core` (backend unit, from `backend/`, with the E-docs changes, 2026-11-18) | 464 passed |
| `pytest tests/core/test_changeset_gate.py`, `test_deploy_guards.py`, `test_verify_db_script.py`, `test_logging_hygiene.py`, `test_deploy_params.py`, `test_copy_ssm_script.py`, `test_backend_stack.py`, `test_frontend_stack.py` (2026-11-18, at `a16327e`) | 49, 37, 15, 6, 32, 25, 39 and 21 passed |
| `.venv/bin/python -m pytest -q` (backend unit, from `backend/`, at `54ca7ba`) | 584 passed, 561 skipped (integration self-skips without `RUN_INTEGRATION`) |
| `.venv/bin/python -m pytest -q` (backend unit, from `backend/`, at `0d953eb`) | 578 passed, 560 skipped |
| `pytest tests/core/test_copy_ssm_script.py` at `0d953eb` | 25 passed — fake `aws` first on PATH; report, apply, mismatch, read errors in either region, lost put race, mixed statuses, region resolution, `cli_history`, `bash -x` trace, HUP/INT/QUIT/TERM during a put |
| `pytest tests/core/test_deploy_params.py` at `0d953eb` | 25 passed under `/bin/bash` 3.2.57 |
| Mutation checks (by hand, reverted) | removing the explicit Default, adding `--overwrite`, printing a read, dropping the cleanup trap, or dropping the HUP/QUIT traps each fails the tests; at `104985d` the previous `config.sh`, `deploy-backend.sh`, `provider-bootstrap.sh` and `verify-db.sh` each fail the new tests |
| `bash -n` on every `infra/scripts/*.sh` and `scripts/provider-bootstrap.sh` | clean (`104985d`; again with this record's reference-line edits) |
| `git diff --check` | clean |
| `sam validate --lint` (infra/backend) at `a12bc01` | only the pre-existing W1011 (also at `0d953eb`, and again with this record's one-line template comment edit) |
| `RUN_INTEGRATION=1 pytest tests/integration` | last recorded at `54ca7ba`: 561 passed, 2 skipped; `backend/app`, `backend/db` and the integration tests unchanged since |
| Frontend (`tsc`, vitest, build) | last recorded at `2bafe24`: tsc clean, vitest 346 passed in 23 files, build passes |
| `scripts/certify.sh` at the release commit | green at `156ace9`: backend unit 756 passed / 561 skipped, integration 561 passed / 2 skipped, tsc clean, vitest 346 in 23 files, build, Playwright 102 passed; `tests/core` 514 passed at `c7b0271` (SIGQUIT stub fix, test-only) |

## Rollback position

- **Code rollback to `release/0` (`a59756d`),** backend then frontend, with
  that tag's own scripts (`infra/RESTORE.md`, "Code rollback"). Migration 017
  stays: it is additive, every new column is nullable or defaulted, the audit
  CHECK is a superset, and `release/0`'s code never reads the new objects.
- **Preconditions, immediately before the `release/0` deploy** (it has no
  open-run refusal and no gate): `verify-db.sh hardening` →
  `open-runs-and-today` 0 and the seed markers still listed by
  `migrations` (`release/0`'s migrate step has no seed gate); the
  default-region secret copies exist and match (`copy-ssm-to-backend-region.sh`
  in the deploying shell), because `release/0`'s script reads them there and
  mints a missing one; a fresh manual snapshot kept at least 35 days. After
  the D15 deletion `release/0` is no longer a rollback target.
- **After any rollback:** the three functions' `LoggingConfig` read; JSON at
  INFO is set to WARN at once (`release/0` has no `httpx` pin); the
  database windows and storage limit read back and recorded; a stack stuck
  in `UPDATE_ROLLBACK_FAILED` on the never-rehearsed HTTP API route removal
  is continued skipping the stage, on the user's go. A later 1a redeploy
  re-exports the alert addresses, a granted concurrency threshold, any mail
  or throttle override and the budget, and needs four confirmations again.
- **What reverts with `release/0`'s templates:** the alarms, topics and
  subscriptions, the SES configuration set and mail records, the trail, the
  JSON logging, the headers policy, probes and budget; the database returns
  to 7-day backups with deletion protection off, so RDS drops automated
  backups older than 7 days (the manual snapshots stay); whether the windows
  and storage limit revert is not asserted. The frontend
  build returns to `frontend/.env.local`'s development Maps key and demo Map
  ID.
- **Retained resources (D13, `RetainExceptOnCreate`):** a failed 1a update
  deletes the log groups and the SES identity it had just created, but a
  function that runs after that can make Lambda re-create a `/saferide/*`
  group: check and delete before the retry. A deliberate rollback to
  `release/0` or a stack delete leaves the fixed-name `/saferide/*` log groups and SES
  identity behind, and they must be deleted or imported before 1a is
  deployed again; the CloudFormation-named trail bucket (plain `Retain`) is
  left behind after any rollback and never clashes.
- **Data:** nothing in 1a rewrites existing rows except filling the new
  `driver_code` column; a data problem follows `infra/RESTORE.md`'s
  point-in-time restore and rename swap.

## Deployed

Not deployed. At the deploy, record here: the window (UTC and EAT) and the
instance's windows read before it, the release commit and tag, the
baselines and their diffs, both change-set tables, the migrate result, the
post-deploy rows above with their outputs, the 1b baselines, the
alarm-drill times, the phone-check CSP lines, the SES status at +24 h and
+72 h, and each of the first 48 hours' checks with its time.
