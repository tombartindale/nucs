#!/usr/bin/env bash
# Zips up a theme directory here, ready to drop into the Theme page (Settings -> Manage
# theme) on a running server.
#
#   ./package-theme.sh            packages themes/default
#   ./package-theme.sh custom     packages themes/custom
#
# Writes <name>-theme.zip next to this script, with theme.toml etc. at the zip's top
# level (not nested in a subfolder) -- that's what the server's upload expects.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="${1:-default}"
dir="$here/$name"

if [[ ! -f "$dir/theme.toml" ]]; then
  echo "error: no theme.toml in $dir -- pass the name of a theme folder here (e.g. custom)." >&2
  exit 1
fi

out="$here/$name-theme.zip"
rm -f "$out"
(cd "$dir" && zip -rq "$out" . -x ".*")

echo "Wrote $out"
