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
# Before invoking, the script checks that the verify function reads this
# stack's database: the host of its DATABASE_URL must be the stack's DbEndpoint
# output (verify_target_guard, config.sh), so a function left pointing at a
# scratch restore (infra/RESTORE.md, step B) is never read as production.
# ALLOW_VERIFY_TARGET_MISMATCH=1 reads it anyway, with a loud warning — the
# restore rehearsal's own reads. Only the host is read; the URL is never printed.
#
# Exit status (any non-zero exit is a No-Go):
#   0  the Lambda answered status "ok"
#   1  the verify Lambda is missing or failed (FunctionError)
#   2  usage
#   3  the answer's status is not "ok" (unknown set, invalid or missing
#      parameter), or the answer is not JSON
#   4  --strict only: status "ok", but at least one check returned an `error`
#      row instead of rows (named on stderr)
#   5  the verify function does not read this stack's database (its
#      DATABASE_URL host is not the DbEndpoint output, or either could not be
#      read), and ALLOW_VERIFY_TARGET_MISMATCH=1 is not set; nothing is invoked
#   other  the `aws lambda invoke` call itself failed (credentials, network,
#      throttling): the AWS CLI's own exit code, e.g. 254 or 255; no answer
#      was read
# Codes 0, 3 and 4 come after the answer is printed.
#
# Expected values per check live in the deployment checklists:
# docs/runbooks/hardening-release-checklists.md (hardening, migrations),
# docs/work/validation/2026-08-20-fleet-plan-drafting.md and the plans'
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
verify_target_guard "$VERIFY_FN" || exit 5

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
# --cli-read-timeout 0: the CLI's default 60 s read timeout equals the verify
# Lambda's Timeout, so a slow check set would time the CLI out (and retry the
# invoke) just as the function answers.
AWS_RC=0
STATUS=$(aws lambda invoke --region "$BACKEND_REGION" --function-name "$VERIFY_FN" \
  --cli-read-timeout 0 --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" \
  "$OUT" --query 'FunctionError' --output text) || AWS_RC=$?
if [ "$AWS_RC" -ne 0 ]; then
  echo "ERROR: aws lambda invoke failed (aws exit $AWS_RC, see the AWS error above): no answer was read." >&2
  exit "$AWS_RC"
fi

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
