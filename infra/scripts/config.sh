#!/usr/bin/env bash
# Shared configuration for SafeRide infra scripts.
set -euo pipefail

export DOMAIN="${DOMAIN:-saferidelive.co.ke}"
export API_DOMAIN="${API_DOMAIN:-api.${DOMAIN}}"
export HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-Z0459820URO1AH08IO8T}"

# CloudFront ACM certs must live in us-east-1; the backend runs in af-south-1.
export CF_REGION="${CF_REGION:-us-east-1}"
export BACKEND_REGION="${BACKEND_REGION:-af-south-1}"

export BACKEND_STACK="${BACKEND_STACK:-saferide-backend}"
export FRONTEND_STACK="${FRONTEND_STACK:-saferide-frontend}"

# Repo paths (resolved relative to this file).
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export INFRA_DIR
export REPO_DIR="$(cd "$INFRA_DIR/.." && pwd)"
export STATE_DIR="$INFRA_DIR/.state"
mkdir -p "$STATE_DIR"

cfn_output() { # stack region key
  aws cloudformation describe-stacks --region "$2" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text 2>/dev/null
}
export -f cfn_output
