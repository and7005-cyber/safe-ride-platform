#!/usr/bin/env bash
# Copy the five /saferide/* secrets the pre-hardening deploy read from the
# operator's default CLI region into $BACKEND_REGION (af-south-1), and prove
# the two copies equal by SHA-256 digest (infra/README.md, "Operator checklist
# before Release 1a", step 4; hardening plan U1, decision D15).
#
# Usage:
#   infra/scripts/copy-ssm-to-backend-region.sh           # report only (read-only)
#   infra/scripts/copy-ssm-to-backend-region.sh --apply   # copy what the target lacks, then report
#
# SOURCE_REGION is where the copies live today. Unset, it is the region an
# un-regioned AWS CLI call uses — the one the pre-hardening deploy (and so
# release/0) read: AWS_REGION, else AWS_DEFAULT_REGION, else
# `aws configure get region`; the header says which. The target is
# BACKEND_REGION from config.sh. Per parameter the script prints exactly one
# line:
#   <name>: present in both, values MATCH
#   <name>: present in both, values MISMATCH
#   <name>: missing in <target region>
#   <name>: missing in <source region>
#   <name>: missing in <source region> and <target region>
#   <name>: could not read in <region>: <AWS error>
#
# Secret handling: a value never reaches the terminal, a shell variable, a
# command-line argument or shell history. Each read is
# `aws ssm get-parameter --with-decryption --output json` piped straight into
# python3, which compares SHA-256 digests in-process. --apply re-reads the
# source the same way and has python3 write the put-parameter input (Name,
# Value, Type SecureString, no Overwrite) into a 0600 file inside a private
# temporary directory; `aws ssm put-parameter --cli-input-json file://…` reads
# it there, and the directory is removed on exit, whatever the exit path. It
# refuses to run while the CLI's cli_history is enabled, because the CLI would
# then store the decrypted responses in ~/.aws/cli/history.
#
# Never overwrites: only a parameter missing in the target is written, and a
# MISMATCH (or a read error) stops --apply before anything is written —
# resolve that one by hand. Exit 0 only when every parameter ends MATCH.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

NAMES=(
  /saferide/db-password
  /saferide/pin-pepper
  /saferide/db-app-password
  /saferide/totp-pepper
  /saferide/google-maps-api-key
)

usage() {
  echo "usage: $0 [--apply]   (SOURCE_REGION defaults to the CLI's region: AWS_REGION, AWS_DEFAULT_REGION, \`aws configure get region\`)"
}

APPLY=0
case "${1:-}" in
  "") ;;
  --apply) APPLY=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  usage >&2
  exit 2
fi

export AWS_PAGER=""
if [ -n "${SOURCE_REGION:-}" ]; then
  SOURCE_ORIGIN="SOURCE_REGION"
elif [ -n "${AWS_REGION:-}" ]; then
  SOURCE_REGION="$AWS_REGION"; SOURCE_ORIGIN="AWS_REGION"
elif [ -n "${AWS_DEFAULT_REGION:-}" ]; then
  SOURCE_REGION="$AWS_DEFAULT_REGION"; SOURCE_ORIGIN="AWS_DEFAULT_REGION"
else
  SOURCE_REGION="$(aws configure get region 2>/dev/null || true)"; SOURCE_ORIGIN="aws configure get region"
fi
if [ -z "$SOURCE_REGION" ]; then
  echo "ERROR: no source region — set SOURCE_REGION (the region the old copies live in)." >&2
  exit 2
fi
if [ "$SOURCE_REGION" = "$BACKEND_REGION" ]; then
  echo "ERROR: SOURCE_REGION and BACKEND_REGION are both $BACKEND_REGION — nothing to compare." >&2
  exit 2
fi
if [ "$(aws configure get cli_history 2>/dev/null || true)" = "enabled" ]; then
  echo "ERROR: the AWS CLI's cli_history is enabled, so it would store the decrypted values in" >&2
  echo "       ~/.aws/cli/history. Turn it off (aws configure set cli_history disabled) and re-run." >&2
  exit 2
