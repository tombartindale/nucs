#!/usr/bin/env bash
# Start the Docker Compose stack for local development: no login, no Caddy/TLS, no S3
# backup service, no SendGrid — only docker-compose.dev.yml's overrides on top of the
# real stack (Postgres, Redis, the worker, the actual bcn pipeline all run as they would
# in production). See docker-compose.dev.yml for exactly what's disabled and why.
#
#   ./start-dev.sh            build and start, then follow logs
#   ./start-dev.sh --no-logs  build and start, then exit
#
# The app is reachable at http://localhost:8420 once it's up.
set -euo pipefail
cd "$(dirname "$0")"

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

echo
echo "NUCS (dev mode, no login): http://localhost:8420"
echo

if [ "${1:-}" != "--no-logs" ]; then
  docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f api worker
fi
