#!/usr/bin/env bash
# Full certification: every suite that proves the app works.
# Requires the local stack (scripts/start-local.sh) and the Python venv at .venv.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
cd "$ROOT_DIR"

if [ ! -x "$PYTHON" ]; then
  echo "Missing venv. Create it with: python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

if ! curl -fsS http://localhost:9001/api/health > /dev/null 2>&1; then
  echo "Local stack is not running. Start it with scripts/start-local.sh" >&2
  exit 1
fi

# Fresh API process: in-process rate-limiter budgets (signups per hour etc.)
# must not accumulate across repeated certification runs.
echo "==> Restarting API (reset in-process rate limits)"
docker compose -f "$ROOT_DIR/docker-compose.local.yml" restart api > /dev/null
for _ in $(seq 1 30); do
  curl -fsS http://localhost:9001/api/health > /dev/null 2>&1 && break
  sleep 1
done

echo "==> Backend unit tests"
(cd backend && "$PYTHON" -m pytest -q)

echo "==> Backend API integration tests (live stack)"
(cd backend && RUN_INTEGRATION=1 "$PYTHON" -m pytest tests/integration -q)

echo "==> Frontend typecheck"
(cd frontend && npx tsc --noEmit)

echo "==> Frontend unit tests"
(cd frontend && npm test)

echo "==> Frontend production build"
(cd frontend && npx vite build)

echo "==> End-to-end browser suite (Playwright)"
(cd frontend && PLAYWRIGHT_BASE_URL=http://localhost:5173 npx playwright test)

echo "==> Restoring canonical demo seed"
"$ROOT_DIR/scripts/reset-local-db.sh" > /dev/null

echo
echo "✅ Certification complete: backend unit, API integration, typecheck, unit, build, and e2e all green."
