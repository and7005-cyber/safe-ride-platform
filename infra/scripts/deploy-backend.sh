#!/usr/bin/env bash
# Build + deploy the SafeRide backend SAM stack to af-south-1, then run the
# database migrations via the migrate Lambda (seeds apply only locally).
#
# Safety rails (infra/README.md, "Deploy safety"):
# - the checkout must be committed and released (require_clean_checkout in
#   config.sh), and nothing untracked under backend/app or backend/db may be
#   packaged;
# - every /saferide/* parameter is read from $BACKEND_REGION and a missing one
#   aborts the deploy: this script never generates a secret;
# - the alarms must email someone: ALERT_EMAIL_PRIMARY is exported, or the
#   stack holds an AlertEmailPrimary, which is then passed explicitly
#   (alert_address_guard, parity-params.sh). ALLOW_NO_ALERTS=1 deploys without
#   one, with a loud warning;
# - no run may be open: before anything is built, and again just before the
#   change set executes, the deployed verify Lambda's tenancy-preflight
#   `open-runs` count must be 0. ALLOW_OPEN_RUNS=1 deploys anyway, with a loud
#   warning. The verify Lambda must read this stack's database (its
#   DATABASE_URL host is the DbEndpoint output: verify_target_guard in
#   config.sh), not a scratch restore; ALLOW_VERIFY_TARGET_MISMATCH=1 waives
#   that, with a loud warning;
# - the stack change set is reviewed before it executes
#   (scripts/changeset_gate.py): a master-password change on the database, or
#   the replacement of anything but a stateless type (SNS subscription, alarm,
#   metric filter), aborts it. ALLOW_REPLACE=<LogicalId,...> lets the named
#   resources be replaced on this deploy, with a loud warning (never the
#   database or an S3 bucket). CONFIRM_CHANGESET=1 also asks the operator y/N
#   before executing;
# - every live-parity parameter with a template Default is passed explicitly
#   (scripts/parity-params.sh), so the live stack runs the template Default
#   unless this deploy exports an override.
#
# Pass the API cert ARN via env (API_CERT_ARN=...) or infra/.state/certs.env to
# enable the custom domain.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
source "$(dirname "${BASH_SOURCE[0]}")/parity-params.sh"

require_clean_checkout

# Refuse to package migration/seed files git does not track: sam bundles the
# DIRECTORY, and a stray editor or file-sync duplicate ("015_… 2.sql") would
# apply to production as an unmarked migration. Idempotent files survive that
# by design — anything else must never get the chance.
for dir in "$REPO_DIR/backend/db/migrations" "$REPO_DIR/backend/db/seeds"; do
  strays="$(cd "$REPO_DIR" && comm -13 \
    <(git ls-files "${dir#"$REPO_DIR"/}" | xargs -n1 basename | sort) \
    <(ls "$dir" | sort))"
  if [ -n "$strays" ]; then
    echo "ERROR: untracked files in $dir would deploy as migrations/seeds:" >&2
    echo "$strays" >&2
    exit 1
  fi
done
# The same rule for everything else the Lambda packages from backend/app and
# backend/db: an untracked, non-ignored file there would ship unreviewed.
# Ignored files (__pycache__, .env) are out of scope by construction.
untracked="$(git -C "$REPO_DIR" ls-files --others --exclude-standard backend/app backend/db)"
if [ -n "$untracked" ]; then
  echo "ERROR: untracked files under backend/app or backend/db would be packaged into the Lambda:" >&2
  echo "$untracked" >&2
  echo "Commit them or move them out of the tree before deploying." >&2
  exit 1
fi

APP_VERSION="$(git -C "$REPO_DIR" rev-parse --short HEAD)"

# Load cert ARNs if request-certs.sh has run.
[ -f "$STATE_DIR/certs.env" ] && source "$STATE_DIR/certs.env"
API_CERT_ARN="${API_CERT_ARN:-}"

