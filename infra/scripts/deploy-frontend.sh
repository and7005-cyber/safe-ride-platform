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
#
# Safety rails (infra/README.md, "Deploy safety"), as in deploy-backend.sh:
# - a committed, released checkout (require_clean_checkout, config.sh);
# - the alarms and the budget must email someone: ALERT_EMAIL_PRIMARY is
#   exported, or the frontend stack holds an AlertEmailPrimary, which is then
#   passed explicitly. ALLOW_NO_ALERTS=1 deploys without one, loudly;
# - the stack change set is reviewed before it executes
#   (scripts/changeset_gate.py): the replacement of anything but a stateless
#   type (SNS subscription, alarm) aborts it; ALLOW_REPLACE=<LogicalId,...>
#   lets the named resources be replaced on this deploy, loudly, but never the
#   CloudFront distribution or the site bucket. CONFIRM_CHANGESET=1 also asks
#   the operator y/N before executing. Nothing is uploaded unless the stack is
#   deployed (or has no changes).
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
# No deploy may leave the us-east-1 alarms and the budget emailing no one
# (hardening E1): the frontend stack's own AlertEmailPrimary is the one read.
alert_address_guard "$FRONTEND_STACK" "$CF_REGION" || exit 1

echo "==> Building frontend with VITE_API_BASE_URL=${API_BASE_URL} and the production Maps key and Map ID from SSM (not printed)"
cd "$REPO_DIR/frontend"
VITE_API_BASE_URL="$API_BASE_URL" \
  VITE_GOOGLE_MAPS_API_KEY="$MAPS_BROWSER_KEY" \
  VITE_GOOGLE_MAPS_MAP_ID="$MAPS_MAP_ID" \
  npm run build

echo "==> Deploying frontend stack (region ${CF_REGION}, cert: ${CF_CERT_ARN:-<none>}, CspMode=${CSP_MODE})"

# --- Change-set gate (hardening E3) ---------------------------------------------
# `aws cloudformation deploy --no-execute-changeset` only creates the change
# set (named awscli-cloudformation-package-deploy-<epoch>) and prints its ARN;
# scripts/changeset_gate.py reviews it and the changeset_* helpers in config.sh
# delete it (nothing runs) or execute it and wait. "No changes to deploy" goes
# straight to the upload.
CHANGESET_SINCE="$(date -u +%s)"
exec 3>&1
CFN_OUT="$(aws cloudformation deploy \
  --region "$CF_REGION" \
  --stack-name "$FRONTEND_STACK" \
  --template-file "$INFRA_DIR/frontend/template.yaml" \
  --no-execute-changeset \
  --parameter-overrides "${DEPLOY_PARAMS[@]}" \
  --no-fail-on-empty-changeset 2>&1 | tee /dev/fd/3)"
exec 3>&-

CHANGESET_ID=""
changeset_re='--change-set-name (arn:aws[^[:space:]]*:changeSet/[^[:space:]]+)'
if [[ $CFN_OUT =~ $changeset_re ]]; then
  CHANGESET_ID="${BASH_REMATCH[1]}"
fi

if [ -z "$CHANGESET_ID" ] && [[ $CFN_OUT == *"No changes to deploy"* ]]; then
  echo "==> No stack changes: nothing to execute; continuing to the upload."
else
  [ -n "$CHANGESET_ID" ] || CHANGESET_ID="$(changeset_newest_since "$FRONTEND_STACK" "$CF_REGION" \
    "$CHANGESET_SINCE" awscli-cloudformation-package-deploy- || true)"
  if [ -z "$CHANGESET_ID" ]; then
    echo "ERROR: could not find the change set aws cloudformation deploy created on ${FRONTEND_STACK} (${CF_REGION})." >&2
    exit 1
  fi
  echo "==> Reviewing change set ${CHANGESET_ID}"
  if ! changeset_review "$FRONTEND_STACK" "$CF_REGION" "$CHANGESET_ID"; then
    echo "ERROR: change-set gate refused this deploy (see ABORT above); nothing was uploaded." >&2
    changeset_discard "$FRONTEND_STACK" "$CF_REGION" "$CHANGESET_ID"
    exit 1
  fi
  changeset_confirm "$FRONTEND_STACK" "$CF_REGION" "$CHANGESET_ID" || exit 1
  changeset_execute "$FRONTEND_STACK" "$CF_REGION" "$CHANGESET_ID" || exit 1
  echo "==> Stack ${FRONTEND_STACK} deployed"
fi

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
