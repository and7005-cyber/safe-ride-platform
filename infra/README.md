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

# 3. Build + deploy backend (change-set gate), run DB migrations
./scripts/deploy-backend.sh          # CONFIRM_CHANGESET=1 to answer y/N first

# 4. Build frontend against the API (Maps key + Map ID from SSM), deploy
#    S3+CloudFront + headers, probes, alarms, budget; upload, invalidate
./scripts/deploy-frontend.sh         # CSP_MODE=report-only (default) | enforce
```

`scripts/config.sh` holds the shared config (domain, zone id, regions, stack
names). Cert ARNs land in `.state/certs.env` (gitignored).

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

The first five also have older copies in the operator's default CLI region.
They stay until Release 2 is stable because `release/0`'s deploy script still
reads them there (see "Deploy safety" below); the current scripts read only
af-south-1.

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
because every migration is additive. `release/0`'s scripts still read the
default-region parameter copies, which is why those copies stay until
Release 2 is stable.

## Alarms

The backend stack sends every alarm, and its return to OK, to the SNS topic
`saferide-ops` in af-south-1 (stack output `OpsTopicArn`); SES bounce,
complaint, reject, rendering-failure and delivery-delay events land there
too. Each address exported as `ALERT_EMAIL_PRIMARY` / `ALERT_EMAIL_SECONDARY`
on a deploy gets an email subscription that must be confirmed from that
inbox. Later deploys keep the addresses: SAM reuses a parameter's previous
value when it is not passed, which holds for every override in
`deploy-backend.sh`. Thresholds are counts, not rates, and missing data
counts as OK.

| Alarm | Means | First response |
|---|---|---|
| `ApiServerErrors` | at least one API 5xx in 5 minutes | find the ERROR lines in `/saferide/api` at that time; roll back if a deploy just landed |
| `ApiClientErrorsBurst` | 50 or more API 4xx in 5 minutes | read `/saferide/api` for the paths and whether one client repeats (broken release, looping phone, sign-in probing) |
| `ApiLatencyP95` | p95 over 3 s in 3 of 5 five-minute periods | check the database CPU and credit alarms and the API function's duration |
| `ApiFunctionErrors` | the API function failed (init crash or timeout) | read the latest `/saferide/api` errors; roll back if a deploy just landed |
| `ApiFunctionThrottles` | the API function was throttled in the last minute | check `AccountConcurrency` and the pings route for a looping client; raise the quota |
| `MigrateFunctionErrors` | the migrate function failed | read `/saferide/migrate`; fix and re-run the migrate step (the live API may need it) |
| `AccountConcurrency` | Lambda concurrency at `ConcurrencyAlarmThreshold` (80% of the account limit, 8 today) | find the runaway client; request a quota increase and raise the threshold with it |
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

### Snapshot and restore (data-move releases)

Before any release that rewrites data (the tenancy data move, migration 014):

1. Take a **manual RDS snapshot** of the backend stack's database instance
   and record its id in the release's validation record.
2. Deploy, then run the release's `verify-db.sh` check set and compare
   against the rehearsal numbers before calling it good.
3. To roll back, restore the snapshot to a **new RDS instance** and point the
   stack at it (template change), then redeploy the previous code. The
   decision window is until the next morning's first run — after live runs
   have written on top, the move is the new baseline. Details per release:
   the plan's Rollback section.

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
