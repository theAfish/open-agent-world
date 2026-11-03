#!/usr/bin/env bash
# Rebuild the Core frontend and the Knowledge Base plugin frontend after source edits.
# Dependencies are not installed; run scripts/setup.sh for that.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Same steps and environment as setup.py: Core only, without the all-Pack prebuild hook.
export OPEN_AGENT_WORLD_CORE_ONLY=1
npm --prefix "$root/frontend" run build:pack-sdk
npm --prefix "$root/frontend" --ignore-scripts run build
npm --prefix "$root/plugins/knowledge_base/frontend" run build
