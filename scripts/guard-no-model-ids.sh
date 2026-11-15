#!/usr/bin/env bash
# AgentFlow must never hardcode a model ID in application code. Models are
# resolved from environment variables and validated against the provider
# catalogue at startup (docs/model-configuration.md).
#
# Allowed to contain model IDs:
#   - the LLM adapter's catalogue snapshot and its tests
#   - tests and fixtures that assert on configuration
set -euo pipefail
pattern='(anthropic|openai|google|meta-llama|mistralai|deepseek|x-ai|qwen|moonshotai|z-ai)/[a-z0-9][a-z0-9._-]*'
violations=0
for f in "$@"; do
  case "$f" in
    backend/app/adapters/llm/*) continue ;;
    backend/tests/*) continue ;;
  esac
  [ -f "$f" ] || continue
  if hits=$(grep -nE "['\"]${pattern}['\"]" "$f"); then
    echo "ERROR: hardcoded model ID in $f" >&2
    echo "$hits" | sed 's/^/  /' >&2
    violations=1
  fi
done
if [ "$violations" -ne 0 ]; then
  echo "Resolve models via app.core.config / app.adapters.llm.registry instead." >&2
  exit 1
fi
