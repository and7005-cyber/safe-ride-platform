# SafeRide restore and rollback runbook

Hardening plan U6 (R21), moved out of [README.md](README.md) on 2026-11-18;
the deploy rules it relies on (deploy safety, the SSM parameters,
`verify-db.sh`) stay there, and the Release 1a checklists are in
[docs/runbooks/hardening-release-checklists.md](../docs/runbooks/hardening-release-checklists.md).

One bash session from `infra/`, started with the lines below; the variables
carry from block to block. No command here prints a secret: where a value is
needed it moves through a pipe or a 0600 file, and AWS calls whose answer
would contain one are cut down with `--query`. Steps B and C (and the 017
rehearsal's migrate-function swap) still make the CLI *receive* decrypted
values — the master password from the parameter store and the functions'
configuration, whose `DATABASE_URL` carries it — so
the session starts by checking that the CLI's `cli_history` is off (with it
on, the CLI stores every response in `~/.aws/cli/history`), the same check the
copy helper makes.

```bash
umask 077
export AWS_PAGER=""
aws configure get cli_history     # must print nothing or "disabled"; if "enabled":
                                  #   aws configure set cli_history disabled
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

## Decide first

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

## Code rollback

```bash
cd "$(git rev-parse --show-toplevel)"
git status --short                         # nothing modified or staged
git fetch origin --tags
git checkout release/<previous>            # detached HEAD on the tag
(cd infra && ./scripts/deploy-backend.sh)  # the tag's own script and template
(cd infra && ./scripts/deploy-frontend.sh)
git checkout -                             # back to the branch you came from
```

From `release/1a` on, the tag's own scripts carry the deploy-safety rails
(README, "Deploy safety"). The backend script refuses to deploy while a run
is open, before building and again just before executing (README,
"Open-run refusal"): roll back outside route hours, or export
`ALLOW_OPEN_RUNS=1` for an emergency rollback that cannot wait. It also
refuses while the verify function points at another database than the
stack's ("Verify-target check"). Both scripts refuse without a primary
alert address, exported or held by the stack ("Alert-address guard"), and
both review their change set: a replaced stateless resource prints a
`WARN`, anything else replaced needs `ALLOW_REPLACE` naming it, and the
database, the CloudFront distribution and the buckets are never
replaceable.

Then `curl -s https://api.saferidelive.co.ke/api/health`, one sign-in per
role, and `./scripts/verify-db.sh migrations` (a rollback never removes a
migration; the two cosmetic `015_tenancy_constraints_rls 2`/`3` rows are
expected, README "Database checks"). Record the start and end times of both
deploys.

**Rolling back Release 1a means `release/0`, whose scripts predate the
hardening:**

- No clean-checkout rule, open-run refusal, verify-target check,
  alert-address guard or change-set gate, on either stack. Its migrate step
  has no `APP_ENV` seed gate: it leaves the seed files alone only because
  their markers — `seed:001_demo_seed`, `seed:002_live_demo_seed`,
  `seed:003_local_snapshot` — are in `saferide_migrations` (the pre-1a
  baseline listed them).
- It reads the five secrets from the CLI's default region and **mints** a new
  `db-password`, `pin-pepper` or `db-app-password` when one is missing there.
  After the D15 deletion, never deploy `release/0`.
- Its template takes Release 1a's stack changes away: the alarms, the ops
  topic and subscriptions, the SES configuration set and mail records, the
  trail, the JSON logging (functions log to `/aws/lambda/*` again — check
  it, below). The database goes back to 7-day backups with deletion
  protection off, and RDS drops the automated backups older than 7 days at
  once, so the restore window shrinks to 7 days (the manual snapshots stay).
  Whether the backup and maintenance windows and the storage limit go back
  as well is not asserted: read them back afterwards and record them.
  Migration 017's objects stay: they are additive, every new column is
  nullable or has a default, and `release/0`'s code never reads them.
