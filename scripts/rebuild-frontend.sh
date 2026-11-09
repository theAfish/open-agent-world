#!/usr/bin/env bash
# Rebuild the Core frontend and the Knowledge Base and XRD plugin frontends after source edits.
# Dependencies are not installed; run scripts/setup.sh for that.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Same steps and environment as setup.py: Core only, without the all-Pack prebuild hook.
export OPEN_AGENT_WORLD_CORE_ONLY=1
npm --prefix "$root/frontend" run build:pack-sdk
npm --prefix "$root/frontend" --ignore-scripts run build

# The Core build empties frontend/dist, so republish the Packs there like setup.py does.
OPEN_AGENT_WORLD_CORE_ONLY=0 OAW_ALLOW_PACK_FAILURES=0 node "$root/frontend/scripts/source-packs.mjs" \
  --frontend-path "$root/plugins/knowledge_base/frontend" --frontend-path "$root/plugins/xrd/frontend" \
  --build --output "$root/frontend/dist/assets/packs"