# Every parameter below flows through a CloudFormation parameter into the
# functions' environment. Each must already exist in $BACKEND_REGION: a missing
# one aborts the deploy, because a minted DB password, pepper or key would lock
# the API out of the database or invalidate every PIN and second factor. The
# value is never printed. ssm_required lives in config.sh (shared with
# deploy-frontend.sh); a failed read aborts here under set -e.
DB_PASSWORD="$(ssm_required /saferide/db-password)"
PIN_PEPPER="$(ssm_required /saferide/pin-pepper)"
DB_APP_PASSWORD_VAL="$(ssm_required /saferide/db-app-password)"
# Tenancy (U2): a silently re-minted TOTP pepper would re-enrol every
# provider's second factor; scripts/provider-bootstrap.sh creates it once.
TOTP_PEPPER_VAL="$(ssm_required /saferide/totp-pepper)"
GOOGLE_MAPS_KEY="$(ssm_required /saferide/google-maps-api-key)"

# The provider bootstrap must PRE-EXIST too — a minted bootstrap would insert
# garbage accounts. Create it with scripts/provider-bootstrap.sh.
PROVIDER_BOOTSTRAP_RAW="$(ssm_required /saferide/provider-bootstrap)" || {
  echo "       (if it is missing, create it with scripts/provider-bootstrap.sh)" >&2
  exit 1
}
if ! printf '%s' "$PROVIDER_BOOTSTRAP_RAW" | python3 -c "
import base64, json, sys
raw = sys.stdin.read().strip()
if not raw.startswith('{'):
    raw = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode()
data = json.loads(raw)
assert isinstance(data['version'], int) and data['providers']
" 2>/dev/null; then
  echo "ERROR: SSM /saferide/provider-bootstrap is not parseable bootstrap JSON. Re-run scripts/provider-bootstrap.sh." >&2
  exit 1
fi

# Push secrets: prefer SSM; seed each from backend/.env when it is missing there
# (they are provisioned once — Firebase service account, web config, VAPID
# pair). Empty is fine: the code degrades (FCM off / web push off) and the
# in-app feed still works. A read error other than ParameterNotFound aborts the
# deploy: it is never taken for "missing", seeded over or deployed as empty.
ssm_seeded() { # ssm_name env_key -> value ("" if neither SSM nor .env has it)
  # Pinned to BACKEND_REGION (ssm_lookup, config.sh): the two Firebase JSON
  # params are read from SSM by the Lambda at runtime (in BACKEND_REGION), so
  # they must live there — not in the operator's default CLI region.
  local name="$1" env_key="$2" val rc=0 dir
  val="$(ssm_lookup "$name")" || rc=$?
  case "$rc" in
    0) printf '%s' "$val"; return 0 ;;
    3) ;;
    *) return 1 ;;
  esac
  # Missing in SSM: python3 reads the value from backend/.env and writes the
  # put-parameter input (Name, Value, Type SecureString — no Overwrite, so a
  # parameter created meanwhile is never replaced) into a 0600 file in a
  # private directory: the value never reaches an argv. Exit 3: .env has no
  # value either.
  dir="$(mktemp -d)"
  rc=0
  python3 - "$REPO_DIR/backend/.env" "$env_key" "$name" "$dir/put.json" <<'PY' || rc=$?
import json
import os
import sys

env_path, key, name, out = sys.argv[1:5]
try:
    with open(env_path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
except OSError:
    sys.exit(3)
prefix = key + "="
value = next((line[len(prefix):] for line in lines if line.startswith(prefix)), "")
if not value:
    sys.exit(3)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump({"Name": name, "Value": value, "Type": "SecureString"}, handle)
PY
  if [ "$rc" -eq 3 ]; then
    rm -rf "$dir"
    return 0
  fi
  if [ "$rc" -ne 0 ] || ! aws ssm put-parameter --region "$BACKEND_REGION" \
      --cli-input-json "file://$dir/put.json" --output json >/dev/null; then
    rm -rf "$dir"
    echo "ERROR: could not seed SSM $name ($BACKEND_REGION) from backend/.env" >&2
    return 1
  fi
  rm -rf "$dir"
  echo "==> Seeded SSM $name ($BACKEND_REGION) from backend/.env" >&2
  # Re-read: the stack gets what SSM holds.
  val="$(ssm_lookup "$name")" || {
    echo "ERROR: SSM $name ($BACKEND_REGION) is not readable after seeding it" >&2
    return 1
  }
  printf '%s' "$val"
}

