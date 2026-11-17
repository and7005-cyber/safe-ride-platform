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
#    (five "values MATCH" lines; "Operator checklist before Release 1a", step 4)
./scripts/copy-ssm-to-backend-region.sh

# 4. Build + deploy backend (change-set gate), run DB migrations
#    ALERT_EMAIL_PRIMARY / ALERT_EMAIL_SECONDARY on the first alerting deploy
./scripts/deploy-backend.sh          # CONFIRM_CHANGESET=1 to answer y/N first

# 5. Build frontend against the API (Maps key + Map ID from SSM), deploy
#    S3+CloudFront + headers, probes, alarms, budget; upload, invalidate
./scripts/deploy-frontend.sh         # CSP_MODE=report-only (default) | enforce
```

Backend first, then frontend — a release deploys both, in that order, and a
rollback redeploys the previous release tag the same way ("Restore and
rollback runbook"). `scripts/config.sh` holds the shared config (domain, zone
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
| `/saferide/firebase-*`, `/saferide/vapid-*` | af-south-1 | seeded once from `backend/.env` when absent | push (see docs/push-notifications.md) |

The first five also have older copies in the operator's default CLI region
(eu-central-1 on the operator's machine), where the deploy script read them
before the hardening work. `scripts/copy-ssm-to-backend-region.sh` copies them
to af-south-1 and proves each pair equal by digest without printing a value
(operator checklist, step 4). The old copies stay until one week after
Release 2 is stable (D15), because `release/0`'s deploy script still reads them
there — and mints a new database password, PIN pepper or app-role password
when one is missing — so once they are deleted `release/0` must never be
deployed again. The current scripts read only af-south-1.

`deploy-backend.sh` **fails fast** if any parameter marked "must pre-exist"
is missing — none is ever minted. A silently re-minted TOTP pepper would
invalidate every provider's enrolled second factor at once; rotating it
deliberately has the same consequence (every provider needs a peer TOTP reset
and a fresh enrolment — see the provider guide's rotation warning).
Re-running `provider-bootstrap.sh` bumps the payload version so the next
deploy re-keys (or newly creates) the accounts in the payload — the
password-lockout recovery path, and the way to introduce a fresh unenrolled
account after a pepper incident.

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

The five backend "must pre-exist" values are read with `ssm_required`
(`scripts/config.sh`, shared by both deploy scripts): a missing one stops the
deploy before `sam build` with
`ERROR: SSM <name> (af-south-1) is missing — copy or create it in af-south-1 …`.
Copy it from where it lives today as the operator checklist describes; never
re-create it, because a new pepper silently invalidates every driver PIN or
provider second factor and a new database password rotates the master
password under the running stack. `deploy-frontend.sh` reads the two Maps
values the same way before it builds (see "Security headers").

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

**Change-set gate.** Every backend deploy runs
`sam deploy --no-execute-changeset`, then describes the change set and prints
each change (action, logical id, type, replacement, changed properties). It
aborts and deletes the change set, so nothing executes, when:

- the `Database` resource lists `MasterUserPassword` among its changes (the
  af-south-1 `db-password` copy does not match the live password), or
- any resource has `Replacement: True`, or
- the change set did not reach `CREATE_COMPLETE`.

`Replacement: Conditional` prints a warning and continues. With
`CONFIRM_CHANGESET=1` exported the script asks `y/N` before executing (the
Release 1a deploy runs this way); otherwise it executes once the checks pass.
"No changes" skips straight to the migrate step. A failed stack update prints
the latest stack events and exits before migrating.

**Live-parity parameters.** SAM CLI (1.162.1) sends `UsePreviousValue` for
every parameter a deploy does not pass to an existing stack, so a changed
template Default never reached production and a one-off export stuck until
the next export. `deploy-backend.sh` therefore sources
`scripts/parity-params.sh`, whose maps hold the GPS defaults, the API
throttles, the four mail settings, the two alert addresses and
`ConcurrencyAlarmThreshold`, and `build_parity_params` passes them on every
deploy:

- **Non-empty template Default:** always passed — the exported variable under
  the name the app reads (e.g. `GPS_CUSTODY_THRESHOLD_M=200`) when it is set
  and non-empty, otherwise the Default read from `backend/template.yaml`. An
  export lasts one deploy: repeat it on every deploy, or change the Default in
  a release. The deploy log prints each override and the list of Defaults it
  passed.
- **Empty template Default** (`AlertEmailPrimary`, `AlertEmailSecondary`,
  `MailReplyTo`): passed only when exported, because SAM refuses an empty
  override. Otherwise the stack keeps its current value, so a stale value is
  replaced only by exporting a new one; no deploy can clear it back to empty.

Parameters outside the maps (`CorsOrigins`, `AppBaseUrl`, `ApiDomainName`,
`HostedZoneId`, `MailDomain`, `DbEngineVersion`, `DbInstanceClass`,
`DbAllocatedStorage`, `DbMasterUsername`) still keep their previous stack
value: a change to one of their Defaults reaches production only after the
parameter joins a map. A template Default the reader cannot take as a plain
one-line value (`""` for empty) stops the deploy before the build.
`backend/tests/core/test_deploy_params.py` runs `build_parity_params` to pin
the rule. The variable names are the ones `backend/.env` uses, so deploy from
a shell that has not loaded that file (direnv or similar): its development
values would otherwise go to production for that deploy.

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
because every migration is additive ("Restore and rollback runbook").
`release/0`'s scripts still read the default-region parameter copies, which
is why those copies stay until one week after Release 2 is stable.

## Alarms

The backend stack sends every alarm, and its return to OK, to the SNS topic
`saferide-ops` in af-south-1 (stack output `OpsTopicArn`); SES bounce,
complaint, reject, rendering-failure and delivery-delay events land there
too. Each address exported as `ALERT_EMAIL_PRIMARY` / `ALERT_EMAIL_SECONDARY`
on a deploy gets an email subscription that must be confirmed from that
inbox. Later deploys keep the addresses: their template Default is empty, so
the deploy passes them only when exported and the stack keeps its value
otherwise ("Live-parity parameters" above); changing one means exporting the
new address. Thresholds are counts, not rates, and missing data counts as OK.

| Alarm | Means | First response |
|---|---|---|
| `ApiServerErrors` | at least one API 5xx in 5 minutes | find the ERROR lines in `/saferide/api` at that time; roll back if a deploy just landed |
| `ApiClientErrorsBurst` | 50 or more API 4xx in 5 minutes | read `/saferide/api` for the paths and whether one client repeats (broken release, looping phone, sign-in probing) |
| `ApiLatencyP95` | p95 over 3 s in 3 of 5 five-minute periods | check the database CPU and credit alarms and the API function's duration |
| `ApiFunctionErrors` | the API function failed (init crash or timeout) | read the latest `/saferide/api` errors; roll back if a deploy just landed |
| `ApiFunctionThrottles` | the API function was throttled in the last minute | check `AccountConcurrency` and the pings route for a looping client; raise the quota |
| `MigrateFunctionErrors` | the migrate function failed | read `/saferide/migrate`; fix and re-run the migrate step (the live API may need it) |
| `AccountConcurrency` | Lambda concurrency at `ConcurrencyAlarmThreshold` (80% of the account limit, 8 today) | find the runaway client; request a quota increase, then export `CONCURRENCY_ALARM_THRESHOLD` (80% of the new limit) on every backend deploy until a release raises the Default |
| `DbFreeStorageLow` | database free storage under 2 GiB | check the storage graph and that the position-trail purge runs |
| `DbFreeableMemoryLow` | freeable memory under 100 MB for 15 minutes | check the connection count and slow statements in the postgresql log export |
| `DbCpuHigh` | database CPU over 80% for 15 minutes | find the expensive statements in the postgresql log export and the routes that run them |
| `DbCpuCreditsLow` | under 30 CPU credits for 15 minutes; the instance will be held to baseline | find what burns CPU; consider a larger class |
| `ApiCertificateExpiry` | the API certificate expires in under 20 days (custom domain only) | check the api validation CNAME in Route 53 and the renewal status in ACM |

Application exceptions surface as API 5xx: FastAPI answers an unhandled
exception with a 500 response, so `ApiServerErrors` is the application alarm
and Lambda `Errors` means init failures or timeouts. The retention heartbeat
and the metric-filter alarms arrive in Release 1b.

**us-east-1 (frontend stack).** Route 53 health-check, CloudFront and
CloudFront-certificate metrics exist only in us-east-1, so the frontend stack
has its own topic, `saferide-ops-us-east-1` (output `OpsTopicUsEast1Arn`),
with the same two addresses: export `ALERT_EMAIL_PRIMARY` /
`ALERT_EMAIL_SECONDARY` on a `deploy-frontend.sh` run too, so the two topics
make four confirmations. Later deploys keep them (`aws cloudformation deploy`
reuses unpassed parameters). Two Route 53 health checks (outputs
`ApiHealthCheckId`, `SiteHealthCheckId`) probe over HTTPS every 30 seconds
from us-east-1, eu-west-1 and ap-southeast-1 and count any 2xx or 3xx as
healthy. In December nothing calls the API, so only the probes can report a
dead stack: their alarms treat missing data as breaching.

| Alarm | Means | First response |
|---|---|---|
| `ApiProbeFailing` | no healthy answer from `https://api…/api/health` for 3 minutes: the API or its database is down for everyone | curl the health endpoint; read the af-south-1 alarms and `/saferide/api`; roll back if a deploy just landed |
| `SiteProbeFailing` | the site's home page has not loaded for 3 minutes | open the site; check the distribution and its S3 bucket; redeploy the frontend from the last release tag if a deploy just landed |
| `SiteServerErrors` | over 5% of CloudFront responses were 5xx for 10 minutes | read the distribution's error graphs and the AWS Health Dashboard for CloudFront and S3 |
| `SiteCertificateExpiry` | the CloudFront certificate expires in under 20 days (custom domain only) | check the apex and www validation CNAMEs in Route 53 and the renewal status in ACM (us-east-1) |

