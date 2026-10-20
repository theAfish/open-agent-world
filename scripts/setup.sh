#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo 'Missing prerequisite: uv.' >&2
  exit 1
fi
# Only the stdlib bootstrap uses uv run; plugins and the server use backend/.venv.
exec uv run --no-project --python '>=3.11' "$root/scripts/setup.py" "$@"
