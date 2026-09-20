#!/usr/bin/env bash
# Tenancy (U2): create the SSM inputs the deploy requires — the TOTP pepper
# and the provider bootstrap (two Kuumbai accounts with server-generated
# initial passwords). Prints SSM parameter NAMES only, never secrets; each
# initial password is stored as its own SecureString for the operator to
# retrieve out-of-band:
#   aws ssm get-parameter --name /saferide/provider-initial-password/<email> \
#     --with-decryption --query Parameter.Value --output text
#
# Usage: scripts/provider-bootstrap.sh <email1> "<Full Name 1>" <email2> "<Full Name 2>"
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/infra/scripts/config.sh"

if [ $# -ne 4 ]; then
  echo "Usage: $0 <email1> \"<Full Name 1>\" <email2> \"<Full Name 2>\"" >&2
  exit 1
fi

PY="$ROOT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# 1. TOTP pepper: create once, never rotate silently (rotation re-enrols all).
existing_pepper="$(aws ssm get-parameter --name /saferide/totp-pepper --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)"
if [ -z "$existing_pepper" ] || [ "$existing_pepper" = "None" ]; then
  aws ssm put-parameter --name /saferide/totp-pepper --type SecureString \
    --value "$(openssl rand -hex 32)" >/dev/null
  echo "==> Created SSM /saferide/totp-pepper"
else
  echo "==> SSM /saferide/totp-pepper already exists (unchanged)"
fi

# 2. Bootstrap payload: bump the version so the migrate Lambda re-keys the
# bootstrap accounts on the next deploy (the lockout-recovery path).
# Captured in two steps: under pipefail, a missing parameter would fail the
# whole pipeline and the || fallback would APPEND its 0 to python's 0.
raw_bootstrap="$(aws ssm get-parameter --name /saferide/provider-bootstrap \
  --region "$BACKEND_REGION" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
prev_version="$(printf '%s' "$raw_bootstrap" | "$PY" -c "
import base64, json, sys
raw = sys.stdin.read().strip()
if not raw or raw == 'None':
    print(0); raise SystemExit
if not raw.startswith('{'):
    raw = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode()
print(json.loads(raw).get('version', 0))
" 2>/dev/null || echo 0)"
version=$((prev_version + 1))

payload=""
for pair in "$1|$2" "$3|$4"; do
  email="${pair%%|*}"
  name="${pair#*|}"
  password="$(openssl rand -base64 18 | tr '+/' 'Aa')"
  hash="$(cd "$ROOT_DIR/backend" && "$ROOT_DIR/.venv/bin/python" -c "
from app.core.security import hash_password
print(hash_password('$password'))
")"
  # SSM parameter names allow only letters, digits and .-_ per path segment,
  # so the email is slugged for the NAME only; the bootstrap JSON keeps the
  # real address. The exact name is printed — copy it from here.
  email_slug="$(printf '%s' "$email" | sed 's/[^A-Za-z0-9._-]/-/g')"
  aws ssm put-parameter --name "/saferide/provider-initial-password/$email_slug" \
    --type SecureString --value "$password" --overwrite >/dev/null
  payload="$payload{\"email\": \"$email\", \"full_name\": \"$name\", \"password_hash\": \"$hash\"},"
  echo "==> Initial password stored at SSM /saferide/provider-initial-password/$email_slug"
done

json="{\"version\": $version, \"providers\": [${payload%,}]}"
encoded="$(printf '%s' "$json" | "$PY" -c "
import base64, sys
print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).decode().rstrip('='))
")"
aws ssm put-parameter --name /saferide/provider-bootstrap --type SecureString \
  --value "$encoded" --region "$BACKEND_REGION" --overwrite >/dev/null
echo "==> Wrote SSM /saferide/provider-bootstrap (version $version, $BACKEND_REGION)"
echo "Next deploy will create or re-key the provider accounts."
