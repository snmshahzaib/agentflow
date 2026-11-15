#!/usr/bin/env bash
# Refuses to let a real .env file enter a commit. *.example files are fine.
set -euo pipefail
bad=()
for f in "$@"; do
  base="$(basename "$f")"
  case "$base" in
    *.example) continue ;;
    .env|.env.*|*.env) bad+=("$f") ;;
  esac
done
if [ ${#bad[@]} -gt 0 ]; then
  echo "ERROR: refusing to commit environment file(s) that may contain secrets:" >&2
  printf '  %s\n' "${bad[@]}" >&2
  echo "Commit only *.example files. Remove with: git restore --staged <file>" >&2
  exit 1
fi
