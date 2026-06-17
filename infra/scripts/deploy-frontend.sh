#!/usr/bin/env bash
# Build the frontend against the production API URL, deploy the S3 + CloudFront
# stack (us-east-1), upload the build, and invalidate the CDN cache.
#
# Pass the CloudFront cert ARN via env (CF_CERT_ARN=...) or infra/.state/certs.env
# to attach the custom domain + Route 53 records. Without it, the site is served
# on the default *.cloudfront.net domain (useful for an early smoke test).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

[ -f "$STATE_DIR/certs.env" ] && source "$STATE_DIR/certs.env"
CF_CERT_ARN="${CF_CERT_ARN:-}"
API_BASE_URL="${VITE_API_BASE_URL:-https://${API_DOMAIN}}"

echo "==> Building frontend with VITE_API_BASE_URL=${API_BASE_URL}"
cd "$REPO_DIR/frontend"
VITE_API_BASE_URL="$API_BASE_URL" npm run build

echo "==> Deploying frontend stack (region ${CF_REGION}, cert: ${CF_CERT_ARN:-<none>})"
FE_PARAMS=("DomainName=${DOMAIN}" "HostedZoneId=${HOSTED_ZONE_ID}")
[ -n "$CF_CERT_ARN" ] && FE_PARAMS+=("CertificateArn=${CF_CERT_ARN}")
aws cloudformation deploy \
  --region "$CF_REGION" \
  --stack-name "$FRONTEND_STACK" \
  --template-file "$INFRA_DIR/frontend/template.yaml" \
  --parameter-overrides "${FE_PARAMS[@]}" \
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
