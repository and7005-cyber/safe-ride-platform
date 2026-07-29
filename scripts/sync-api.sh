#!/usr/bin/env bash
# Sync the backend working tree into the API container and restart it.
#
# The API container runs a baked image with no bind mount, so editing
# backend/app changes nothing the running API sees. Without this, tests pass
# against stale code — a green suite that proves nothing.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
docker compose -f docker-compose.local.yml cp backend/app api:/app/ > /dev/null
docker compose -f docker-compose.local.yml restart api > /dev/null
for _ in $(seq 1 30); do
  curl -fsS http://localhost:9001/api/health > /dev/null 2>&1 && { echo "API synced and healthy."; exit 0; }
  sleep 1
done
echo "API did not come back — check 'docker compose -f docker-compose.local.yml logs api'." >&2
exit 1
