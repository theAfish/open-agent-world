#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --project backend --dev --extra adk --extra litellm
npm --prefix frontend ci --ignore-scripts --no-audit --no-fund
