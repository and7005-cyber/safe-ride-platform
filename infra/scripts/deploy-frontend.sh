#!/usr/bin/env bash
# Build the frontend against the production API URL, deploy the S3 + CloudFront
# stack (us-east-1), upload the build, and invalidate the CDN cache.
#
# Pass the CloudFront cert ARN via env (CF_CERT_ARN=...) or infra/.state/certs.env
# to attach the custom domain + Route 53 records. Without it, the site is served
# on the default *.cloudfront.net domain (useful for an early smoke test).
#
# CSP_MODE=report-only|enforce (default report-only) picks the content-security
# policy header; ALERT_EMAIL_PRIMARY / ALERT_EMAIL_SECONDARY subscribe to the
# us-east-1 alarm topic and the cost budget; MONTHLY_BUDGET_USD overrides the
# budget's template Default for this deploy (infra/README.md, "Alarms" and
# "Security headers").
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
source "$(dirname "${BASH_SOURCE[0]}")/parity-params.sh"

# A committed tree on origin/main or a release/* tag (config.sh).
require_clean_checkout

# Checked before anything is read or built: CloudFormation would refuse any
# other value only after the build.
CSP_MODE="${CSP_MODE:-report-only}"
case "$CSP_MODE" in
  report-only|enforce) ;;
  *)
    echo "ERROR: CSP_MODE must be report-only or enforce (got '${CSP_MODE}')." >&2
    exit 1
    ;;
esac

# Production Maps values (hardening U16): the referrer-restricted browser key and
# the Cloud-styled Map ID live only in the parameter store ($BACKEND_REGION, like
# every /saferide/* parameter) and reach the bundle through the build
# environment. Vite gives variables already set in the environment priority
# over .env files, so frontend/.env.local (the development key and the demo Map
# ID) cannot leak into this build. A missing parameter aborts here, naming it;
# the operator creates both (infra/README.md, SSM parameters). The values are
# never printed.
MAPS_BROWSER_KEY="$(ssm_required /saferide/google-maps-browser-key)"
MAPS_MAP_ID="$(ssm_required /saferide/google-maps-map-id)"

[ -f "$STATE_DIR/certs.env" ] && source "$STATE_DIR/certs.env"
CF_CERT_ARN="${CF_CERT_ARN:-}"
API_BASE_URL="${VITE_API_BASE_URL:-https://${API_DOMAIN}}"
# The stack's CSP connect-src and API probe use the origin this build calls.
API_ORIGIN="${API_BASE_URL%/}"

# --- Stack parameters ----------------------------------------------------------
# `aws cloudformation deploy` reuses a parameter's previous value when it is not
# passed, so every frontend parameter with a template Default is passed on every
# deploy (scripts/parity-params.sh, FRONTEND_PARAMS): CspMode is always the
# mode asked for now (report-only unless CSP_MODE=enforce), never a leftover;
# DomainName, HostedZoneId and ApiOrigin follow config.sh and this build;
# MonthlyBudgetUsd is MONTHLY_BUDGET_USD when exported, else the template
# Default. The alert addresses, like the backend's, are passed when exported
# and kept otherwise. Assembled before the build, so a template the parity
# reader cannot parse stops the deploy before anything is built.
DEPLOY_PARAMS=()
[ -n "$CF_CERT_ARN" ] && DEPLOY_PARAMS+=("CertificateArn=${CF_CERT_ARN}")
build_parity_params "$INFRA_DIR/frontend/template.yaml" "${FRONTEND_PARAMS[@]}"

echo "==> Building frontend with VITE_API_BASE_URL=${API_BASE_URL} and the production Maps key and Map ID from SSM (not printed)"
cd "$REPO_DIR/frontend"
VITE_API_BASE_URL="$API_BASE_URL" \
  VITE_GOOGLE_MAPS_API_KEY="$MAPS_BROWSER_KEY" \
  VITE_GOOGLE_MAPS_MAP_ID="$MAPS_MAP_ID" \
  npm run build

echo "==> Deploying frontend stack (region ${CF_REGION}, cert: ${CF_CERT_ARN:-<none>}, CspMode=${CSP_MODE})"
aws cloudformation deploy \
  --region "$CF_REGION" \
  --stack-name "$FRONTEND_STACK" \
  --template-file "$INFRA_DIR/frontend/template.yaml" \
  --parameter-overrides "${DEPLOY_PARAMS[@]}" \
  --no-fail-on-empty-changeset

BUCKET="$(cfn_output "$FRONTEND_STACK" "$CF_REGION" BucketName)"
DIST_ID="$(cfn_output "$FRONTEND_STACK" "$CF_REGION" DistributionId)"
SITE_URL="$(cfn_output "$FRONTEND_STACK" "$CF_REGION" SiteUrl)"

echo "==> Uploading build to s3://${BUCKET}"
# Hashed assets: cache forever. index.html + service worker: never cache.
aws s3 sync "$REPO_DIR/frontend/dist" "s3://${BUCKET}" --delete \
  --exclude index.html --exclude sw.js \
  --cache-control "public,max-age=31536000,immutable"
aws s3 cp "$REPO_DIR/frontend/dist/index.html" "s3://${BUCKET}/index.html" \
  --cache-control "no-cache" --content-type "text/html"
[ -f "$REPO_DIR/frontend/dist/sw.js" ] && aws s3 cp "$REPO_DIR/frontend/dist/sw.js" "s3://${BUCKET}/sw.js" \
  --cache-control "no-cache" --content-type "application/javascript"

echo "==> Invalidating CloudFront ${DIST_ID}"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" \
  --query 'Invalidation.Id' --output text

echo ""
echo "Frontend deployed: ${SITE_URL}"
