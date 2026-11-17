#!/usr/bin/env sh
set -eu
oaw_install_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
oaw_install_python=
for oaw_candidate in "$oaw_install_root/python/bin/python3" "$oaw_install_root/backend/.venv/bin/python" \
    "$oaw_install_root/.open-agent-world/desktop-payload/python/bin/python3" python3 python3.12 python3.11 python3.13; do
    if command -v "$oaw_candidate" >/dev/null 2>&1 && "$oaw_candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        oaw_install_python=$oaw_candidate
        break
    fi
done
if [ -z "$oaw_install_python" ]; then
    oaw_uv=$(command -v uv 2>/dev/null) || oaw_uv=
    if [ -z "$oaw_uv" ] && [ -x "$HOME/.local/bin/uv" ]; then
        oaw_uv="$HOME/.local/bin/uv"
    fi
    if [ -n "$oaw_uv" ]; then
        oaw_install_python=$("$oaw_uv" python find --offline '>=3.11' 2>/dev/null) || oaw_install_python=
    fi
fi
if [ -z "$oaw_install_python" ] || ! "$oaw_install_python" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo 'OAW needs Python 3.11 or newer. Run bash scripts/setup.sh or use the desktop installation.' >&2
    exit 1
fi
exec "$oaw_install_python" -I -B "$oaw_install_root/scripts/install-cli.py" "$@"
