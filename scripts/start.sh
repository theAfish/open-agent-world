#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/backend/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  echo "Backend environment is missing. Run: bash \"$root/scripts/setup.sh\"" >&2
  exit 1
fi

exec "$python" "$root/scripts/start.py" "$@"