fi

# Private scratch space: AWS error text and exit codes, and (with --apply) the
# put-parameter input. umask 077 makes every file in it 0600.
umask 077
TMP_ROOT="${TMPDIR:-/tmp}"
WORK_DIR="$(mktemp -d "${TMP_ROOT%/}/saferide-ssm-copy.XXXXXX")"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT
# Every catchable signal goes through exit, so the EXIT trap removes the
# directory (bash runs no EXIT trap when an untrapped signal kills it).
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 141' PIPE
trap 'exit 143' TERM

# python3 compare: argv = name source target, then for each region the
# get-parameter JSON (a pipe), its stderr file and its exit-status file. It
# prints the one status line and exits 0 MATCH, 3 MISMATCH, 4 missing in the
# target, 5 missing in the source, 6 missing in both, 7 read error.
IFS= read -r -d '' COMPARE_PY <<'PY' || true
import hashlib
import hmac
import json
import sys

name, source, target = sys.argv[1:4]


def read(out_path, err_path, rc_path):
    with open(out_path, "rb") as handle:  # the pipe: EOF means the read is done
        body = handle.read()
    try:
        with open(rc_path) as handle:
            rc = int(handle.read().strip())
    except (OSError, ValueError):
        rc = -1
    try:
        with open(err_path, errors="replace") as handle:
            err = handle.read().strip()
    except OSError:
        err = ""
    if rc == 0:
        try:
            parameter = json.loads(body)["Parameter"]
            if parameter["Name"] != name:
                raise ValueError("another parameter")
            return "present", hashlib.sha256(parameter["Value"].encode("utf-8")).digest()
        except (ValueError, KeyError, TypeError):
            return "error", "unreadable get-parameter output"
    if "ParameterNotFound" in err:
        return "missing", None
    lines = [line.strip() for line in err.splitlines() if line.strip()]
    named = [line for line in lines if "error occurred" in line.lower()]
    return "error", (named or lines or ["aws exited {}".format(rc)])[-1]


src_state, src_value = read(*sys.argv[4:7])
tgt_state, tgt_value = read(*sys.argv[7:10])

if "error" in (src_state, tgt_state):
    for region, state, detail in ((source, src_state, src_value), (target, tgt_state, tgt_value)):
        if state == "error":
            print("{}: could not read in {}: {}".format(name, region, detail))
    sys.exit(7)
if src_state == "present" and tgt_state == "present":
    if hmac.compare_digest(src_value, tgt_value):
        print("{}: present in both, values MATCH".format(name))
        sys.exit(0)
    print("{}: present in both, values MISMATCH".format(name))
    sys.exit(3)
if src_state == "present":
    print("{}: missing in {}".format(name, target))
    sys.exit(4)
if tgt_state == "present":
    print("{}: missing in {}".format(name, source))
    sys.exit(5)
print("{}: missing in {} and {}".format(name, source, target))
sys.exit(6)
PY

# python3 put input: argv = name, output file; stdin = the source's
# get-parameter JSON. Writes {"Name", "Value", "Type": "SecureString"} — no
# Overwrite, so put-parameter refuses a name that exists.
IFS= read -r -d '' BUILD_PUT_PY <<'PY' || true
import json
import os
import sys

name, path = sys.argv[1:3]
parameter = json.load(sys.stdin)["Parameter"]
if parameter["Name"] != name:
    sys.exit("get-parameter answered for another parameter")
os.chmod(path, 0o600)
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"Name": name, "Value": parameter["Value"], "Type": "SecureString"}, handle)
PY

read_param() { # name region tag -> get-parameter JSON on stdout; stderr and exit status into WORK_DIR
  local rc=0
  aws ssm get-parameter --name "$1" --region "$2" --with-decryption --output json \
    2>"$WORK_DIR/$3.err" || rc=$?
  echo "$rc" >"$WORK_DIR/$3.rc"
}