FIREBASE_SA_JSON="$(ssm_seeded /saferide/firebase-service-account-json FIREBASE_SERVICE_ACCOUNT_JSON)"
FIREBASE_WEB_JSON="$(ssm_seeded /saferide/firebase-web-config-json FIREBASE_WEB_CONFIG_JSON)"
FIREBASE_VAPID="$(ssm_seeded /saferide/firebase-vapid-key FIREBASE_VAPID_KEY)"
VAPID_PUB="$(ssm_seeded /saferide/vapid-public-key VAPID_PUBLIC_KEY)"
VAPID_PRIV="$(ssm_seeded /saferide/vapid-private-key VAPID_PRIVATE_KEY)"
VAPID_SUBJ="$(ssm_seeded /saferide/vapid-subject VAPID_SUBJECT)"
if [ -z "$FIREBASE_SA_JSON" ] && [ -z "$VAPID_PRIV" ]; then
  echo "WARN: no push credentials in SSM or backend/.env — push delivery stays simulated (feed still works)." >&2
fi

# --- Stack parameters ----------------------------------------------------------
# Assembled before the build, so a template the parity reader cannot parse stops
# the deploy before anything is built.
DEPLOY_PARAMS=("DbMasterPassword=${DB_PASSWORD}" "PinPepper=${PIN_PEPPER}" "DbAppPassword=${DB_APP_PASSWORD_VAL}" "TotpPepper=${TOTP_PEPPER_VAL}" "GoogleMapsApiKey=${GOOGLE_MAPS_KEY}" "AppVersion=${APP_VERSION}")
# SAM rejects empty Key= overrides, so only pass the cert ARN when we have one.
[ -n "$API_CERT_ARN" ] && DEPLOY_PARAMS+=("ApiCertificateArn=${API_CERT_ARN}")
# Push params: only pass non-empty (SAM rejects empty Key= overrides; the
# template Default "" applies when omitted). VapidSubject has a template default.
# The two Firebase JSON blobs are NOT passed as parameters — they are read from
# SSM at runtime by the app (too large for the 4 KB Lambda env once encoded, and
# CFN's --parameter-overrides shorthand mangles JSON). ssm_seeded above already
# ensured /saferide/firebase-{service-account,web-config}-json exist in SSM.
[ -n "$FIREBASE_VAPID" ] && DEPLOY_PARAMS+=("FirebaseVapidKey=${FIREBASE_VAPID}")
[ -n "$VAPID_PUB" ] && DEPLOY_PARAMS+=("VapidPublicKey=${VAPID_PUB}")
[ -n "$VAPID_PRIV" ] && DEPLOY_PARAMS+=("VapidPrivateKey=${VAPID_PRIV}")
[ -n "$VAPID_SUBJ" ] && DEPLOY_PARAMS+=("VapidSubject=${VAPID_SUBJ}")
# Live-parity parameters (scripts/parity-params.sh: PARITY_PAIRS holds the GPS,
# API-throttle, mail and alarm parameters). SAM sends UsePreviousValue for any
# parameter a deploy does not pass, so each one with a template Default is
# passed on every deploy: the exported variable under the name the app reads
# (e.g. GPS_CUSTODY_THRESHOLD_M=200), otherwise the template Default. An export
# lasts for that deploy only — repeat it, or change the Default in a release.
# The alert addresses and MailReplyTo have empty Defaults: passed only when
# exported (SAM rejects an empty override), otherwise the stack keeps its value.
build_parity_params "$INFRA_DIR/backend/template.yaml" "${PARITY_PAIRS[@]}"
# No deploy may leave the alarms emailing no one (hardening E1).
alert_address_guard "$BACKEND_STACK" "$BACKEND_REGION" || exit 1

