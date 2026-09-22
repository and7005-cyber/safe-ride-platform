#!/usr/bin/env bash
# Build + deploy the SafeRide backend SAM stack to af-south-1, then run the
# database migrations/seeds via the migrate Lambda.
#
# Secrets (DB password, PIN pepper) are generated once and kept in SSM
# SecureString so they stay stable across redeploys. Pass the API cert ARN via
# env (API_CERT_ARN=...) or infra/.state/certs.env to enable the custom domain.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

# Load cert ARNs if request-certs.sh has run.
[ -f "$STATE_DIR/certs.env" ] && source "$STATE_DIR/certs.env"
API_CERT_ARN="${API_CERT_ARN:-}"

ssm_secret() { # name -> value (creates a random one if missing)
  local name="$1" val
  val="$(aws ssm get-parameter --name "$name" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -z "$val" ] || [ "$val" = "None" ]; then
    val="$(openssl rand -hex 24)"
    aws ssm put-parameter --name "$name" --type SecureString --value "$val" >/dev/null
  fi
  echo "$val"
}

DB_PASSWORD="$(ssm_secret /saferide/db-password)"
PIN_PEPPER="$(ssm_secret /saferide/pin-pepper)"
DB_APP_PASSWORD_VAL="$(ssm_secret /saferide/db-app-password)"

# Tenancy (U2): the TOTP pepper and the provider bootstrap must PRE-EXIST —
# never minted here. A silently re-minted pepper would re-enrol every
# provider's second factor, and a minted bootstrap would insert garbage
# accounts. Create both with scripts/provider-bootstrap.sh before deploying.
TOTP_PEPPER_VAL="$(aws ssm get-parameter --name /saferide/totp-pepper --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)"
if [ -z "$TOTP_PEPPER_VAL" ] || [ "$TOTP_PEPPER_VAL" = "None" ]; then
  echo "ERROR: SSM /saferide/totp-pepper is missing. Run scripts/provider-bootstrap.sh first." >&2
  exit 1
fi
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

# Google Maps server key: prefer SSM; seed it once from backend/.env when
# missing (it cannot be auto-generated like the secrets above).
GOOGLE_MAPS_KEY="$(aws ssm get-parameter --name /saferide/google-maps-api-key --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)"
if [ -z "$GOOGLE_MAPS_KEY" ] || [ "$GOOGLE_MAPS_KEY" = "None" ]; then
  GOOGLE_MAPS_KEY="$(sed -n 's/^GOOGLE_MAPS_API_KEY=//p' "$REPO_DIR/backend/.env" 2>/dev/null | head -1)"
  if [ -n "$GOOGLE_MAPS_KEY" ]; then
    aws ssm put-parameter --name /saferide/google-maps-api-key --type SecureString \
      --value "$GOOGLE_MAPS_KEY" >/dev/null
    echo "==> Seeded SSM /saferide/google-maps-api-key from backend/.env"
  else
    echo "WARN: no Google Maps API key in SSM or backend/.env — autocomplete and road routing will be degraded." >&2
  fi
fi

# Push secrets: prefer SSM; seed each from backend/.env when missing (they are
# provisioned once — Firebase service account, web config, VAPID pair). Unlike
# db-password/pin-pepper these cannot be auto-generated. Empty is fine: the
# code degrades (FCM off / web push off) and the in-app feed still works.
ssm_seeded() { # ssm_name env_key -> value ("" if neither SSM nor .env has it)
  # Pinned to BACKEND_REGION: the two Firebase JSON params are read from SSM by
  # the Lambda at runtime (in BACKEND_REGION), so they must live there — not in
  # the operator's default CLI region. (ssm_secret above is region-agnostic: its
  # values flow through CFN params into the env, never read from SSM at runtime.)
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

cd "$INFRA_DIR/backend"

echo "==> sam build (containerized arm64)"
sam build --use-container

echo "==> sam deploy (region ${BACKEND_REGION}, custom-domain cert: ${API_CERT_ARN:-<none>})"
# SAM rejects empty Key= overrides, so only pass the cert ARN when we have one.
DEPLOY_PARAMS=("DbMasterPassword=${DB_PASSWORD}" "PinPepper=${PIN_PEPPER}" "DbAppPassword=${DB_APP_PASSWORD_VAL}" "TotpPepper=${TOTP_PEPPER_VAL}")
[ -n "$API_CERT_ARN" ] && DEPLOY_PARAMS+=("ApiCertificateArn=${API_CERT_ARN}")
[ -n "$GOOGLE_MAPS_KEY" ] && DEPLOY_PARAMS+=("GoogleMapsApiKey=${GOOGLE_MAPS_KEY}")
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
# GPS tracking system defaults (GPS plan U11). The template Defaults equal the
# app's Settings defaults; an operator changes one for a deploy by exporting
# the same variable name the app reads (e.g. GPS_CUSTODY_THRESHOLD_M=200) —
# omitted means the template Default, never an empty override (SAM rejects
# those). Schools override the first five per school in School Settings; the
# last two are system-wide. Each pair is TemplateParameter:ENV_NAME.
GPS_PARAMS=(
  GpsCustodyThresholdM:GPS_CUSTODY_THRESHOLD_M
  GpsVicinityRadiusM:GPS_VICINITY_RADIUS_M
  GpsFixAccuracyCapM:GPS_FIX_ACCURACY_CAP_M
  GpsPositionRetentionDays:GPS_POSITION_RETENTION_DAYS
  GpsPingIntervalS:GPS_PING_INTERVAL_S
  GpsStaleAfterS:GPS_STALE_AFTER_S
  GpsFixWaitBudgetS:GPS_FIX_WAIT_BUDGET_S
)
for pair in "${GPS_PARAMS[@]}"; do
  param="${pair%%:*}"; var="${pair##*:}"
  if [ -n "${!var:-}" ]; then
    DEPLOY_PARAMS+=("${param}=${!var}")
    echo "==> GPS default override: ${param}=${!var} (from \$${var})"
  fi
done
sam deploy --parameter-overrides "${DEPLOY_PARAMS[@]}"

API_URL="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" HttpApiUrl)"
MIGRATE_FN="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" MigrateFunctionName)"
DB_ENDPOINT="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" DbEndpoint)"
CUSTOM_URL="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" ApiCustomDomainUrl)"

echo ""
echo "==> Running DB migrations + seeds via ${MIGRATE_FN}"
# NOTE deploy window: the new API code is already live at this point and its
# DAOs hard-depend on the latest migration objects. The window is brief, but a
# FAILED migration must abort loudly rather than leave the API 500ing.
MIGRATE_STATUS=$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$MIGRATE_FN" \
  --cli-binary-format raw-in-base64-out --payload '{}' \
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
