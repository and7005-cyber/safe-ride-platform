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
  local name="$1" env_key="$2" val
  val="$(aws ssm get-parameter --name "$name" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -z "$val" ] || [ "$val" = "None" ]; then
    val="$(sed -n "s/^${env_key}=//p" "$REPO_DIR/backend/.env" 2>/dev/null | head -1)"
    if [ -n "$val" ]; then
      aws ssm put-parameter --name "$name" --type SecureString --value "$val" >/dev/null
      echo "==> Seeded SSM $name from backend/.env" >&2
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

cd "$INFRA_DIR/backend"

echo "==> sam build (containerized arm64)"
sam build --use-container

echo "==> sam deploy (region ${BACKEND_REGION}, custom-domain cert: ${API_CERT_ARN:-<none>})"
# SAM rejects empty Key= overrides, so only pass the cert ARN when we have one.
DEPLOY_PARAMS=("DbMasterPassword=${DB_PASSWORD}" "PinPepper=${PIN_PEPPER}")
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
