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
# - the stack change set is reviewed before it executes: a master-password
#   change on the database or any resource replacement aborts it.
#   CONFIRM_CHANGESET=1 also asks the operator y/N before executing;
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
PROVIDER_BOOTSTRAP_RAW="$(aws ssm get-parameter --name /saferide/provider-bootstrap \
  --region "$BACKEND_REGION" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
if [ -z "$PROVIDER_BOOTSTRAP_RAW" ] || [ "$PROVIDER_BOOTSTRAP_RAW" = "None" ]; then
  echo "ERROR: SSM /saferide/provider-bootstrap ($BACKEND_REGION) is missing. Run scripts/provider-bootstrap.sh first." >&2
  exit 1
fi
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

# Push secrets: prefer SSM; seed each from backend/.env when missing (they are
# provisioned once — Firebase service account, web config, VAPID pair). Empty
# is fine: the code degrades (FCM off / web push off) and the in-app feed
# still works.
ssm_seeded() { # ssm_name env_key -> value ("" if neither SSM nor .env has it)
  # Pinned to BACKEND_REGION: the two Firebase JSON params are read from SSM by
  # the Lambda at runtime (in BACKEND_REGION), so they must live there — not in
  # the operator's default CLI region. (ssm_required above reads the same region;
  # its values flow through CFN params into the env instead.)
  local name="$1" env_key="$2" val
  val="$(aws ssm get-parameter --name "$name" --with-decryption --region "$BACKEND_REGION" \
    --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -z "$val" ] || [ "$val" = "None" ]; then
    val="$(sed -n "s/^${env_key}=//p" "$REPO_DIR/backend/.env" 2>/dev/null | head -1)"
    if [ -n "$val" ]; then
      aws ssm put-parameter --name "$name" --type SecureString --value "$val" \
        --region "$BACKEND_REGION" >/dev/null
      echo "==> Seeded SSM $name ($BACKEND_REGION) from backend/.env" >&2
    fi
  fi
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
# Live-parity parameters (scripts/parity-params.sh: the GPS, API-throttle, mail
# and alarm maps). SAM sends UsePreviousValue for any parameter a deploy does
# not pass, so each one with a template Default is passed on every deploy: the
# exported variable under the name the app reads (e.g.
# GPS_CUSTODY_THRESHOLD_M=200), otherwise the template Default. An export lasts
# for that deploy only — repeat it, or change the Default in a release. The
# alert addresses and MailReplyTo have empty Defaults: passed only when
# exported (SAM rejects an empty override), otherwise the stack keeps its value.
build_parity_params "$INFRA_DIR/backend/template.yaml"

cd "$INFRA_DIR/backend"

echo "==> sam build (containerized arm64)"
sam build --use-container

echo "==> sam deploy (region ${BACKEND_REGION}, custom-domain cert: ${API_CERT_ARN:-<none>}, version ${APP_VERSION})"

# --- Change-set gate ----------------------------------------------------------
# SAM only creates the change set; this script reviews it and executes it. It
# aborts (and deletes the change set, so nothing runs) when the Database
# resource lists a MasterUserPassword change — the /saferide/db-password copy
# in $BACKEND_REGION does not match the live password — or when any resource
# would be replaced. A Conditional replacement is printed as a warning.

newest_changeset_since() { # epoch -> id of the newest executable SAM change set created since
  aws cloudformation list-change-sets --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
    --output json | python3 -c '
import json, re, sys
from datetime import datetime

def created(text):
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:  # fractional seconds this Python cannot parse
        return datetime.fromisoformat(re.sub(r"\.\d+", "", text)).timestamp()

since = float(sys.argv[1]) - 5  # tolerate a few seconds of clock skew
best = None
for summary in json.load(sys.stdin).get("Summaries", []):
    if (summary.get("Status") != "CREATE_COMPLETE"
            or summary.get("ExecutionStatus") != "AVAILABLE"
            or not summary.get("ChangeSetName", "").startswith("samcli-deploy")):
        continue
    at = created(summary["CreationTime"])
    if at >= since and (best is None or at > best[0]):
        best = (at, summary["ChangeSetId"])
print(best[1] if best else "")
' "$1"
}

review_changeset() { # change-set id -> prints the review; non-zero means abort
  aws cloudformation describe-change-set --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
    --change-set-name "$1" --output json | python3 -c '
import json, sys

change_set = json.load(sys.stdin)
if change_set.get("Status") != "CREATE_COMPLETE" or change_set.get("ExecutionStatus") != "AVAILABLE":
    print("ABORT: change set is {}/{}: {}".format(
        change_set.get("Status"), change_set.get("ExecutionStatus"),
        change_set.get("StatusReason", "")), file=sys.stderr)
    sys.exit(2)

rows, aborts, warnings = [], [], []
for change in change_set.get("Changes", []):
    resource = change.get("ResourceChange")
    if not resource:
        continue
    logical = resource.get("LogicalResourceId", "?")
    rtype = resource.get("ResourceType", "?")
    replacement = resource.get("Replacement") or "-"
    names = []
    for detail in resource.get("Details", []):
        target = detail.get("Target", {})
        name = target.get("Name") if target.get("Attribute") == "Properties" else target.get("Attribute")
        if name and name not in names:
            names.append(name)
    rows.append((resource.get("Action", "?"), logical, rtype, replacement, ", ".join(names) or "-"))
    if logical == "Database" and any(
            "MasterUserPassword" in (detail.get("Target", {}).get("Name") or "")
            for detail in resource.get("Details", [])):
        aborts.append("Database lists a MasterUserPassword change: SSM /saferide/db-password in this"
                      " region does not match the live master password")
    if replacement == "True":
        aborts.append("{} ({}) would be REPLACED".format(logical, rtype))
    elif replacement == "Conditional":
        warnings.append("{} ({}) may be replaced (Replacement: Conditional)".format(logical, rtype))

header = ("Action", "LogicalResourceId", "ResourceType", "Replacement", "Changed")
widths = [max(len(row[i]) for row in rows + [header]) for i in range(4)]
print()
for row in [header] + rows:
    print("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:4])) + "  " + row[4])
print()
sys.stdout.flush()  # the table before the verdict, even when piped to a log
for warning in warnings:
    print("WARN: " + warning, file=sys.stderr)
for abort in aborts:
    print("ABORT: " + abort, file=sys.stderr)
sys.exit(2 if aborts else 0)
'
}

discard_changeset() { # change-set id
  if aws cloudformation delete-change-set --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
      --change-set-name "$1"; then
    echo "    change set deleted; nothing was executed." >&2
  else
    echo "WARN: could not delete change set $1 — it was NOT executed; delete it in the console." >&2
  fi
}

CHANGESET_SINCE="$(date -u +%s)"
exec 3>&1
SAM_OUT="$(sam deploy --no-execute-changeset --parameter-overrides "${DEPLOY_PARAMS[@]}" 2>&1 | tee /dev/fd/3)"
exec 3>&-

CHANGESET_ID=""
changeset_re='Changeset created successfully\. (arn:aws[^[:space:]]*:changeSet/[^[:space:]]+)'
if [[ $SAM_OUT =~ $changeset_re ]]; then
  CHANGESET_ID="${BASH_REMATCH[1]}"
fi

if [ -z "$CHANGESET_ID" ] && [[ $SAM_OUT == *"No changes to deploy"* ]]; then
  echo "==> No stack changes: nothing to execute; continuing to the migrate step."
else
  [ -n "$CHANGESET_ID" ] || CHANGESET_ID="$(newest_changeset_since "$CHANGESET_SINCE")"
  if [ -z "$CHANGESET_ID" ]; then
    echo "ERROR: could not find the change set sam deploy created on ${BACKEND_STACK} (${BACKEND_REGION})." >&2
    exit 1
  fi
  echo "==> Reviewing change set ${CHANGESET_ID}"
  if ! review_changeset "$CHANGESET_ID"; then
    echo "ERROR: change-set gate refused this deploy (see ABORT above)." >&2
    discard_changeset "$CHANGESET_ID"
    exit 1
  fi
  if [ "${CONFIRM_CHANGESET:-}" = "1" ]; then
    answer=""
    read -r -p "Execute this change set on ${BACKEND_STACK} (${BACKEND_REGION})? [y/N] " answer || true
    case "$answer" in
      y|Y|yes|YES|Yes) ;;
      *)
        echo "Deploy cancelled by the operator." >&2
        discard_changeset "$CHANGESET_ID"
        exit 1
        ;;
    esac
  fi
  # A first deploy leaves the new stack in REVIEW_IN_PROGRESS until executed.
  STACK_WAITER=stack-update-complete
  [ "$(aws cloudformation describe-stacks --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
    --query 'Stacks[0].StackStatus' --output text)" = "REVIEW_IN_PROGRESS" ] && STACK_WAITER=stack-create-complete
  echo "==> Executing change set (waiting for ${STACK_WAITER}; database changes can take many minutes)"
  aws cloudformation execute-change-set --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
    --change-set-name "$CHANGESET_ID"
  if ! aws cloudformation wait "$STACK_WAITER" --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK"; then
    echo "ERROR: ${BACKEND_STACK} did not reach ${STACK_WAITER#stack-}. Latest stack events:" >&2
    aws cloudformation describe-stack-events --region "$BACKEND_REGION" --stack-name "$BACKEND_STACK" \
      --max-items 20 --output table \
      --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' >&2 || true
    exit 1
  fi
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
