#!/usr/bin/env bash
# Full local dev environment: the backend (api, worker, Postgres, Redis) runs in Docker
# via docker-compose.dev.yml (no login, no Caddy/TLS, no S3 backup, no SendGrid — see that
# file for exactly what's disabled and why), while the Quasar frontend runs natively with
# `quasar dev` for real Vite hot-reload — editing a .vue/.ts file under ui/app/src updates
# the browser instantly, no container rebuild.
#
#   ./start-dev.sh             backend in Docker, frontend dev server in the foreground
#   ./start-dev.sh --backend   backend only, no frontend dev server (e.g. if you're
#                              running `quasar dev` yourself, or just want the API)
#
# Once up: the live-reloading app is at the URL `quasar dev` prints (typically
# http://localhost:9000); it proxies /api and /files to the Dockerized backend on :8420
# (see ui/app/quasar.config.ts's devServer.proxy). http://localhost:8420 also works
# directly but without hot reload (it serves the last `docker compose --build`'s SPA).
set -euo pipefail
cd "$(dirname "$0")"

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

echo
echo "Backend (no login): http://localhost:8420"

if [ "${1:-}" = "--backend" ]; then
  exit 0
fi

# ui/app's build tooling (@quasar/app-vite) needs Node >=22.22; fall back to nvm if the
# active node is older, since that's the common case on a machine with an older default.
NODE_OK=$(node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.stdout.write(a>22||(a===22&&b>=22)?"1":"0")' 2>/dev/null || echo 0)
if [ "$NODE_OK" != "1" ]; then
  if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
    # shellcheck source=/dev/null
    source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
    NVM_NODE=$(nvm ls --no-colors 2>/dev/null | grep -oE 'v22\.(2[2-9]|[3-9][0-9])\.[0-9]+' | sed 's/^v//' | sort -V | tail -1)
    if [ -n "$NVM_NODE" ]; then
      echo "Local node ($(node --version)) is older than 22.22; using nvm's $NVM_NODE for the frontend dev server."
      nvm use "$NVM_NODE" >/dev/null
    else
      echo "warning: no Node >=22.22 found via nvm either; 'npm run dev' below may fail. Install one with: nvm install 22" >&2
    fi
  else
    echo "warning: node $(node --version) is older than the 22.22 ui/app needs, and nvm isn't available to switch." >&2
    echo "         install Node >=22.22 (e.g. via nvm) and re-run, or use --backend and run the frontend yourself." >&2
  fi
fi

echo "Starting the frontend dev server (Ctrl-C to stop; the backend keeps running)..."
echo
cd ui/app
npm run dev