- It removes the sign-in and school-lookup routes and their route settings
  from the HTTP API. That removal has never been rehearsed (see "A rollback
  that cannot finish" below).
- Its frontend build reads `frontend/.env.local` (the development Maps key
  and the demo Map ID), and its frontend stack drops the headers policy, the
  probes, the us-east-1 alarms and the budget.
- The `/saferide/*` log groups, the SES identity and the trail bucket stay
  behind: see "Retained resources" below before deploying 1a again.

**Immediately before the `release/0` deploy**, in the shell that will
deploy, from the current checkout — `release/0` has no open-run refusal and
no gate, so these reads are the guard:

1. `./scripts/verify-db.sh hardening` → `open-runs-and-today` shows
   `open_runs` 0 and `created_today_nairobi` 0, and
   `./scripts/verify-db.sh migrations` still lists the three seed markers.
2. `./scripts/copy-ssm-to-backend-region.sh` prints five MATCH lines: it
   resolves the source region the way `release/0`'s script will
   (`AWS_REGION`, `AWS_DEFAULT_REGION`, the configured region), so they
   prove the copies that script reads exist and equal af-south-1's.
3. A fresh manual snapshot, kept for at least 35 days — the 28 days of
   automated backups beyond 7 are gone the moment the rollback lands:

   ```bash
   SNAP="saferide-pre-rollback-$(date -u +%Y%m%d-%H%M)"
   aws rds create-db-snapshot --region $REGION --db-instance-identifier "$DB_ID" \
     --db-snapshot-identifier "$SNAP" --query DBSnapshot.Status --output text
   aws rds wait db-snapshot-available --region $REGION --db-snapshot-identifier "$SNAP"
   ```

**After any rollback of Release 1a** — a deliberate `release/0` deploy or a
failed 1a update that CloudFormation rolled back — check how the three
functions log. `release/0`'s code has no `httpx` pin (D20): if it runs with
JSON logging at INFO, the geocoder's request URLs, with the server Maps key
and searched home addresses, reach the logs.

```bash
for key in ApiFunctionName MigrateFunctionName VerifyFunctionName; do
  fn="$(aws cloudformation describe-stacks --region $REGION --stack-name saferide-backend \
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text)"
  aws lambda get-function-configuration --region $REGION --function-name "$fn" \
    --query '[FunctionName,LoggingConfig.LogFormat,LoggingConfig.ApplicationLogLevel,LoggingConfig.LogGroup]' --output text
done
```

A function showing `JSON` with `INFO` (or `DEBUG`, `TRACE`) is set to WARN
at once, as part of the rollback, keeping the log group it printed:

```bash
aws lambda update-function-configuration --region $REGION --function-name <FunctionName> \
  --logging-config 'LogFormat=JSON,ApplicationLogLevel=WARN,SystemLogLevel=INFO,LogGroup=<the group printed>' \
  --query LoggingConfig
```

Then read back the database's windows and storage limit and record them
(the describe command of the
[Release 1a deploy checklist](../docs/runbooks/hardening-release-checklists.md#release-1a-deploy-checklist),
"Database properties").

**A rollback that cannot finish.** If a rollback stops in
`UPDATE_ROLLBACK_FAILED` — the never-rehearsed removal of the HTTP API's
routes and route settings is the likely place — find the failed resource
and, on the user's go, continue the rollback skipping it (the API stage),
then record which resource was skipped:

```bash
aws cloudformation describe-stack-events --region $REGION --stack-name saferide-backend --max-items 40 \
  --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[Timestamp,LogicalResourceId,ResourceType,ResourceStatusReason]" \
  --output table
aws cloudformation continue-update-rollback --region $REGION --stack-name saferide-backend \
  --resources-to-skip <LogicalResourceId of the stage>
```

**Deploying 1a again after a rollback.** Delete or import the retained
resources first ("Retained resources" below). Re-export
`ALERT_EMAIL_PRIMARY` and `ALERT_EMAIL_SECONDARY`,
`CONCURRENCY_ALARM_THRESHOLD` if the quota is granted, any `MAIL_*` or
`API_*` throttle override and `MONTHLY_BUDGET_USD`: the 1a-only parameters
left the stacks with the rollback, so there is no previous value to keep,
and the alert-address guard refuses without the primary address. Four
confirmation mails arrive again (operator checklist, step 7).

## Data restore: point-in-time into a scratch instance

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

The host printed must be the scratch endpoint. `verify-db.sh` now refuses,
because the verify function's host is no longer the stack's `DbEndpoint`
(README, "Verify-target check"); here that is the point, so each read
against the restore names `ALLOW_VERIFY_TARGET_MISMATCH=1` for that one
command — never export it:

```bash
ALLOW_VERIFY_TARGET_MISMATCH=1 ./scripts/verify-db.sh restore-compare "$RESTORE_TS"   # from Release 1a on
ALLOW_VERIFY_TARGET_MISMATCH=1 ./scripts/verify-db.sh migrations
ALLOW_VERIFY_TARGET_MISMATCH=1 ./scripts/verify-db.sh gps
ALLOW_VERIFY_TARGET_MISMATCH=1 ./scripts/verify-db.sh baseline <school-uuid>   # each production school (ids: tenancy-preflight → school-rows)
date -u +%FT%TZ                                        # first query -> record
```

Do not deploy while the verify function points at the scratch instance
(`deploy-backend.sh` refuses unless overridden), and do not end the session
before step C.

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

The read-back must print `MATCH`. The production reads run without the
override, so their passing the verify-target check is the second proof that
the function is back.

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
`MaxAllocatedStorage`, the backup retention, the backup and maintenance
windows or the `Name` tag. **Re-apply them by hand** — a deploy does not:
CloudFormation compares the new template with the last one it applied, not
with the live instance, so an unchanged `Database` resource gets no update.
Then confirm with the database-properties describe command in the
[Release 1a deploy checklist](../docs/runbooks/hardening-release-checklists.md#release-1a-deploy-checklist)
and a drift check (`IN_SYNC` expected):

```bash
aws rds modify-db-instance --region $REGION --db-instance-identifier "$DB_ID" \
  --backup-retention-period 35 --preferred-backup-window 23:00-23:30 \
  --preferred-maintenance-window sun:00:00-sun:01:00 --max-allocated-storage 100 \
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
protection on, turn it off first as above.

```bash
aws rds delete-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --skip-final-snapshot --delete-automated-backups --query DBInstance.DBInstanceStatus --output text
aws rds wait db-instance-deleted --region $REGION --db-instance-identifier "$SCRATCH"
```

**The alternative: `DBSnapshotIdentifier` in the template.** Restore to a
snapshot (or point-in-time restore, then snapshot), set `DBSnapshotIdentifier`
on `Database`, and deploy. It is not the primary path: CloudFormation
*replaces* the instance (new identifier and endpoint), which the change-set
gate refuses by design, and `ALLOW_REPLACE` cannot override for a database
(`AWS::RDS::DBInstance`); a snapshot only offers snapshot instants, so a
point-in-time target costs a restore and a snapshot first; the old instance's
deletion protection makes the replacement's clean-up fail and leaves it
behind; and the property must then stay in the template for the life of the
stack — changing or removing it replaces the database again, with an empty
one if removed.

## Rehearsal

Before Release 1a (and the plan's R21 before Release 1b): A with
`SCRATCH=saferide-restore-test`, then the exact Release 1a property set as a
dry run on the scratch instance — an unsupported property would otherwise
roll the whole 1a update back — then B and its comparison, then (before
Release 1a) "017 on the production copy" below, then C and E:

```bash
aws rds modify-db-instance --region $REGION --db-instance-identifier "$SCRATCH" \
  --backup-retention-period 35 --preferred-backup-window 23:00-23:30 \
  --preferred-maintenance-window sun:00:00-sun:01:00 --max-allocated-storage 100 \
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

**017 on the production copy** (before Release 1a, on the user's explicit
go). Migration 017 widens a CHECK over the audit rows production holds and
gives every existing school a driver code; the rehearsal applies it to the
scratch copy through the migrate function, after B's comparison (which
must see the copy without 017) and before C. The migrate function is
pointed at the scratch instance the way B points the verify function, then
put back and read back.

The migrate function production runs today is `release/0`'s package:
migrations up to 016 and no `APP_ENV` seed gate. Invoked as it stands it
answers `migrations.applied: []`, which proves nothing. For the rehearsal
it must run 1a's migrate package (the `MigrateFunction` build of the
`release/1a` commit), which means changing a production function's code
outside CloudFormation for the length of the rehearsal. **Open — agree it
with the user before the rehearsal is scheduled:** how that package reaches
the function and how the original goes back (for example, `sam package` of
the `release/1a` build and `aws lambda update-function-code` pointing at
its `MigrateFunction` object; back at the object the stack's processed
template names). Whatever the means, read the code hash before, after the
change and after the restore; the last must equal the first:

```bash
MIGRATE_FN="$(aws cloudformation describe-stacks --region $REGION --stack-name saferide-backend \
  --query "Stacks[0].Outputs[?OutputKey=='MigrateFunctionName'].OutputValue" --output text)"
aws lambda get-function-configuration --region $REGION --function-name "$MIGRATE_FN" \
  --query '[CodeSha256,LastModified]' --output text      # record
```

Point it at the scratch instance — B's procedure for the migrate function
(its configuration also carries the app-role password, so the saved copy
stays in the private directory until the restore):

```bash
MWORK="$(mktemp -d)"
python3 - "$MWORK" "$SCRATCH_HOST" \
  <(aws lambda get-function-configuration --region $REGION --function-name "$MIGRATE_FN" --output json) \
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
aws lambda update-function-configuration --region $REGION --cli-input-json "file://$MWORK/scratch.json" \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --region $REGION --function-name "$MIGRATE_FN"
aws lambda get-function-configuration --region $REGION --function-name "$MIGRATE_FN" \
  --query Environment.Variables.DATABASE_URL --output text \
  | python3 -c 'import sys; from urllib.parse import urlsplit; print("migrate DATABASE_URL host:", urlsplit(sys.stdin.read().strip()).hostname)'
```

The host printed must be the scratch endpoint. A configuration change and
a code change cannot overlap: wait for `function-updated` after each. Do
not deploy, and do not end the session, while the migrate function points
at the scratch instance — a deploy would migrate the scratch, not
production. Then apply and time it:

```bash
date -u +%FT%TZ                            # invoke start -> record
aws lambda invoke --region $REGION --function-name "$MIGRATE_FN" --cli-read-timeout 0 \
  --cli-binary-format raw-in-base64-out --payload '{}' "$MWORK/migrate-out.json" \
  --query FunctionError --output text      # None = no function error
date -u +%FT%TZ                            # invoke end -> record
cat "$MWORK/migrate-out.json"; echo
ALLOW_VERIFY_TARGET_MISMATCH=1 ./scripts/verify-db.sh migrations   # now lists 017_account_hardening
```

Require `status` `ok`, `migrations.applied` exactly
`["017_account_hardening"]` and the seed files skipped; record the
duration, well inside the function's 120-second timeout. Put the
configuration back and read it back (`MATCH`); then put the code back by
the agreed means and read the hash with the first command — it must equal
the one recorded before:

```bash
aws lambda update-function-configuration --region $REGION --cli-input-json "file://$MWORK/original.json" \
  --query LastUpdateStatus --output text
aws lambda wait function-updated --region $REGION --function-name "$MIGRATE_FN"
aws lambda get-function-configuration --region $REGION --function-name "$MIGRATE_FN" --output json \
  | python3 -c 'import hashlib, json, sys; d = lambda v: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest(); live = json.load(sys.stdin)["Environment"]["Variables"]; saved = json.load(open(sys.argv[1]))["Environment"]["Variables"]; print("migrate environment restored:", "MATCH" if d(live) == d(saved) else "MISMATCH")' "$MWORK/original.json"
rm -rf "$MWORK"
```

The scratch instance keeps 017 until E deletes it.

## Before a release that rewrites data

Take a manual snapshot of the database and record its id; deploy; run the
release's `verify-db.sh` check set and compare against the rehearsal numbers
before calling it good. The decision window for a data restore is the next
morning's first run.

## Retained resources after a failed or reverted Release 1a

D13. The fixed-name 1a resources carry `DeletionPolicy: RetainExceptOnCreate`:
if the 1a update itself fails and CloudFormation rolls it back, the log groups
and the SES identity it had just created are deleted with the rollback. That
does not always leave nothing to clean up: a function invoked between that
deletion and the end of the rollback can make Lambda create its `/saferide/*`
group again, outside the stack. After a failed 1a, run the
`describe-log-groups` command below and delete any `/saferide/*` group
before the retry. A deliberate rollback to `release/0` (an update that
removes them) or a stack delete keeps them all, outside the stack:

| Resource | Name | When 1a is deployed again after a rollback to `release/0` |
|---|---|---|
| `ApiLogGroup`, `MigrateLogGroup`, `VerifyLogGroup` | `/saferide/api`, `/saferide/migrate`, `/saferide/verify` | fixed names: the create fails ("already exists") and rolls the whole update back — delete or import them first |
| `SesIdentity` | `saferidelive.co.ke` (SES, af-south-1) | fixed name: same failure — delete or import it first |
| `TrailBucket` | `saferide-backend-trailbucket-…` (CloudFormation-named, plain `Retain`: a bucket holding objects cannot be deleted during a rollback) | no clash: the next deploy creates a new bucket; the old one keeps its 90-day expiry and is left behind after any rollback (delete it once empty) |

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

## Pepper rotation

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
   page and Kuumbai delivers them by name; `verify-db.sh hardening` shows the
   progress: `driver-pin-hash-versions-per-school` (its `no_pin` column
   counts active drivers with no PIN at all) and
   `driver-last-sign-in-per-school` (a sign-in after the rotation proves that
   driver's new PIN).
5. Record it. SSM keeps the previous value as an older version
   (`aws ssm get-parameter-history`), the way back if the deploy fails before
   any PIN is re-issued. A default-region copy that still exists is now stale:
   the copy helper reports a MISMATCH, and `release/0` must not be deployed.

From Release 1b new hashes carry a key id (`v2:k1:`), so a later release can
verify under two peppers (`k1`, `k2`) and retire the old one without a flag
day; until that code exists, rotation stays "re-issue every PIN". The TOTP
pepper has the same shape: every provider needs a peer TOTP reset and a fresh
enrolment (README, "SSM parameters").

## Who may run each step

| Step | Who |
|---|---|
| Decide between code rollback, data restore and pepper rotation | the user, with the operator on call; a data restore or a pepper rotation only on the user's explicit go |
| Code rollback | an AWS operator with deploy rights, after the user's go |
| Scratch restore, rehearsal, verify-function swap and its restore | an AWS operator; production is only read, and the verify function goes back in the same session |
| 017 on the production copy (migrate-function code and configuration swap) | an AWS operator, on the user's explicit go; the function goes back, read back, in the same session |
| A rollback's logging fix; `continue-update-rollback` | an AWS operator, within the rollback the user approved; skipping a resource on the user's go |
| Rename swap | an AWS operator, on the user's explicit go |
| Delete the scratch instance | the AWS operator who made it |
| Delete the replaced instance | an AWS operator, once the incident record is closed and the user agrees |
| Retained resources: delete or import | an AWS operator |
| Pepper rotation | the user decides; an AWS operator writes and deploys; school offices re-issue PINs; Kuumbai delivers them |

## What to record

In the release's validation record, or `docs/validation/<date>-incident-<slug>.md`
for an incident: the decision time and what triggered it; the path taken and
who approved it; the restore target, `latestRestorable` and the instance ids
(scratch, restored, replaced) and snapshot ids; wall-clock times for restore →
available, available → first query, and swap start → end (or both deploys of a
code rollback); both `restore-compare` outputs with every delta explained; the
verify-function read-backs (host, then MATCH); for the 017 rehearsal, the
migrate result, its duration, the migrate-function read-backs (host, MATCH)
and the three code hashes; the re-applied properties,
`PendingModifiedValues` and the drift status; for a rollback, the pre-rollback
snapshot id, the functions' logging configuration and the database windows
read back; the health check and the sign-ins afterwards; who ran each step.
