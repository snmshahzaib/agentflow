#!/usr/bin/env bash
# Runs a command inside the backend's uv environment.
#
# Git hooks get a minimal PATH that often excludes uv's install location, so
# resolve the binary explicitly instead of relying on the caller's shell.
#
#   usage: scripts/uv-run.sh mypy app
set -euo pipefail

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    [ -x "$c" ] && { echo "$c"; return; }
  done
  return 1
}

if ! UV="$(find_uv)"; then
  echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "       then re-run, or add uv to PATH in your shell profile." >&2
  exit 1
fi

cd "$(dirname "$0")/../backend"
exec "$UV" run "$@"
