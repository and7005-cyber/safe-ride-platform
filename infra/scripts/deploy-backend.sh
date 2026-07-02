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

cd "$INFRA_DIR/backend"

echo "==> sam build (containerized arm64)"
sam build --use-container

echo "==> sam deploy (region ${BACKEND_REGION}, custom-domain cert: ${API_CERT_ARN:-<none>})"
# SAM rejects empty Key= overrides, so only pass the cert ARN when we have one.
DEPLOY_PARAMS=("DbMasterPassword=${DB_PASSWORD}" "PinPepper=${PIN_PEPPER}")
[ -n "$API_CERT_ARN" ] && DEPLOY_PARAMS+=("ApiCertificateArn=${API_CERT_ARN}")
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
