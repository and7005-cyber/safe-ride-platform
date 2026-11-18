# Production hardening — release checklists

The operator checklist before Release 1a and the Release 1a deploy checklist
(hardening plan U1 and U6, `docs/plans/2026-11-16-001-feat-production-hardening-plan.md`,
as amended by the execution decisions recorded in
[the Release 1a validation record](../validation/2026-11-25-hardening-release-1a.md)).
The durable deploy rules — deploy order, the SSM parameters, deploy safety,
alarms, security headers, `verify-db.sh` — are in
[infra/README.md](../../infra/README.md); restoring and rolling back are in
[infra/RESTORE.md](../../infra/RESTORE.md).

Run every command from `infra/` in bash with credentials for the production
account. None of them prints a secret.

## Operator checklist before Release 1a

Hardening plan U1. Start these the day the plan is approved: three of them
wait on AWS or Google. Each step says what to do, what proves it and who does
it; paste the proof into the Release 1a validation record
(`docs/validation/2026-11-25-hardening-release-1a.md`).

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
     (8). Without the export the deploy passes 8 again (infra/README.md,
     "Live-parity parameters").

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
     `AWS_DEFAULT_REGION`, else `aws configure get region`; the header line
     `==> /saferide/* secrets: source <region> -> target af-south-1` prints
     which, and `SOURCE_REGION` overrides all three. It refuses to run while
     the CLI's `cli_history` is enabled, since the CLI would then store the
     decrypted values on disk.

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
   - *Proves it:* the third run's five `present in both, values MATCH` lines
     and its header line (the source region), pasted into the record.
   - *Afterwards:* keep the source-region copies until one week after
     Release 2 is stable — `release/0`'s deploy script reads them there —
     then delete them from the region the helper's header printed (recorded
     with the proof). From then on `release/0` must never be deployed: its
     script would mint new secrets.

     ```bash
     SOURCE_REGION=<the source region the copy helper printed>
     for name in /saferide/db-password /saferide/pin-pepper /saferide/db-app-password \
                 /saferide/totp-pepper /saferide/google-maps-api-key; do
       aws ssm delete-parameter --region "$SOURCE_REGION" --name "$name"
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
     confirmation (note it in the record). Changing an address later replaces
     its subscription (and, on the frontend, the budget) and needs a fresh
     confirmation: infra/README.md, "Alarms".
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
     outside 02:30–16:00 UTC (05:30–19:00 EAT, route hours). A run counts as
     open until it is completed, whatever day it started: one left open on an
     earlier day must be completed before the deploy.
   - *Proves it:* no open run and none created today, read just before the
     deploy. Before Release 1a the deployed verify function predates the
     `hardening` set, so read `open-runs` from `./scripts/verify-db.sh
     tenancy-preflight` (0). From Release 1a on, `./scripts/verify-db.sh
     hardening` → `open-runs-and-today` shows `open_runs` 0 and
     `created_today_nairobi` 0. Record the time of the check.
     `deploy-backend.sh` reads the same `tenancy-preflight` → `open-runs` row
     itself before building and refuses the deploy when it is above 0 or
     cannot be read (infra/README.md, "Open-run refusal"); `ALLOW_OPEN_RUNS`
     stays unset for the release deploys.
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
   ([infra/RESTORE.md](../../infra/RESTORE.md), "Rehearsal").
5. The live parity values are read. From this deploy on every parity
   parameter is passed explicitly, so a live value an earlier one-off export
   left behind returns to the template Default unless it is exported again:

   ```bash
   aws cloudformation describe-stacks --region af-south-1 --stack-name saferide-backend \
     --query 'Stacks[0].Parameters[].[ParameterKey,ParameterValue]' --output table
   aws cloudformation describe-stacks --region us-east-1 --stack-name saferide-frontend \
     --query 'Stacks[0].Parameters[].[ParameterKey,ParameterValue]' --output table
   ```

   Backend: `GpsCustodyThresholdM` 150, `GpsVicinityRadiusM` 100,
   `GpsFixAccuracyCapM` 200, `GpsPositionRetentionDays` 90,
   `GpsPingIntervalS` 10, `GpsStaleAfterS` 90, `GpsFixWaitBudgetS` 5,
   `ApiDefaultBurstLimit` 100, `ApiDefaultRateLimit` 50,
   `PingsRouteBurstLimit` 20 and `PingsRouteRateLimit` 10 are the Defaults
   (secret parameters show `****`; the sign-in and school-lookup throttles,
   the mail settings and the alarm parameters are new in 1a). Frontend:
   `DomainName` and `HostedZoneId` follow `scripts/config.sh`; `ApiOrigin`,
   `CspMode` and `MonthlyBudgetUsd` (60) are new in 1a. Export any other
   value you mean to keep, and record why.
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
   `ALLOW_OPEN_RUNS`, `ALLOW_REPLACE` and `ALLOW_UNRELEASED_DEPLOY` are
   unset (`env | grep '^ALLOW_'` prints nothing).

**Deploy, in order:**

1. **Backend** — `CONFIRM_CHANGESET=1 ./scripts/deploy-backend.sh`. Watch for:
   - `==> Clean checkout: HEAD <sha> is tagged release/1a`, and no
     `ERROR: SSM …` line (all five secrets and the provider bootstrap read
     from af-south-1);
   - `==> Parameter override:` lines for the exported values and one
     `==> Live-parity parameters at their template Default: …` line;
   - `==> Checking for open runs …` followed by `open-runs: 0` (the deploy
     stops here otherwise);
   - the change-set table. Expected among the rows: `Modify Database`
     with replacement `False` and the changed properties
     `BackupRetentionPeriod`, `DeletionProtection`, `MaxAllocatedStorage`,
     `PreferredBackupWindow`, `PreferredMaintenanceWindow` — and never
     `MasterUserPassword`; `Add` rows for the log groups, the ops topic and
     subscriptions, the alarms, the SES resources and records and the trail
     with its bucket. The gate itself aborts (and deletes the change set) on
     `MasterUserPassword` or on any `Replacement: True` other than the
     stateless types (SNS subscription, alarm, metric filter), which print a
     `WARN`. Everything new in 1a is an `Add`, so no `WARN` is expected:
     answer `y` only when there is no `WARN` line and no `Remove` row you
     cannot explain;
   - `==> Stack saferide-backend deployed` (the database changes can take
     several minutes);
   - the migrate result: `migrations.applied` is `["017_account_hardening"]`,
     `seeds` is `{"skipped": "APP_ENV=production", "files":
     ["001_demo_seed.sql", "002_live_demo_seed.sql",
     "003_local_snapshot.sql"]}`, and `app_role.synced` is `true`.
2. **Frontend** — `CSP_MODE=report-only ./scripts/deploy-frontend.sh`
   (report-only is also the default): no `ERROR: SSM …` line for the two Maps
   values; `==> Parameter override:` lines for `DomainName`, `HostedZoneId`,
   `ApiOrigin`, `CspMode=report-only` and the two addresses, and
   `MonthlyBudgetUsd=60` among the Defaults; the build passes, the stack
   deploys, and the invalidation id is printed.

**After the deploy:**

- `./scripts/verify-db.sh --strict migrations` exits 0 and lists
  `017_account_hardening`. It also lists the two cosmetic marker rows
  `015_tenancy_constraints_rls 2` and `015_tenancy_constraints_rls 3`
  (2026-09-20): expected (infra/README.md, "Database checks").
- `./scripts/verify-db.sh --strict hardening` exits 0, so no row carries an
  `error`: `017-objects-present` is `true` on all fifteen rows,
  `audit-check-widening-017` all `true`, `schools-without-driver-code` 0.
  Record `instance-settings` (`max_connections`, the server version): the
  deferred `DatabaseConnections` alarm takes its threshold from it.
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

  Expect `true`, 35, 100, `23:00-23:30`, `sun:00:00-sun:01:00` (Sunday
  03:00–04:00 EAT, D22), `logExports` `null` (no PostgreSQL log export, D21)
  and an empty `pending`.
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
- 90-day retention by hand on the three old `/aws/lambda/*` groups (the
  three commands in infra/README.md, "Alarms" → "Logs and retention by
  hand"), then confirm:
  `aws logs describe-log-groups --region af-south-1 --log-group-name-prefix
  /aws/ --query 'logGroups[].[logGroupName,retentionInDays]' --output table`.
- Fill the record's "Deployed" section.
