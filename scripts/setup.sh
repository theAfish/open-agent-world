#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

for tool in uv node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing prerequisite: $tool. Install uv and Node.js 20 or newer (including npm), then rerun this script." >&2
    exit 1
  fi
done
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)'; then
  echo "Node.js 20 or newer is required." >&2
  exit 1
fi

uv sync --project backend --dev --extra adk --extra litellm
npm --prefix frontend ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend run build
printf '\nSetup complete. Start Open Agent World with:\n  bash "%s/scripts/start.sh"\n' "$PWD"