# --- Open-run guard ------------------------------------------------------------
# A deploy swaps the API code and migrates the database under whatever runs are
# in progress. Before anything is built, and again just before the change set
# executes (a run may start during the build), the verify Lambda the stack has
# today answers its tenancy-preflight set, whose `open-runs` row counts
# live_runs not yet completed; more than 0 refuses the deploy, and so does a
# stack without a verify function or an answer without that row.
# ALLOW_OPEN_RUNS=1 deploys anyway with a loud warning (an emergency fix, or the
# very first deploy). Only the count is printed: the set's other rows are not
# this script's business. A count is only worth reading from this stack's
# database: verify_target_guard (config.sh) refuses first when the verify
# function still points at a scratch restore.
open_runs_check() { # verify function name ("" if none) -> prints a run count, or the reason no count was read
  local fn="$1" fn_error out
  if [ -z "$fn" ]; then
    echo "stack $BACKEND_STACK ($BACKEND_REGION) has no VerifyFunctionName output"
    return 0
  fi
  out="$(mktemp)"
  if ! fn_error="$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$fn" \
      --cli-read-timeout 0 --cli-binary-format raw-in-base64-out --payload '{"checks": "tenancy-preflight"}' \
      "$out" --query FunctionError --output text)"; then
    rm -f "$out"
    echo "could not invoke $fn (see the AWS error above)"
    return 0
  fi
  if [ "$fn_error" != "None" ] && [ -n "$fn_error" ]; then
    rm -f "$out"
    echo "$fn failed ($fn_error)"
    return 0
  fi
  python3 - "$out" <<'PY' || true
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, ValueError):
    print("the verify Lambda's answer is not JSON")
    sys.exit()
if not isinstance(payload, dict) or payload.get("status") != "ok":
    reason = payload.get("reason", "") if isinstance(payload, dict) else ""
    print("tenancy-preflight answered status {!r} {}".format(
        payload.get("status") if isinstance(payload, dict) else None, reason).strip())
    sys.exit()
for result in payload.get("results") or []:
    if isinstance(result, dict) and result.get("label") == "open-runs":
        if "error" in result:
            print("the open-runs check failed: {}".format(result["error"]))
            sys.exit()
        rows = result.get("rows") or [{}]
        count = rows[0].get("open_runs") if isinstance(rows[0], dict) else None
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            print(count)
        else:
            print("the open-runs check returned no count")
        sys.exit()
print("tenancy-preflight has no open-runs row")
PY
  rm -f "$out"
}

loud_open_runs_override() { # what was found
  {
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "WARNING: ALLOW_OPEN_RUNS=1 — deploying although $1."
    echo "Runs in progress see the API restart and the migrations apply under them."
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  } >&2
}

open_runs_guard() { # -> 0 to go on; 1 refused (why is on stderr)
  local fn open_runs
  fn="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" VerifyFunctionName || true)"
  if [ "$fn" = "None" ]; then
    fn=""
  fi
  if [ -n "$fn" ] && ! verify_target_guard "$fn"; then
    return 1
  fi
  open_runs="$(open_runs_check "$fn")"
  case "$open_runs" in
    0)
      echo "    open-runs: 0"
      ;;
    ''|*[!0-9]*)
      if [ "${ALLOW_OPEN_RUNS:-}" = "1" ]; then
        loud_open_runs_override "no open-run count could be read: ${open_runs:-no answer}"
      else
        echo "ERROR: cannot confirm that no run is open: ${open_runs:-no answer}." >&2
        echo "       Deploy outside route hours; ALLOW_OPEN_RUNS=1 skips this check (first deploy, emergency)." >&2
        return 1
      fi
      ;;
    *)
      if [ "${ALLOW_OPEN_RUNS:-}" = "1" ]; then
        loud_open_runs_override "open-runs is ${open_runs}"
      else
        echo "ERROR: ${open_runs} run(s) are open (verify tenancy-preflight: open-runs; a run left open on an" >&2
        echo "       earlier day counts too). Deploy once they are completed, outside route hours;" >&2
        echo "       ALLOW_OPEN_RUNS=1 overrides in an emergency." >&2
        return 1
      fi
      ;;
  esac
}

echo "==> Checking for open runs (verify Lambda, tenancy-preflight: open-runs)"
open_runs_guard || exit 1

cd "$INFRA_DIR/backend"

echo "==> sam build (containerized arm64)"
sam build --use-container

echo "==> sam deploy (region ${BACKEND_REGION}, custom-domain cert: ${API_CERT_ARN:-<none>}, version ${APP_VERSION})"

# --- Change-set gate ----------------------------------------------------------
# SAM only creates the change set; scripts/changeset_gate.py reviews it and this
# script executes it (the changeset_* helpers in config.sh). The gate prints the
# change table and aborts (the change set is then deleted, so nothing runs) when
# the Database resource lists a MasterUserPassword change — the
# /saferide/db-password copy in $BACKEND_REGION does not match the live
# password — or when any resource but a stateless type would be replaced and
# ALLOW_REPLACE does not name it. A Conditional replacement, a stateless one and
# an allowed one are warnings. The stack and region are passed explicitly, so
# samconfig.toml can never point sam at another target than the one the gate
# reviews.

