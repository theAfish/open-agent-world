#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Build the macOS installer on a Mac (or the macOS GitHub Actions runner)." >&2
  exit 1
fi
# GitHub expands unset secrets/variables to empty strings. Tauri distinguishes
# an absent credential from an empty one, so do not request certificate import
# or notarization for a credential-free preview. Keep an empty P12 password:
# it is valid when an actual certificate is supplied.
for credential in APPLE_CERTIFICATE APPLE_SIGNING_IDENTITY APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID; do
  if [[ -z "${!credential:-}" ]]; then
    unset "$credential"
  fi
done
for tool in uv npm cargo python3; do
  command -v "$tool" >/dev/null || { echo "Missing build prerequisite: $tool" >&2; exit 1; }
done
export UV_CACHE_DIR="$PWD/.uv-cache"
npm --prefix frontend ci --ignore-scripts --no-audit --no-fund
npm --prefix frontend run build
python3 scripts/package-backend.py
npm --prefix desktop ci --ignore-scripts --no-audit --no-fund
if [[ -n "${OPEN_AGENT_WORLD_TAURI_BUNDLES:-}" ]]; then
  (cd desktop && npm run build -- --bundles "$OPEN_AGENT_WORLD_TAURI_BUNDLES")
else
  (cd desktop && npm run build)
fi
app="$PWD/desktop/src-tauri/target/release/bundle/macos/Open Agent World.app"
# Exercise the installed resource layout, including spaces and relocated Python.
payload="$app/Contents/Resources/payload"
PATH="$payload/tools:$PATH" "$payload/python/bin/python3" -I -B "$payload/launch.py" --self-test
codesign --verify --deep --strict "$app"
if [[ "${OPEN_AGENT_WORLD_MANUAL_DMG:-}" == "1" ]]; then
  dmg_dir="$PWD/desktop/src-tauri/target/release/bundle/dmg"
  mkdir -p "$dmg_dir"
  dmg="$dmg_dir/Open Agent World_x64.dmg"
  (
    staging_dir="$(mktemp -d -t open-agent-world-dmg)"
    trap 'rm -rf -- "$staging_dir"' EXIT
    ditto "$app" "$staging_dir/Open Agent World.app"
    hdiutil create -volname "Open Agent World" -srcfolder "$staging_dir" -ov -format UDZO "$dmg"
  )
fi
echo "Installer: desktop/src-tauri/target/release/bundle/dmg/"
