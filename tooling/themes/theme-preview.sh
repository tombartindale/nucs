#!/usr/bin/env bash
# Live preview of a slide theme, using the same pinned Marp as bcn render.
#
#   tooling/themes/theme-preview.sh                 the default theme
#   tooling/themes/theme-preview.sh northumbria     another bundled theme (tooling/themes/<name>)
#   PORT=8100 tooling/themes/theme-preview.sh       a different port (default 8099)
#
# Opens tooling/themes/theme-preview.md in the browser; save the theme's CSS or the
# preview deck and the page reloads. Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

THEME="${1:-default}"
DIR="tooling/themes/$THEME"
MARP="tooling/node/node_modules/.bin/marp"
export PORT="${PORT:-8099}"   # marp's server reads PORT; it ignores --port
URL="http://localhost:$PORT/theme-preview.md"

[ -x "$MARP" ] || { echo "Marp not installed: run scripts/setup.sh first." >&2; exit 1; }
# Node 22, as the pipeline pins it (tooling/bcn/tools.py): from PATH, else via nvm.
if ! command -v node >/dev/null 2>&1 && [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  # shellcheck source=/dev/null
  source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  nvm use 22 >/dev/null
fi
command -v node >/dev/null 2>&1 || { echo "node not found: install Node 22 (e.g. nvm install 22)." >&2; exit 1; }
[ -f "$DIR/theme.toml" ] || { echo "No theme at $DIR." >&2; exit 1; }

NAME=$(sed -n 's#.*@theme[[:space:]]\{1,\}\([^[:space:]*]*\).*#\1#p' "$DIR"/*.css | head -1)
echo "Theme: $DIR (registers as '$NAME': the preview deck needs 'theme: $NAME' in its front matter)"
echo "Preview: $URL"

( sleep 2; open "$URL" ) &
exec "$MARP" --no-config-file --allow-local-files --theme-set "$DIR" -s tooling/themes
