#!/usr/bin/env bash
# Run a named read-only verification check set against the production database
# via the verify Lambda (the RDS sits in private subnets — this is the
# sanctioned SQL door for go/no-go checks; see backend/app/verify_handler.py).
#
# Usage:
#   infra/scripts/verify-db.sh [--strict] migrations
#   infra/scripts/verify-db.sh migration-011
#   infra/scripts/verify-db.sh baseline   <school-uuid>
#   infra/scripts/verify-db.sh post-apply <school-uuid>
#   infra/scripts/verify-db.sh gps
#   infra/scripts/verify-db.sh hardening
#   infra/scripts/verify-db.sh tenancy-preflight
#   infra/scripts/verify-db.sh restore-compare <iso-8601-timestamp-with-offset>
#
# The second argument is the check set's parameter: a school uuid for the
# school sets, the restore target time for restore-compare (with a UTC
# offset, e.g. 2026-11-17T06:00:00+03:00 — a naive time is refused). It is sent
# as both `school_id` and `timestamp`; the Lambda reads the one the set binds.
#
# Exit status, after the answer is printed:
#   0  the Lambda answered status "ok"
#   1  the verify Lambda is missing or failed (FunctionError)
#   2  usage
#   3  the answer's status is not "ok" (unknown set, invalid or missing parameter)
#   4  --strict only: status "ok", but at least one check returned an `error`
#      row instead of rows (named on stderr)
#
# Expected values per check live in the deployment checklists:
# docs/work/validation/2026-08-20-fleet-plan-drafting.md and the plan's
# Operational Notes — this script reports observations; judgment is yours.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

STRICT=0
if [ "${1:-}" = "--strict" ]; then
  STRICT=1
  shift
fi
CHECKS="${1:-}"
PARAM="${2:-}"
USAGE="usage: $0 [--strict] <migrations|migration-011|baseline|post-apply|gps|hardening|tenancy-preflight> [school-uuid]
       $0 [--strict] restore-compare <iso-8601-timestamp-with-offset>"
# --strict anywhere but first would be sent as the parameter and ignored.
if [ -z "$CHECKS" ] || [ "$#" -gt 2 ] || [ "$CHECKS" = "--strict" ] || [ "$PARAM" = "--strict" ]; then
  echo "$USAGE" >&2
  exit 2
fi

VERIFY_FN="$(cfn_output "$BACKEND_STACK" "$BACKEND_REGION" VerifyFunctionName || true)"
if [ -z "$VERIFY_FN" ] || [ "$VERIFY_FN" = "None" ]; then
  echo "ERROR: VerifyFunctionName not found on stack $BACKEND_STACK — deploy the stack revision that adds the verify Lambda first." >&2
  exit 1
fi

# JSON built by python3, so no argument can break the payload's quoting.
PAYLOAD="$(python3 -c '
import json
import sys

payload = {"checks": sys.argv[1]}
if sys.argv[2]:
    payload["school_id"] = payload["timestamp"] = sys.argv[2]
print(json.dumps(payload))
' "$CHECKS" "$PARAM")"

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
STATUS=$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$VERIFY_FN" \
  --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" \
  "$OUT" --query 'FunctionError' --output text)

if [ "$STATUS" != "None" ]; then
  echo "ERROR: verify Lambda failed ($STATUS):" >&2
  cat "$OUT" >&2; echo >&2
  exit 1
fi

# Print the answer (as `python3 -m json.tool` does), then the exit status above.
python3 - "$OUT" "$STRICT" <<'PY'
import json
import sys

path, strict = sys.argv[1], sys.argv[2] == "1"
with open(path, encoding="utf-8") as handle:
    text = handle.read()
try:
    payload = json.loads(text)
except ValueError:
    print(text)
    print("verify-db: the answer is not JSON", file=sys.stderr)
    sys.exit(3)
print(json.dumps(payload, indent=4))
if not isinstance(payload, dict) or payload.get("status") != "ok":
    status = payload.get("status") if isinstance(payload, dict) else None
    reason = payload.get("reason") if isinstance(payload, dict) else None
    print("verify-db: status is {!r}, not 'ok'{}".format(status, ": " + reason if reason else ""),
          file=sys.stderr)
    sys.exit(3)
if strict:
    failed = [str(result.get("label", "?")) for result in payload.get("results") or []
              if isinstance(result, dict) and "error" in result]
    if failed:
        print("verify-db --strict: {} check(s) returned an error: {}".format(len(failed), ", ".join(failed)),
              file=sys.stderr)
        sys.exit(4)
PY