compare() { # name -> prints the status line; returns its code (see COMPARE_PY)
  local name="$1"
  rm -f "$WORK_DIR/src.err" "$WORK_DIR/src.rc" "$WORK_DIR/tgt.err" "$WORK_DIR/tgt.rc"
  python3 -c "$COMPARE_PY" "$name" "$SOURCE_REGION" "$BACKEND_REGION" \
    <(read_param "$name" "$SOURCE_REGION" src) "$WORK_DIR/src.err" "$WORK_DIR/src.rc" \
    <(read_param "$name" "$BACKEND_REGION" tgt) "$WORK_DIR/tgt.err" "$WORK_DIR/tgt.rc"
}

copy_param() { # name -> creates it in BACKEND_REGION from SOURCE_REGION; never overwrites
  local name="$1" put_file
  put_file="$(mktemp "$WORK_DIR/put.XXXXXX")"
  if ! aws ssm get-parameter --name "$name" --region "$SOURCE_REGION" --with-decryption --output json \
      2>"$WORK_DIR/copy.err" | python3 -c "$BUILD_PUT_PY" "$name" "$put_file"; then
    rm -f "$put_file"
    echo "ERROR: could not read $name in $SOURCE_REGION to copy it: $(tail -n 1 "$WORK_DIR/copy.err" 2>/dev/null)" >&2
    return 1
  fi
  if ! aws ssm put-parameter --region "$BACKEND_REGION" --cli-input-json "file://$put_file" \
      --output json >/dev/null 2>"$WORK_DIR/copy.err"; then
    rm -f "$put_file"
    echo "ERROR: put-parameter $name ($BACKEND_REGION) failed: $(tail -n 1 "$WORK_DIR/copy.err" 2>/dev/null)" >&2
    return 1
  fi
  rm -f "$put_file"
  echo "==> Copied $name to $BACKEND_REGION (SecureString)"
}

MODE="report only, read-only"
[ "$APPLY" = 1 ] && MODE="apply: copy what $BACKEND_REGION lacks"
echo "==> /saferide/* secrets: source $SOURCE_REGION -> target $BACKEND_REGION ($MODE)"
echo "    source region from $SOURCE_ORIGIN"

STATUS=()
for i in "${!NAMES[@]}"; do
  code=0
  compare "${NAMES[$i]}" || code=$?
  STATUS[$i]=$code
done

if [ "$APPLY" = 1 ]; then
  blocked=0
  for i in "${!NAMES[@]}"; do
    case "${STATUS[$i]}" in
      3)
        blocked=1
        echo "ERROR: ${NAMES[$i]} differs between $SOURCE_REGION and $BACKEND_REGION." >&2
        echo "       Resolve it by hand: find which value the live stack uses (the database's master" >&2
        echo "       password, the pepper behind the stored PIN and TOTP hashes) — this script never" >&2
        echo "       overwrites." >&2
        ;;
      0|4|5|6) ;;
      *) blocked=1 ;;
    esac
  done
  if [ "$blocked" = 1 ]; then
    echo "ERROR: nothing was written (a mismatch or read error above must be resolved first)." >&2
    exit 1
  fi
  for i in "${!NAMES[@]}"; do
    [ "${STATUS[$i]}" = 4 ] || continue
    copy_param "${NAMES[$i]}" || exit 1
    code=0
    compare "${NAMES[$i]}" || code=$?
    STATUS[$i]=$code
  done
fi

failed=0
for i in "${!NAMES[@]}"; do
  [ "${STATUS[$i]}" = 0 ] || failed=$((failed + 1))
done
if [ "$failed" -gt 0 ]; then
  echo "==> $failed of ${#NAMES[@]} parameters do not MATCH." >&2
  [ "$APPLY" = 1 ] || echo "    Run with --apply to copy the ones missing in $BACKEND_REGION." >&2
  exit 1
fi
echo "==> All ${#NAMES[@]} parameters MATCH."