The `saferide-monthly-cost` budget (`MonthlyBudgetUsd`, 60 USD, the whole
account) mails the same addresses directly when actual spend passes 80% and
when the month's forecast passes 100%; no confirmation is needed, and it
exists only when at least one address is set.

**Log retention by hand (operator step, once after the Release 1a deploy).**
The functions now log JSON to `/saferide/api`, `/saferide/migrate` and
`/saferide/verify` (90 days, retained). Their older `/aws/lambda/*` groups and
the PostgreSQL export group (created when the export is first enabled) never
expire until set; `<…FunctionName>` are the stack outputs of those names and
`<db-id>` is the `Database` physical id (see Teardown):

```bash
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<ApiFunctionName>
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<MigrateFunctionName>
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/lambda/<VerifyFunctionName>
aws logs put-retention-policy --region af-south-1 --retention-in-days 90 --log-group-name /aws/rds/instance/<db-id>/postgresql
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

## Operator checklist before Release 1a

Hardening plan U1. Start these the day the plan is approved: three of them
wait on AWS or Google. Each step says what to do, what proves it and who does
it; paste the proof into the Release 1a validation record
(`docs/validation/2026-11-25-hardening-release-1a.md`). Run the commands from
`infra/` in bash with credentials for the production account. None of them
prints a secret.

1. **SES production access and two verified addresses (af-south-1).**
   - *Do:* request production access in af-south-1 — mail type
     transactional, website `https://saferidelive.co.ke`, both operator
     addresses as contacts (console: SES → Account dashboard → Request
     production access, or the command below). The answer can take up to
     96 hours. Meanwhile verify both operator addresses as SES identities, so
     Release 1b's mail can be rehearsed while the account is still in the
     sandbox (which delivers only to verified addresses); each owner clicks
     the link SES mails them. These two identities live outside the stacks
     and do not clash with the domain identity Release 1a creates.

     ```bash
     aws sesv2 put-account-details --region af-south-1 --production-access-enabled \
       --mail-type TRANSACTIONAL --website-url https://saferidelive.co.ke --contact-language EN \
       --additional-contact-email-addresses <operator-1> <operator-2> \
       --use-case-description "Transactional mail only: password-reset and email-verification links and account notices to parents and school staff using SafeRide. No marketing. Bounces and complaints reach the operators through an SNS topic."
     aws sesv2 create-email-identity --region af-south-1 --email-identity <operator-1>
     aws sesv2 create-email-identity --region af-south-1 --email-identity <operator-2>
     ```
   - *Proves it:* `aws sesv2 get-account --region af-south-1 --query
     '{production:ProductionAccessEnabled,review:Details.ReviewDetails}'`
     shows the case id and `PENDING` or `GRANTED` (record the case id);
     `aws sesv2 get-email-identity --region af-south-1 --email-identity
     <operator-n> --query VerifiedForSendingStatus` is `true` for both.
   - *Who:* the AWS operator files the request and the identities; each
     address owner clicks their own link.

