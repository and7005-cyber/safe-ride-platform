#!/usr/bin/env bash
# Run a named read-only verification check set against the production database
# via the verify Lambda (the RDS sits in private subnets — this is the
# sanctioned SQL door for go/no-go checks; see backend/app/verify_handler.py).
#
# Usage:
#   infra/scripts/verify-db.sh migrations
#   infra/scripts/verify-db.sh migration-011
#   infra/scripts/verify-db.sh baseline   <school-uuid>
#   infra/scripts/verify-db.sh post-apply <school-uuid>
#
# Expected values per check live in the deployment checklists:
# docs/work/validation/2026-08-20-fleet-plan-drafting.md and the plan's
# Operational Notes — this script reports observations; judgment is yours.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

CHECKS="${1:-}"
SCHOOL_ID="${2:-}"
if [ -z "$CHECKS" ]; then
  echo "usage: $0 <migrations|migration-011|baseline|post-apply> [school-uuid]" >&2
  exit 2
fi

VERIFY_FN="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" VerifyFunctionName)"
if [ -z "$VERIFY_FN" ] || [ "$VERIFY_FN" = "None" ]; then
  echo "ERROR: VerifyFunctionName not found on stack $BACKEND_STACK — deploy the stack revision that adds the verify Lambda first." >&2
  exit 1
fi

PAYLOAD="{\"checks\": \"$CHECKS\"}"
if [ -n "$SCHOOL_ID" ]; then
  PAYLOAD="{\"checks\": \"$CHECKS\", \"school_id\": \"$SCHOOL_ID\"}"
fi

OUT="$(mktemp)"
STATUS=$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$VERIFY_FN" \
  --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" \
  "$OUT" --query 'FunctionError' --output text)

if [ "$STATUS" != "None" ]; then
  echo "ERROR: verify Lambda failed ($STATUS):" >&2
  cat "$OUT" >&2; echo >&2
  exit 1
fi

if command -v python3 >/dev/null; then
  python3 -m json.tool "$OUT"
else
  cat "$OUT"; echo
fi
