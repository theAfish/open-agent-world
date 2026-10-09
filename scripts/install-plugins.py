"""Install trusted checkout plugins and their own declared Python dependencies.

Run after syncing the host. The loader only discovers code; installing packages
belongs in setup/build, never in an HTTP request or application startup.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def plugin_paths(directory: Path) -> list[Path]:
    return [manifest.parent for manifest in sorted(directory.glob("*/pyproject.toml"))
            if tomllib.loads(manifest.read_text(encoding="utf-8"))
            .get("project", {}).get("entry-points", {}).get("open_agent_world.plugins")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", type=Path, help="Install non-editable packages into a desktop payload")
    parser.add_argument("--constraint", type=Path, help="Preserve the locked host dependency versions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    paths = args.paths or plugin_paths(ROOT / "plugins")
    if not paths:
        return
    command = ["uv", "pip", "install", "--python", args.python]
    if args.dry_run:
        command.append("--dry-run")
    if args.constraint:
        command += ["--constraint", str(args.constraint)]
    if args.target:
        command += ["--target", str(args.target), *(str(path.resolve()) for path in paths)]
    else:
        for path in paths:
            command += ["--editable", str(path.resolve())]
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