2. **Lambda concurrency quota (af-south-1).**
   - *Do:* ask for 1000 concurrent executions (today 10). Once granted, the
     `AccountConcurrency` alarm must follow it: export
     `CONCURRENCY_ALARM_THRESHOLD=800` (80% of the new limit) on every backend
     deploy until a release raises the `ConcurrencyAlarmThreshold` Default
     (8). Without the export the deploy passes 8 again ("Live-parity
     parameters").

     ```bash
     aws service-quotas request-service-quota-increase --region af-south-1 \
       --service-code lambda --quota-code L-B99A9384 --desired-value 1000
     aws service-quotas list-requested-service-quota-change-history-by-quota --region af-south-1 \
       --service-code lambda --quota-code L-B99A9384 \
       --query 'RequestedQuotas[].[Id,Status,DesiredValue,Created]' --output table
     ```
   - *Proves it:* `aws service-quotas get-service-quota --region af-south-1
     --service-code lambda --quota-code L-B99A9384 --query Quota.Value` is at
     least 100 (R24's floor), or the request id and status are recorded;
     after the next deploy with the export, `aws cloudwatch describe-alarms
     --region af-south-1 --alarm-name-prefix
     saferide-backend-AccountConcurrency --query 'MetricAlarms[].Threshold'`
     shows 800.
   - *Who:* the AWS operator.

3. **Tag the production commit `release/0`.**
   - *Do:* tag the commit production runs today — `a59756d`, the merge of
     PR #16 on `main` — and push the tag. It is the rollback target for
     Release 1a.

     ```bash
     git fetch origin --tags
     git log -1 --format='%H %s' a59756d      # Merge pull request #16 …
     git tag -a release/0 a59756d -m "release/0: production before the hardening releases (merge of PR #16)"
     git push origin release/0
     ```
   - *Proves it:* `git ls-remote --tags origin 'refs/tags/release/*'` lists
     `release/0` with its peeled commit `a59756d…`.
   - *Who:* the repository owner. Pushing a tag changes origin, so it needs
     the user's explicit go.

4. **Copy the five secrets to af-south-1 (D15).**
   - *Do:* run the copy helper in report mode, then with `--apply`, then in
     report mode again. The source is the region an un-regioned CLI call
     uses, which is where the pre-hardening deploy read: `AWS_REGION`, else
     `AWS_DEFAULT_REGION`, else `aws configure get region` (eu-central-1 on
     the operator's machine); the header prints which, and `SOURCE_REGION`
     overrides all three. It refuses to run while the CLI's `cli_history` is
     enabled, since the CLI would then store the decrypted values on disk.

     ```bash
     ./scripts/copy-ssm-to-backend-region.sh            # report: what af-south-1 lacks
     ./scripts/copy-ssm-to-backend-region.sh --apply    # create only the missing ones there
     ./scripts/copy-ssm-to-backend-region.sh            # proof: five "values MATCH" lines, exit 0
     ```

     Values never leave python3: the digests are compared in-process, and
     `--apply` hands each value to `put-parameter` through a 0600 file in a
     private temporary directory that is removed on exit. It never
     overwrites: a `MISMATCH` stops it before any write. Resolve that one by
     hand — find which value the live stack uses (the database's master
     password; the pepper behind the stored PIN or TOTP hashes) — never by
     re-creating it. `backend/tests/core/test_copy_ssm_script.py` (unit suite)
     runs the helper against a fake `aws`.
   - *Proves it:* the third run's five `present in both, values MATCH` lines,
     pasted into the record.
   - *Afterwards:* keep the source-region copies until one week after
     Release 2 is stable — `release/0`'s deploy script reads them there —
     then delete them. From then on `release/0` must never be deployed: its
     script would mint new secrets.

     ```bash
     for name in /saferide/db-password /saferide/pin-pepper /saferide/db-app-password \
                 /saferide/totp-pepper /saferide/google-maps-api-key; do
       aws ssm delete-parameter --region eu-central-1 --name "$name"
     done
     ```
   - *Who:* the AWS operator.

5. **Production Map ID and browser key (Google Cloud).**
   - *Do:* in the Google Cloud project that owns the current key: Maps
     Platform → Map management → Create Map ID, type JavaScript, vector (the
     demo id the app uses today renders vector). Then Credentials → Create
     API key with application restriction *Websites*
     `https://saferidelive.co.ke/*` and `https://www.saferidelive.co.ke/*`,
     and API restriction *Maps JavaScript API* only (Places, geocoding and
     routes run server-side with `/saferide/google-maps-api-key`). Store both
     values as SecureString in af-south-1 so neither reaches an argv or the
     shell history: `read -s` does not echo, `printf` is a bash builtin, and
     python3 writes the put-parameter input into a 0600 file.

     ```bash
     (
       umask 077
       WORK="$(mktemp -d)"
       for name in /saferide/google-maps-browser-key /saferide/google-maps-map-id; do
         read -r -s -p "Value for $name: " VALUE; echo
         printf '%s' "$VALUE" | python3 -c 'import json, sys; json.dump({"Name": sys.argv[1], "Type": "SecureString", "Value": sys.stdin.read()}, open(sys.argv[2], "w"))' "$name" "$WORK/put.json"
         unset VALUE
         aws ssm put-parameter --region af-south-1 --cli-input-json "file://$WORK/put.json" --query Version --output text
         rm -f "$WORK/put.json"
       done
       rm -rf "$WORK"
     )
     ```

     There is no `"Overwrite"`: a second run fails with
     `ParameterAlreadyExists`; to replace a value deliberately, add
     `"Overwrite": true` to the dictionary. Keep the unrestricted development
     key only in `frontend/.env.local` — local development, and the
     `release/0` frontend build, which reads it from there.
   - *Proves it:* `aws ssm describe-parameters --region af-south-1
     --parameter-filters
     "Key=Name,Values=/saferide/google-maps-browser-key,/saferide/google-maps-map-id"
     --query 'Parameters[].[Name,Type,Version]' --output table` shows two
     SecureString rows; the restriction is proved by the Release 1a phone
     checks (maps render on both origins with no `RefererNotAllowedMapError`).
   - *Who:* the Google Cloud project owner creates both; the AWS operator
     stores them.

6. **Route 53 preflight.**
   - *Do:* confirm nothing in the zone collides with the mail records
     Release 1a creates (`_dmarc` TXT, three DKIM CNAMEs, `mail.` MX and SPF)
     and there is no second apex SPF. Clean on 2026-11-17 (only the site
     records and the three ACM validation CNAMEs); re-run it right before
     the deploy.

     ```bash
     aws route53 list-resource-record-sets --hosted-zone-id Z0459820URO1AH08IO8T \
       --query "ResourceRecordSets[?Name=='_dmarc.saferidelive.co.ke.' || Name=='mail.saferidelive.co.ke.' || ends_with(Name, '.mail.saferidelive.co.ke.') || contains(Name, '._domainkey.') || Type=='MX' || (Type=='TXT' && Name=='saferidelive.co.ke.')].[Name,Type]" \
       --output table
     ```
   - *Proves it:* an empty result. A hit would make the stack's record
     creation fail and roll the whole update back: resolve it first.
   - *Who:* the AWS operator.

7. **Alert addresses and the four confirmations.**
   - *Do:* confirm the two addresses with their owners and export both in
     the shell that runs both Release 1a deploys:

     ```bash
     export ALERT_EMAIL_PRIMARY=<operator-1> ALERT_EMAIL_SECONDARY=<operator-2>
     ```

     Each deploy subscribes both addresses to its topic (`saferide-ops` in
     af-south-1, `saferide-ops-us-east-1`), so four "AWS Notification -
     Subscription Confirmation" mails arrive; each must be clicked within
     48 hours. A missed one: `aws sns subscribe --region <region> --topic-arn
     <arn> --protocol email --notification-endpoint <address>` sends a fresh
     confirmation (note it in the record).
   - *Proves it:* four confirmed subscriptions, none `PendingConfirmation`:

     ```bash
     aws sns list-subscriptions-by-topic --region af-south-1 \
       --topic-arn "$(aws cloudformation describe-stacks --region af-south-1 --stack-name saferide-backend \
         --query "Stacks[0].Outputs[?OutputKey=='OpsTopicArn'].OutputValue" --output text)" \
       --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output table
     aws sns list-subscriptions-by-topic --region us-east-1 \
       --topic-arn "$(aws cloudformation describe-stacks --region us-east-1 --stack-name saferide-frontend \
         --query "Stacks[0].Outputs[?OutputKey=='OpsTopicUsEast1Arn'].OutputValue" --output text)" \
       --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output table
     ```
   - *Who:* the user confirms the addresses; both recipients click; the AWS
     operator runs the check.

8. **The deploy day.**
   - *Do:* agree a day with no run at either production school (and none at
     the test school other than the phone-check drill run), and deploy
     outside 02:30–16:00 UTC (05:30–19:00 EAT, route hours).
   - *Proves it:* no open run and none created today, read just before the
     deploy. Before Release 1a the deployed verify function predates the
     `hardening` set, so read `open-runs` from `./scripts/verify-db.sh
     tenancy-preflight` (0). From Release 1a on, `./scripts/verify-db.sh
     hardening` → `open-runs-and-today` shows `open_runs` 0 and
     `created_today_nairobi` 0. Record the time of the check.
   - *Who:* the user agrees the day with Kuumbai and the schools; the AWS
     operator runs the check.

## Release 1a deploy checklist

Hardening plan U6 and "Documentation / Operational Notes". Every line ends in
the validation record. Run from `infra/` in one bash session.

**Before the deploy** — all of these hold:

1. The operator checklist is complete (SES access and the quota at least
   requested), with its proofs in the record.
2. `scripts/certify.sh` exits 0 at the release commit (the
   `feat/hardening-r1a` tip).
3. `release/1a` is tagged on that commit and pushed, and HEAD is on it —
   `require_clean_checkout` accepts a `release/*` tag:

   ```bash
   git tag -a release/1a <release-commit> -m "release/1a: hardening Release 1a (schema and platform)"
   git push origin release/1a
   git checkout release/1a
   git status --short          # only the untracked .claude/ and justfile
   ```
4. The scratch restore and the property dry run are done, timings recorded
   ("Restore and rollback runbook" → "Rehearsal").
5. The live parity values are read. From this deploy on every parity
   parameter is passed explicitly, so a live value an earlier one-off export
   left behind returns to the template Default unless it is exported again:

   ```bash
   aws cloudformation describe-stacks --region af-south-1 --stack-name saferide-backend \
     --query 'Stacks[0].Parameters[].[ParameterKey,ParameterValue]' --output table
   ```

   `GpsCustodyThresholdM` 150, `GpsVicinityRadiusM` 100, `GpsFixAccuracyCapM`
   200, `GpsPositionRetentionDays` 90, `GpsPingIntervalS` 10, `GpsStaleAfterS`
   90, `GpsFixWaitBudgetS` 5, `ApiDefaultBurstLimit` 100, `ApiDefaultRateLimit`
   50, `PingsRouteBurstLimit` 20 and `PingsRouteRateLimit` 10 are the
   Defaults (secret parameters show `****`). Export any other value you mean
   to keep, and record why.
6. A manual snapshot is taken as a fixed restore point (a rollback to
   `release/0` would cut the automated backups back to 7 days):

   ```bash
   DB_ID="$(aws cloudformation describe-stack-resource --region af-south-1 --stack-name saferide-backend \
     --logical-resource-id Database --query StackResourceDetail.PhysicalResourceId --output text)"
   SNAP="saferide-pre-1a-$(date -u +%Y%m%d)"
   aws rds create-db-snapshot --region af-south-1 --db-instance-identifier "$DB_ID" \
     --db-snapshot-identifier "$SNAP" --query DBSnapshot.Status --output text
   aws rds wait db-snapshot-available --region af-south-1 --db-snapshot-identifier "$SNAP"
   ```
7. The deploy window holds (operator checklist, step 8) and the exports are
   set: `ALERT_EMAIL_PRIMARY`, `ALERT_EMAIL_SECONDARY`, and
   `CONCURRENCY_ALARM_THRESHOLD=800` only if the quota is granted.

**Deploy, in order:**

1. **Backend** — `CONFIRM_CHANGESET=1 ./scripts/deploy-backend.sh`. Watch for:
   - `==> Clean checkout: HEAD <sha> is tagged release/1a`, and no
     `ERROR: SSM …` line (all five secrets read from af-south-1);
   - `==> Parameter override:` lines for the exported values and one
     `==> Live-parity parameters at their template Default: …` line;
   - the change-set table. Expected among the rows: `Modify Database`
     with replacement `False` and the changed properties
     `BackupRetentionPeriod`, `DeletionProtection`,
     `EnableCloudwatchLogsExports`, `MaxAllocatedStorage`,
     `PreferredBackupWindow`, `PreferredMaintenanceWindow` — and never
     `MasterUserPassword`; `Add` rows for the log groups, the ops topic and
     subscriptions, the alarms, the SES resources and records and the trail
     with its bucket. The gate itself aborts (and deletes the change set) on
     `MasterUserPassword` or any `Replacement: True`; answer `y` only when
     there is no `ABORT`/`WARN` line and no `Remove` row you cannot explain;
   - `==> Stack saferide-backend deployed` (the database changes can take
     several minutes);
   - the migrate result: `migrations.applied` is `["017_account_hardening"]`,
     `seeds` is `{"skipped": "APP_ENV=production", "files":
     ["001_demo_seed.sql", "002_live_demo_seed.sql",
     "003_local_snapshot.sql"]}`, and `app_role.synced` is `true`.
2. **Frontend** — `CSP_MODE=report-only ./scripts/deploy-frontend.sh`
   (report-only is also the default): no `ERROR: SSM …` line for the two Maps
   values, the build passes, the stack deploys, and the invalidation id is
   printed.

**After the deploy:**

- `./scripts/verify-db.sh migrations` lists `017_account_hardening`.
- `./scripts/verify-db.sh hardening`: `017-objects-present` is `true` on all
  fifteen rows, `audit-check-widening-017` all `true`,
  `schools-without-driver-code` 0, and no row carries an `error`.
- Headers on the apex, www, a deep route and the service worker — HSTS
  (`max-age=31536000; includeSubDomains`, no preload), `nosniff`,
  `X-Frame-Options: DENY`, `strict-origin-when-cross-origin`, the
  Permissions-Policy, and `content-security-policy-report-only` with no
  enforcing `content-security-policy` line:

  ```bash
  for url in https://saferidelive.co.ke/ https://www.saferidelive.co.ke/ \
             https://saferidelive.co.ke/parent/track https://saferidelive.co.ke/sw.js; do
    echo "== $url"
    curl -sSI "$url" | grep -iE '^(HTTP/|strict-transport-security|x-content-type-options|x-frame-options|referrer-policy|permissions-policy|content-security-policy)'
  done
  ```
- Database properties, with nothing pending (`$DB_ID` as in step 6 above):

  ```bash
  aws rds describe-db-instances --region af-south-1 --db-instance-identifier "$DB_ID" \
    --query 'DBInstances[0].{deletionProtection:DeletionProtection,backupRetention:BackupRetentionPeriod,maxStorage:MaxAllocatedStorage,backupWindow:PreferredBackupWindow,maintenanceWindow:PreferredMaintenanceWindow,logExports:EnabledCloudwatchLogsExports,pending:PendingModifiedValues}'
  ```

  Expect `true`, 35, 100, `23:00-23:30`, `sat:23:30-sun:00:30`,
  `["postgresql"]` and an empty `pending`.
- Every alarm in `OK` or `INSUFFICIENT_DATA`:

  ```bash
  aws cloudwatch describe-alarms --region af-south-1 --alarm-name-prefix saferide-backend- \
    --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
  aws cloudwatch describe-alarms --region us-east-1 --alarm-name-prefix saferide-frontend- \
    --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
  ```
- The four subscriptions confirmed (operator checklist, step 7).
- Alarm drill, one alarm per topic: both inboxes receive the ALARM mail
  within five minutes and the OK mail after the next evaluation. Record the
  times.

  ```bash
  ALARM="$(aws cloudwatch describe-alarms --region af-south-1 --alarm-name-prefix saferide-backend-ApiServerErrors \
    --query 'MetricAlarms[0].AlarmName' --output text)"
  aws cloudwatch set-alarm-state --region af-south-1 --alarm-name "$ALARM" \
    --state-value ALARM --state-reason "Release 1a alarm drill"
  ALARM="$(aws cloudwatch describe-alarms --region us-east-1 --alarm-name-prefix saferide-frontend-SiteServerErrors \
    --query 'MetricAlarms[0].AlarmName' --output text)"
  aws cloudwatch set-alarm-state --region us-east-1 --alarm-name "$ALARM" \
    --state-value ALARM --state-reason "Release 1a alarm drill"
  ```
- SES identity at +24 h and +72 h — `true`, `SUCCESS`, `SUCCESS` by +72 h:

  ```bash
  aws sesv2 get-email-identity --region af-south-1 --email-identity saferidelive.co.ke \
    --query '{verified:VerifiedForSendingStatus,dkim:DkimAttributes.Status,mailFrom:MailFromAttributes.MailFromDomainStatus}'
  ```
- CloudTrail logging and its first objects:

  ```bash
  aws cloudtrail get-trail-status --region af-south-1 --name saferide-trail \
    --query '{logging:IsLogging,lastDelivery:LatestDeliveryTime,lastError:LatestDeliveryError}'
  TRAIL_BUCKET="$(aws cloudformation describe-stacks --region af-south-1 --stack-name saferide-backend \
    --query "Stacks[0].Outputs[?OutputKey=='TrailBucketName'].OutputValue" --output text)"
  aws s3 ls "s3://$TRAIL_BUCKET/AWSLogs/" --recursive | head -5
  ```
- Three sign-ins prove the copied secrets: one driver PIN sign-in
  (`pin-pepper`), one provider sign-in with TOTP (`totp-pepper`) and one staff
  password sign-in (the API's database path). The change-set gate already
  proved `db-password`; planner autocomplete below proves
  `google-maps-api-key`.
- Phone checks on Android Chrome and Samsung Internet (USB debugging,
  `chrome://inspect`) and iOS Safari in the installed home-screen app (Web
  Inspector): sign-in, parent Track, Fleet Map, planner autocomplete, push
  enable, and driver Start Run on the test school (end the run afterwards).
  Copy every `Content-Security-Policy` console line verbatim into the record —
  they decide Release 2's policy.
- 90-day retention by hand on the old `/aws/lambda/*` groups and the RDS
  export group (the four commands under "Alarms"), then confirm:
  `aws logs describe-log-groups --region af-south-1 --log-group-name-prefix
  /aws/ --query 'logGroups[].[logGroupName,retentionInDays]' --output table`.
- Fill the record's "Deployed" section.

## Restore and rollback runbook

Hardening plan U6 (R21). One bash session from `infra/`, started with the
lines below; the variables carry from block to block. No command here prints a
secret: where a value is needed it moves through a pipe or a 0600 file, and
AWS calls whose answer would contain one are cut down with `--query`.

```bash
umask 077
export AWS_PAGER=""
REGION=af-south-1
DB_ID="$(aws cloudformation describe-stack-resource --region $REGION --stack-name saferide-backend \
  --logical-resource-id Database --query StackResourceDetail.PhysicalResourceId --output text)"
VERIFY_FN="$(aws cloudformation describe-stacks --region $REGION --stack-name saferide-backend \
  --query "Stacks[0].Outputs[?OutputKey=='VerifyFunctionName'].OutputValue" --output text)"
wait_named() { # instance id -> returns once an instance of that name is available
  until [ "$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$1" \
      --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null)" = available ]; do sleep 20; done
}
```

### Decide first

1. **Is the data wrong** — rows lost or corrupted by a bad migration, a
   destructive bug or an operator mistake? → **Data restore** (below). The
   decision window is the next morning's first run: once live runs have
   written on top, the damaged state is the new baseline and the fix is
   forward.
2. **Otherwise the code or the stack is wrong** — 5xx, a broken screen, an
   alarm storm, a failed migrate step after a deploy? → **Code rollback**:
   redeploy the previous release tag, backend then frontend, never the
   frontend alone. Every migration is additive, so older code runs on the
   newer schema; an older frontend alone may call the newer backend in a way
   it no longer accepts (from Release 1b, a PIN sign-in without a school).
3. **A pepper may have leaked** (with a database copy it exposes every PIN or
   second factor) → **Pepper rotation**.

### Code rollback

```bash
cd "$(git rev-parse --show-toplevel)"
git status --short                         # nothing modified or staged
git fetch origin --tags
git checkout release/<previous>            # detached HEAD on the tag
(cd infra && ./scripts/deploy-backend.sh)  # the tag's own script and template
(cd infra && ./scripts/deploy-frontend.sh)
git checkout -                             # back to the branch you came from
```

Then `curl -s https://api.saferidelive.co.ke/api/health`, one sign-in per
role, and `./scripts/verify-db.sh migrations` (a rollback never removes a
migration). Record the start and end times of both deploys.

**Rolling back Release 1a means `release/0`, whose scripts predate the
hardening:**

- No clean-checkout or change-set gate, and its migrate step applies the seed
  files, as every deploy before 1a did.
- It reads the five secrets from the CLI's default region and **mints** a new
  `db-password`, `pin-pepper` or `db-app-password` when one is missing there.
  Before checking the tag out, run `./scripts/copy-ssm-to-backend-region.sh`
  from the current checkout, in the same shell that will deploy: it resolves
  the source region the way `release/0`'s script will (`AWS_REGION`,
  `AWS_DEFAULT_REGION`, the configured region), so five MATCH lines prove the
  copies that script reads exist and equal af-south-1's. After the D15
  deletion, never deploy `release/0`.
- Its template takes Release 1a's stack changes away: the alarms, the ops
  topic and subscriptions, the SES configuration set and mail records, the
  trail, the JSON logging (functions log to `/aws/lambda/*` again). The
  database goes back to 7-day backups with deletion protection off and loses
  the windows, the log export and storage autoscaling; RDS then drops
  automated backups older than 7 days, so the restore window shrinks to
  7 days (the manual pre-1a snapshot stays). Migration 017's objects stay:
  they are additive, every new column is nullable or has a default, and
  `release/0`'s code never reads them.
- Its frontend build reads `frontend/.env.local` (the development Maps key
  and the demo Map ID), and its frontend stack drops the headers policy, the
  probes, the us-east-1 alarms and the budget.
- The `/saferide/*` log groups, the SES identity and the trail bucket stay
  behind: see "Retained resources" before deploying 1a again.

### Data restore: point-in-time into a scratch instance

**A. Restore.** Pick the restore target: an instant before the fault, no later
than `latestRestorable`, with a UTC offset (`verify-db.sh` refuses a naive
time). The scratch instance uses the stack's subnet group, database security
group and parameter group, so the verify function reaches it.

```bash
aws rds describe-db-instances --region $REGION --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].{class:DBInstanceClass,engine:EngineVersion,latestRestorable:LatestRestorableTime,subnetGroup:DBSubnetGroup.DBSubnetGroupName,securityGroups:VpcSecurityGroups[].VpcSecurityGroupId,parameterGroup:DBParameterGroups[0].DBParameterGroupName}'
SUBNET_GROUP="$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].DBSubnetGroup.DBSubnetGroupName' --output text)"
DB_SG="$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)"
PARAM_GROUP="$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)"
RESTORE_TS="2026-11-24T18:00:00+00:00"     # the restore target
SCRATCH=saferide-restore-test              # saferide-restored-<yyyymmdd> when it will replace production
date -u +%FT%TZ                            # decision time -> record
aws rds restore-db-instance-to-point-in-time --region $REGION \
  --source-db-instance-identifier "$DB_ID" --target-db-instance-identifier "$SCRATCH" \
  --restore-time "$RESTORE_TS" \
  --db-subnet-group-name "$SUBNET_GROUP" --vpc-security-group-ids "$DB_SG" \
  --db-parameter-group-name "$PARAM_GROUP" --db-instance-class db.t4g.micro \
  --no-publicly-accessible --no-multi-az --no-deletion-protection \
  --query DBInstance.DBInstanceStatus --output text
wait_named "$SCRATCH"
date -u +%FT%TZ                            # available -> record
```

**B. Point the verify function at it.** Its `DATABASE_URL` is rebuilt from
the parameter store (the master password from `/saferide/db-password`, user,
port and database from the current URL, host from the scratch endpoint) —
never pasted. The saved original configuration contains the master password,
so it lives only in a private temporary directory that step C deletes.

```bash
SCRATCH_HOST="$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$SCRATCH" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
WORK="$(mktemp -d)"
python3 - "$WORK" "$SCRATCH_HOST" \
  <(aws lambda get-function-configuration --region $REGION --function-name "$VERIFY_FN" --output json) \
  <(aws ssm get-parameter --region $REGION --name /saferide/db-password --with-decryption --output json) <<'PY'
import json, sys
from urllib.parse import urlsplit
work, host, config_path, param_path = sys.argv[1:5]
with open(config_path) as handle:
    config = json.load(handle)
with open(param_path) as handle:
    password = json.load(handle)["Parameter"]["Value"]
env = config["Environment"]["Variables"]
url = urlsplit(env["DATABASE_URL"])
scratch = dict(env, DATABASE_URL="postgresql://{}:{}@{}:{}{}".format(
    url.username, password, host, url.port or 5432, url.path))
for name, variables in (("original", env), ("scratch", scratch)):
    with open("{}/{}.json".format(work, name), "w") as handle:
        json.dump({"FunctionName": config["FunctionName"], "Environment": {"Variables": variables}}, handle)
print("saved the configuration; the scratch DATABASE_URL points at", host)
PY
aws lambda update-function-configuration --region $REGION --cli-input-json "file://$WORK/scratch.json" \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --region $REGION --function-name "$VERIFY_FN"
aws lambda get-function-configuration --region $REGION --function-name "$VERIFY_FN" --output json \
  | python3 -c 'import json, sys; from urllib.parse import urlsplit; print("verify DATABASE_URL host:", urlsplit(json.load(sys.stdin)["Environment"]["Variables"]["DATABASE_URL"]).hostname)'
```

The host printed must be the scratch endpoint. Then, against the restore:

```bash
./scripts/verify-db.sh restore-compare "$RESTORE_TS"   # from Release 1a on
./scripts/verify-db.sh migrations
./scripts/verify-db.sh gps
./scripts/verify-db.sh baseline <school-uuid>          # each production school (ids: tenancy-preflight → school-rows)
date -u +%FT%TZ                                        # first query -> record
```

Do not deploy while the verify function points at the scratch instance, and
do not end the session before step C.

**C. Put the verify function back and read it back**, then run the same sets
against production:

```bash
aws lambda update-function-configuration --region $REGION --cli-input-json "file://$WORK/original.json" \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --region $REGION --function-name "$VERIFY_FN"
aws lambda get-function-configuration --region $REGION --function-name "$VERIFY_FN" --output json \
  | python3 -c 'import hashlib, json, sys; d = lambda v: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest(); live = json.load(sys.stdin)["Environment"]["Variables"]; saved = json.load(open(sys.argv[1]))["Environment"]["Variables"]; print("verify environment restored:", "MATCH" if d(live) == d(saved) else "MISMATCH")' "$WORK/original.json"
rm -rf "$WORK"
./scripts/verify-db.sh restore-compare "$RESTORE_TS"
./scripts/verify-db.sh migrations
```

Reading `restore-compare`: on the restore, `total_rows` equals
`rows_at_or_before` and `max_time` is at or before the target; on production,
`rows_at_or_before` equals the restore's `total_rows`, and `total_rows -
rows_at_or_before` is what was written after the target. Explain every
difference in the record (a transaction that started before the target and
committed after it is the usual small one).

**D. Replace production: the rename swap** (primary path). Only on the user's
explicit go, outside route hours where possible. Restore with `SCRATCH` set
to `saferide-restored-<yyyymmdd>` and check it with steps B and C first. The
restored instance takes the production identifier, so its endpoint is the
one every function's `DATABASE_URL` already names and CloudFormation tracks
it as `Database` without a stack change. Writes made to production after the
target are lost by design; the production `restore-compare` above says how
many per table.

```bash
OLD="${DB_ID}-replaced-$(date -u +%Y%m%d)"
date -u +%FT%TZ          # swap start -> record; the API fails until the second rename completes
aws rds modify-db-instance --region $REGION --db-instance-identifier "$DB_ID" \
  --new-db-instance-identifier "$OLD" --apply-immediately --query DBInstance.DBInstanceStatus --output text
wait_named "$OLD"
aws rds modify-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --new-db-instance-identifier "$DB_ID" --apply-immediately --query DBInstance.DBInstanceStatus --output text
wait_named "$DB_ID"
aws rds reboot-db-instance --region $REGION --db-instance-identifier "$OLD" \
  --query DBInstance.DBInstanceStatus --output text   # drops pooled connections still open to the old instance
date -u +%FT%TZ          # swap end -> record
curl -s https://api.saferidelive.co.ke/api/health
./scripts/verify-db.sh migrations
```

A point-in-time restore does not carry `DeletionProtection`,
`MaxAllocatedStorage`, the backup retention, the log export, the backup and
maintenance windows or the `Name` tag. **Re-apply them by hand** — a deploy
does not: CloudFormation compares the new template with the last one it
applied, not with the live instance, so an unchanged `Database` resource gets
no update. Then confirm with the describe command in the Release 1a checklist
and a drift check (`IN_SYNC` expected):

```bash
aws rds modify-db-instance --region $REGION --db-instance-identifier "$DB_ID" \
  --backup-retention-period 35 --preferred-backup-window 23:00-23:30 \
  --preferred-maintenance-window sat:23:30-sun:00:30 --max-allocated-storage 100 \
  --cloudwatch-logs-export-configuration '{"EnableLogTypes":["postgresql"]}' \
  --deletion-protection --apply-immediately --query DBInstance.PendingModifiedValues
aws rds add-tags-to-resource --region $REGION --tags Key=Name,Value=saferide \
  --resource-name "$(aws rds describe-db-instances --region $REGION --db-instance-identifier "$DB_ID" \
    --query 'DBInstances[0].DBInstanceArn' --output text)"
wait_named "$DB_ID"
aws cloudformation detect-stack-resource-drift --region $REGION --stack-name saferide-backend \
  --logical-resource-id Database --query 'StackResourceDrift.[StackResourceDriftStatus,PropertyDifferences[].PropertyPath]'
```

Keep the replaced instance until the incident record is closed: swapping the
names back the same way is the way out if the restore was wrong. Then:

```bash
aws rds modify-db-instance --region $REGION --db-instance-identifier "$OLD" \
  --no-deletion-protection --apply-immediately --query DBInstance.DBInstanceStatus --output text
wait_named "$OLD"
aws rds delete-db-instance --region $REGION --db-instance-identifier "$OLD" \
  --final-db-snapshot-identifier "${OLD}-final" --query DBInstance.DBInstanceStatus --output text
```

**E. Delete the scratch instance** (rehearsal, or a restore that will not
replace production) — no final snapshot. If the dry run turned deletion
protection on, turn it off first as above; the dry run's log export leaves a
group to delete too.

```bash
aws rds delete-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --skip-final-snapshot --delete-automated-backups --query DBInstance.DBInstanceStatus --output text
aws rds wait db-instance-deleted --region $REGION --db-instance-identifier "$SCRATCH"
aws logs delete-log-group --region $REGION --log-group-name "/aws/rds/instance/$SCRATCH/postgresql"
```

**The alternative: `DBSnapshotIdentifier` in the template.** Restore to a
snapshot (or point-in-time restore, then snapshot), set `DBSnapshotIdentifier`
on `Database`, and deploy. It is not the primary path: CloudFormation
*replaces* the instance (new identifier and endpoint), which the change-set
gate refuses by design; a snapshot only offers snapshot instants, so a
point-in-time target costs a restore and a snapshot first; the old instance's
deletion protection makes the replacement's clean-up fail and leaves it
behind; and the property must then stay in the template for the life of the
stack — changing or removing it replaces the database again, with an empty
one if removed.

### Rehearsal

Before Release 1a (and the plan's R21 before Release 1b): A with
`SCRATCH=saferide-restore-test`, then the exact Release 1a property set as a
dry run on the scratch instance — an unsupported property would otherwise
roll the whole 1a update back — then B, C and E:

```bash
aws rds modify-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --backup-retention-period 35 --preferred-backup-window 23:00-23:30 \
  --preferred-maintenance-window sat:23:30-sun:00:30 --max-allocated-storage 100 \
  --cloudwatch-logs-export-configuration '{"EnableLogTypes":["postgresql"]}' \
  --deletion-protection --apply-immediately --query DBInstance.PendingModifiedValues
wait_named "$SCRATCH"
aws rds describe-db-instances --region $REGION --db-instance-identifier "$SCRATCH" \
  --query 'DBInstances[0].{deletionProtection:DeletionProtection,backupRetention:BackupRetentionPeriod,maxStorage:MaxAllocatedStorage,backupWindow:PreferredBackupWindow,maintenanceWindow:PreferredMaintenanceWindow,logExports:EnabledCloudwatchLogsExports,pending:PendingModifiedValues}'
aws rds modify-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --no-deletion-protection --apply-immediately --query DBInstance.DBInstanceStatus --output text
wait_named "$SCRATCH"
```

Before the 1a deploy the deployed verify function predates `restore-compare`
(and `hardening`), so the pre-1a rehearsal compares with `migrations`, `gps`
and `baseline <school-uuid>` per production school, picking a restore target
when nothing is running so the counts must match exactly. Repeat B and C with
`restore-compare` once after 1a and before 1b. Target: restore to first query
in under one hour.

### Before a release that rewrites data

Take a manual snapshot of the database and record its id; deploy; run the
release's `verify-db.sh` check set and compare against the rehearsal numbers
before calling it good. The decision window for a data restore is the next
morning's first run.

### Retained resources after a failed or reverted Release 1a

D13. A failed 1a update (CloudFormation rolls it back) or a rollback to
`release/0` leaves these 1a resources in the account, outside the stack,
because their `DeletionPolicy` is `Retain`:

| Resource | Name | When 1a is deployed again |
|---|---|---|
| `ApiLogGroup`, `MigrateLogGroup`, `VerifyLogGroup` | `/saferide/api`, `/saferide/migrate`, `/saferide/verify` | fixed names: the create fails ("already exists") and rolls the whole update back — delete or import them first |
| `SesIdentity` | `saferidelive.co.ke` (SES, af-south-1) | fixed name: same failure — delete or import it first |
| `TrailBucket` | `saferide-backend-trailbucket-…` (CloudFormation-named) | no clash: the next deploy creates a new bucket; the old one keeps its 90-day expiry and is left behind (delete it once empty) |

```bash
aws logs describe-log-groups --region af-south-1 --log-group-name-prefix /saferide/ \
  --query 'logGroups[].[logGroupName,storedBytes]' --output table
aws sesv2 get-email-identity --region af-south-1 --email-identity saferidelive.co.ke \
  --query '{dkim:DkimAttributes.Status,mailFrom:MailFromAttributes.MailFromDomainStatus}'
```

**Default: delete.** The SES identity's DKIM and MAIL FROM records left with
the stack, so it would drift out of verification anyway; the next deploy
re-creates it and the DKIM clock (up to 72 hours) starts again. The log groups
hold the failed release's evidence: copy the lines the incident record needs
first (for example `aws logs tail /saferide/api --region af-south-1 --since
6h > api-1a.log`; the never-log list keeps secrets and personal data out of
them).

```bash
aws sesv2 delete-email-identity --region af-south-1 --email-identity saferidelive.co.ke
for group in /saferide/api /saferide/migrate /saferide/verify; do
  aws logs delete-log-group --region af-south-1 --log-group-name "$group"
done
```

**Import instead** keeps the groups. An IMPORT change set may only add the
imported resources, so it is built from the stack's current processed
template (`aws cloudformation get-template --template-stage Processed`) plus
the three `AWS::Logs::LogGroup` resources exactly as 1a declares them (same
logical ids, `DeletionPolicy: Retain`), every stack parameter passed with
`UsePreviousValue`, and `--change-set-type IMPORT --resources-to-import`
naming each `LogGroupName`. The console's import wizard refuses SAM
templates, so it is CLI-only, and it is unrehearsed: try it on a scratch stack
first and fall back to deleting.

### Pepper rotation

The PIN pepper (`/saferide/pin-pepper`) keys every stored PIN hash, so a new
pepper invalidates every driver PIN at once; until a release can verify under
two peppers, **rotation means re-issuing every PIN**:

1. The user decides (a leaked pepper together with a database copy exposes
   every PIN) and agrees a no-run window with Kuumbai and the schools.
2. Write a new value generated inside python3, so nobody types or sees it:

   ```bash
   (
     umask 077
     WORK="$(mktemp -d)"
     python3 -c 'import json, secrets, sys; json.dump({"Name": "/saferide/pin-pepper", "Type": "SecureString", "Value": secrets.token_hex(24), "Overwrite": True}, open(sys.argv[1], "w"))' "$WORK/put.json"
     aws ssm put-parameter --region af-south-1 --cli-input-json "file://$WORK/put.json" --query Version --output text
     rm -rf "$WORK"
   )
   ```
3. Deploy the current release tag (backend, then frontend); the change set
   shows function changes only.
4. Each school office re-issues every active driver's PIN from the Drivers
   page and Kuumbai delivers them by name; `verify-db.sh hardening` →
   `drivers-without-pin` and `driver-pin-hash-versions-per-school` show the
   progress.
5. Record it. SSM keeps the previous value as an older version
   (`aws ssm get-parameter-history`), the way back if the deploy fails before
   any PIN is re-issued. A default-region copy that still exists is now stale:
   the copy helper reports a MISMATCH, and `release/0` must not be deployed.

From Release 1b new hashes carry a key id (`v2:k1:`), so a later release can
verify under two peppers (`k1`, `k2`) and retire the old one without a flag
day; until that code exists, rotation stays "re-issue every PIN". The TOTP
pepper has the same shape: every provider needs a peer TOTP reset and a fresh
enrolment ("SSM parameters" above).

### Who may run each step

| Step | Who |
|---|---|
| Decide between code rollback, data restore and pepper rotation | the user, with the operator on call; a data restore or a pepper rotation only on the user's explicit go |
| Code rollback | an AWS operator with deploy rights, after the user's go |
| Scratch restore, rehearsal, verify-function swap and its restore | an AWS operator; production is only read, and the verify function goes back in the same session |
| Rename swap | an AWS operator, on the user's explicit go |
| Delete the scratch instance | the AWS operator who made it |
| Delete the replaced instance | an AWS operator, once the incident record is closed and the user agrees |
| Retained resources: delete or import | an AWS operator |
| Pepper rotation | the user decides; an AWS operator writes and deploys; school offices re-issue PINs; Kuumbai delivers them |

### What to record

In the release's validation record, or `docs/validation/<date>-incident-<slug>.md`
for an incident: the decision time and what triggered it; the path taken and
who approved it; the restore target, `latestRestorable` and the instance ids
(scratch, restored, replaced) and snapshot ids; wall-clock times for restore →
available, available → first query, and swap start → end (or both deploys of a
code rollback); both `restore-compare` outputs with every delta explained; the
verify-function read-backs (host, then MATCH); the re-applied properties,
`PendingModifiedValues` and the drift status; the health check and the sign-ins
afterwards; who ran each step.

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
id; restoring and rolling back follow the "Restore and rollback runbook"
above (point-in-time restore and rename swap; the template's
`DBSnapshotIdentifier` only as the alternative).

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
snapshot (`DeletionPolicy: Snapshot`), the `/saferide/*` log groups, the SES
identity and the CloudTrail bucket (`DeletionPolicy: Retain`). Delete them by
hand when the teardown is meant to be complete — the commands are under
"Retained resources after a failed or reverted Release 1a"; the bucket with
`aws s3 rb s3://<TrailBucketName> --force`. The `/saferide/*` SSM parameters,
in af-south-1 and any default-region copies, are outside both stacks.
