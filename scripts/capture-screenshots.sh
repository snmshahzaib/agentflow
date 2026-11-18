#!/usr/bin/env bash
# Capture the documentation screenshots from the running application.
#
#   scripts/capture-screenshots.sh
#
# Brings up the offline stack, drives the real UI, and writes PNGs to
# docs/screenshots/. Every image is of the actual application showing real
# rows, and carries the "Offline demo mode" banner so a reader can see the
# content is fixture output rather than a live model's.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

scripts/e2e-stack.sh up

printf '\n== capturing\n'
cd frontend
pnpm exec playwright test --config=playwright.screenshots.config.ts

printf '\n== written\n'
ls -la "$ROOT/docs/screenshots" | tail -n +2 | awk '{printf "  %-28s %s\n", $9, $5}'
