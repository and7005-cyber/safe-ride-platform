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

# The value a stack parameter holds now; "" when the stack or the parameter
# does not exist (a first deploy, or a stack rolled back to a template without
# it). Any other read error is named on stderr and also gives "": the caller
# treats empty as "nothing held" and refuses or warns accordingly.
cfn_parameter() { # stack region key -> value
  local val err rc=0
  err="$(mktemp)"
  val="$(aws cloudformation describe-stacks --region "$2" --stack-name "$1" \
    --query "Stacks[0].Parameters[?ParameterKey=='$3'].ParameterValue" --output text 2>"$err")" || rc=$?
  if [ "$rc" -ne 0 ] && ! grep -q "does not exist" "$err"; then
    echo "WARN: could not read parameter $3 of stack $1 ($2): $(tr '\n' ' ' < "$err")(aws exit $rc)" >&2
  fi
  rm -f "$err"
  if [ "$rc" -ne 0 ] || [ "$val" = "None" ]; then
    val=""
  fi
  printf '%s' "$val"
}
export -f cfn_parameter

# --- Verify target (hardening E5) ------------------------------------------------
# After a restore rehearsal (infra/RESTORE.md, step B) the verify function reads
# a scratch instance until step C puts it back; an open-run count or a go/no-go
# check read there describes the wrong database. verify_target_guard compares
# the host of the verify function's DATABASE_URL with the stack's DbEndpoint
# output. Only the host is read: the URL carries the master password, so the
# CLI's answer goes straight into python3 (urlsplit), and neither the URL nor
# the password is ever printed, stored or put on an argv.
verify_database_host() { # function name -> its DATABASE_URL host, lowercased ("" when unset); non-zero when the CLI call fails
  aws lambda get-function-configuration --region "$BACKEND_REGION" --function-name "$1" \
    --query Environment.Variables.DATABASE_URL --output text \
    | python3 -c '
import sys
from urllib.parse import urlsplit

try:
    print((urlsplit(sys.stdin.read().strip()).hostname or "").lower())
except Exception:  # never echo the URL, not even in a traceback
    print("")
'
}

# verify_target_guard function-name -> 0 when the function reads the stack's
# database, or ALLOW_VERIFY_TARGET_MISMATCH=1 waives the check (loud warning);
# 1, saying why on stderr, otherwise. Prints nothing on stdout.
verify_target_guard() {
  local fn="$1" endpoint host problem=""
  endpoint="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" DbEndpoint || true)"
  if [ "$endpoint" = "None" ]; then
    endpoint=""
  fi
  if ! host="$(verify_database_host "$fn")"; then
    problem="the DATABASE_URL host of $fn could not be read (see the AWS error above)"
  elif [ -z "$host" ]; then
    problem="$fn has no DATABASE_URL"
  elif [ -z "$endpoint" ]; then
    problem="stack $BACKEND_STACK ($BACKEND_REGION) has no DbEndpoint output"
  elif [ "$host" != "$(printf '%s' "$endpoint" | tr '[:upper:]' '[:lower:]')" ]; then
    problem="$fn reads database host $host, not the stack's DbEndpoint $endpoint (it still points at a scratch restore)"
  fi
  if [ -z "$problem" ]; then
    return 0
  fi
  if [ "${ALLOW_VERIFY_TARGET_MISMATCH:-}" = "1" ]; then
    {
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      echo "WARNING: ALLOW_VERIFY_TARGET_MISMATCH=1 — going on although $problem."
      echo "Every answer of the verify function describes that database, not ${BACKEND_STACK}'s."
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    } >&2
    return 0
  fi
  echo "ERROR: the verify function does not read ${BACKEND_STACK}'s database: $problem." >&2
  echo "       Put its configuration back (infra/RESTORE.md, step C) and read the host back;" >&2
  echo "       ALLOW_VERIFY_TARGET_MISMATCH=1 reads it anyway (a restore rehearsal)." >&2
  return 1
}

# --- Change-set gate (hardening D7, E3) ------------------------------------------
# Both deploys create a change set without executing it; scripts/changeset_gate.py
# reviews it, and these helpers delete it (nothing runs) or, once the gate and
# any CONFIRM_CHANGESET=1 prompt pass, execute it and wait. Every call names the
# stack and region explicitly. ALLOW_REPLACE=<LogicalId,...> reaches the gate.
CHANGESET_GATE="$INFRA_DIR/scripts/changeset_gate.py"

changeset_newest_since() { # stack region epoch name-prefix -> id of the newest executable change set with that prefix created since epoch
  aws cloudformation list-change-sets --region "$2" --stack-name "$1" --output json \
    | python3 "$CHANGESET_GATE" newest-since "$3" --prefix "$4"
}

changeset_review() { # stack region id -> prints the review; non-zero means abort
  aws cloudformation describe-change-set --region "$2" --stack-name "$1" \
    --change-set-name "$3" --output json \
    | python3 "$CHANGESET_GATE" review --allow-replace "${ALLOW_REPLACE:-}"
}

changeset_discard() { # stack region id
  if aws cloudformation delete-change-set --region "$2" --stack-name "$1" --change-set-name "$3"; then
    echo "    change set deleted; nothing was executed." >&2
  else
    echo "WARN: could not delete change set $3 — it was NOT executed; delete it in the console." >&2
  fi
}

# With CONFIRM_CHANGESET=1 the operator answers y/N; anything but y deletes the
# change set and returns 1. Without it, returns 0.
changeset_confirm() { # stack region id
  local answer=""
  if [ "${CONFIRM_CHANGESET:-}" != "1" ]; then
    return 0
  fi
  read -r -p "Execute this change set on $1 ($2)? [y/N] " answer || true
  case "$answer" in
    y|Y|yes|YES|Yes) return 0 ;;
  esac
  echo "Deploy cancelled by the operator." >&2
  changeset_discard "$1" "$2" "$3"
  return 1
}

# Executes the change set and waits for the stack; non-zero, after printing the
# latest stack events, when it does not get there.
changeset_execute() { # stack region id
  local waiter=stack-update-complete
  # A first deploy leaves the new stack in REVIEW_IN_PROGRESS until executed.
  if [ "$(aws cloudformation describe-stacks --region "$2" --stack-name "$1" \
      --query 'Stacks[0].StackStatus' --output text)" = "REVIEW_IN_PROGRESS" ]; then
    waiter=stack-create-complete
  fi
  echo "==> Executing change set (waiting for ${waiter}; this can take many minutes)"
  aws cloudformation execute-change-set --region "$2" --stack-name "$1" --change-set-name "$3" || return 1
  if ! aws cloudformation wait "$waiter" --region "$2" --stack-name "$1"; then
    echo "ERROR: $1 did not reach ${waiter#stack-}. Latest stack events:" >&2
    aws cloudformation describe-stack-events --region "$2" --stack-name "$1" \
      --max-items 20 --output table \
      --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' >&2 || true
    return 1
  fi
}

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
