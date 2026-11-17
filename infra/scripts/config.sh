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

# Read one /saferide/* parameter (decrypted) and print its value. Every such
# parameter lives in $BACKEND_REGION whatever the operator's default CLI region,
# so that is the default; pass a region only for a deliberate exception. A
# missing or unreadable parameter prints why on stderr and returns 1, so
# `VAR="$(ssm_required /saferide/x)"` aborts a `set -e` deploy: no deploy ever
# mints a secret (infra/README.md, "Deploy safety"). The value is never printed.
ssm_required() { # name [region] -> value
  local name="$1" region="${2:-$BACKEND_REGION}" val err
  err="$(mktemp)"
  val="$(aws ssm get-parameter --name "$name" --with-decryption --region "$region" \
    --query Parameter.Value --output text 2>"$err" || true)"
  if [ -z "$val" ] || [ "$val" = "None" ]; then
    if [ -s "$err" ] && ! grep -q ParameterNotFound "$err"; then
      # Credentials, permissions or network: not a miss, so say what AWS said.
      echo "ERROR: could not read SSM $name ($region): $(tr '\n' ' ' < "$err")" >&2
    else
      echo "ERROR: SSM $name ($region) is missing — copy or create it in $region before deploying (see infra/README.md, Operator checklist)" >&2
    fi
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  printf '%s' "$val"
}
export -f ssm_required

# Deploy only what was released: no modified or staged tracked files, and HEAD
# is origin/main (after a fetch) or carries a release/* tag. Untracked files are
# ignored here (.claude/ and justfile stay untracked on purpose); the backend
# deploy has its own untracked-file guard for what it packages.
# ALLOW_UNRELEASED_DEPLOY=1 is the documented emergency hatch (infra/README.md,
# "Deploy safety"): it waives the release check, never the tracked-file check.
require_clean_checkout() {
  local head branch tags
  if ! git -C "$REPO_DIR" fetch --quiet origin; then
    echo "ERROR: git fetch origin failed — cannot confirm HEAD is released." >&2
    return 1
  fi
  if ! git -C "$REPO_DIR" diff --quiet || ! git -C "$REPO_DIR" diff --cached --quiet; then
    echo "ERROR: modified or staged tracked files — deploy only a committed tree:" >&2
    git -C "$REPO_DIR" status --short --untracked-files=no >&2
    return 1
  fi
  head="$(git -C "$REPO_DIR" rev-parse --short HEAD)"
  if [ "$(git -C "$REPO_DIR" rev-parse HEAD)" = "$(git -C "$REPO_DIR" rev-parse origin/main)" ]; then
    echo "==> Clean checkout: HEAD $head is origin/main"
    return 0
  fi
  tags="$(git -C "$REPO_DIR" tag --points-at HEAD --list 'release/*' | tr '\n' ' ')"
  if [ -n "$tags" ]; then
    echo "==> Clean checkout: HEAD $head is tagged ${tags% }"
    return 0
  fi
  branch="$(git -C "$REPO_DIR" symbolic-ref --quiet --short HEAD || echo "detached HEAD")"
  if [ "${ALLOW_UNRELEASED_DEPLOY:-}" = "1" ]; then
    {
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      echo "WARNING: ALLOW_UNRELEASED_DEPLOY=1 — deploying $head on $branch, which is"
      echo "neither origin/main nor a release/* tag. Emergency use only: tag this"
      echo "commit release/<n> and record why in the release's validation record."
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    } >&2
    return 0
  fi
  echo "ERROR: HEAD $head on $branch is neither origin/main nor a release/* tag." >&2
  echo "       Deploy from an up-to-date main or a checked-out release/* tag (infra/README.md, Deploy safety)." >&2
  return 1
}
export -f require_clean_checkout