CHANGESET_SINCE="$(date -u +%s)"
exec 3>&1
SAM_OUT="$(sam deploy --no-execute-changeset --stack-name "$BACKEND_STACK" --region "$BACKEND_REGION" \
  --parameter-overrides "${DEPLOY_PARAMS[@]}" 2>&1 | tee /dev/fd/3)"
exec 3>&-

CHANGESET_ID=""
changeset_re='Changeset created successfully\. (arn:aws[^[:space:]]*:changeSet/[^[:space:]]+)'
if [[ $SAM_OUT =~ $changeset_re ]]; then
  CHANGESET_ID="${BASH_REMATCH[1]}"
fi

if [ -z "$CHANGESET_ID" ] && [[ $SAM_OUT == *"No changes to deploy"* ]]; then
  echo "==> No stack changes: nothing to execute; continuing to the migrate step."
else
  [ -n "$CHANGESET_ID" ] || CHANGESET_ID="$(changeset_newest_since "$BACKEND_STACK" "$BACKEND_REGION" \
    "$CHANGESET_SINCE" samcli-deploy || true)"
  if [ -z "$CHANGESET_ID" ]; then
    echo "ERROR: could not find the change set sam deploy created on ${BACKEND_STACK} (${BACKEND_REGION})." >&2
    exit 1
  fi
  echo "==> Reviewing change set ${CHANGESET_ID}"
  if ! changeset_review "$BACKEND_STACK" "$BACKEND_REGION" "$CHANGESET_ID"; then
    echo "ERROR: change-set gate refused this deploy (see ABORT above)." >&2
    changeset_discard "$BACKEND_STACK" "$BACKEND_REGION" "$CHANGESET_ID"
    exit 1
  fi
  changeset_confirm "$BACKEND_STACK" "$BACKEND_REGION" "$CHANGESET_ID" || exit 1
  # The build, the review and the prompt take minutes: a run opened meanwhile
  # refuses the deploy here too, under the same rule and override (hardening E2).
  echo "==> Re-checking for open runs before executing the change set"
  if ! open_runs_guard; then
    echo "ERROR: the open-run re-check refused this deploy; the change set was not executed." >&2
    changeset_discard "$BACKEND_STACK" "$BACKEND_REGION" "$CHANGESET_ID"
    exit 1
  fi
  changeset_execute "$BACKEND_STACK" "$BACKEND_REGION" "$CHANGESET_ID" || exit 1
  echo "==> Stack ${BACKEND_STACK} deployed"
fi

API_URL="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" HttpApiUrl)"
MIGRATE_FN="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" MigrateFunctionName)"
DB_ENDPOINT="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" DbEndpoint)"
CUSTOM_URL="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" ApiCustomDomainUrl)"

echo ""
echo "==> Running DB migrations via ${MIGRATE_FN} (seeds apply only locally)"
# NOTE deploy window: the new API code is already live at this point and its
# DAOs hard-depend on the latest migration objects. The window is brief, but a
# FAILED migration must abort loudly rather than leave the API 500ing.
MIGRATE_STATUS=$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$MIGRATE_FN" \
  --cli-read-timeout 0 --cli-binary-format raw-in-base64-out --payload '{}' \
  "$STATE_DIR/migrate-out.json" --query 'FunctionError' --output text)
echo "    migrate result:"
cat "$STATE_DIR/migrate-out.json"; echo
if [ "$MIGRATE_STATUS" != "None" ] && [ -n "$MIGRATE_STATUS" ]; then
  echo "ERROR: migrate Lambda failed ($MIGRATE_STATUS) — the live API depends on these migrations. Fix and re-run immediately." >&2
  exit 1
fi

echo ""
echo "Backend deployed:"
echo "  DB endpoint:       $DB_ENDPOINT"
echo "  HTTP API URL:      $API_URL"
[ -n "$CUSTOM_URL" ] && [ "$CUSTOM_URL" != "None" ] && echo "  Custom domain URL: $CUSTOM_URL"
echo ""
echo "Smoke test:"
echo "  curl -s ${API_URL}/api/health"
