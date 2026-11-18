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

# Read one /saferide/* parameter (decrypted) from $BACKEND_REGION — where every
# such parameter lives, whatever the operator's default CLI region. On success
# it prints the value and returns 0. It returns 3, printing nothing, when the
# parameter does not exist (ParameterNotFound, or no value). Any other failure
# (credentials, permissions, network, no region) is never mistaken for a miss:
# it names AWS's error on stderr and returns 1.
ssm_lookup() { # name -> value; status 0 found, 3 missing, 1 read error
  local name="$1" val err rc=0
  err="$(mktemp)"
  val="$(aws ssm get-parameter --name "$name" --with-decryption --region "$BACKEND_REGION" \
    --query Parameter.Value --output text 2>"$err")" || rc=$?
  if [ "$rc" -ne 0 ] && ! grep -q ParameterNotFound "$err"; then
    echo "ERROR: could not read SSM $name ($BACKEND_REGION): $(tr '\n' ' ' < "$err")(aws exit $rc)" >&2
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  if [ "$rc" -ne 0 ] || [ -z "$val" ] || [ "$val" = "None" ]; then
    return 3
  fi
  printf '%s' "$val"
}
export -f ssm_lookup

# Read one /saferide/* parameter that must pre-exist and print its value. A
# missing or unreadable parameter prints why on stderr and returns 1, so
# `VAR="$(ssm_required /saferide/x)"` aborts a `set -e` deploy: no deploy ever
# mints a secret (infra/README.md, "Deploy safety"). The value is never printed.
ssm_required() { # name -> value
  local name="$1" val rc=0
  val="$(ssm_lookup "$name")" || rc=$?
  if [ "$rc" -eq 3 ]; then
    echo "ERROR: SSM $name ($BACKEND_REGION) is missing — copy or create it in $BACKEND_REGION before deploying (see docs/runbooks/hardening-release-checklists.md, operator checklist step 4)" >&2
  fi
  [ "$rc" -eq 0 ] || return 1
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
