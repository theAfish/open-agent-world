"""Prepare a source checkout explicitly; normal dev/start never install packages."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def backend_python(root: Path = ROOT) -> Path:
    return root / "backend/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError("Profile must contain 1-64 letters, digits, underscores or hyphens")
    return value


def prerequisites(*, install: bool, warn: bool = True) -> dict[str, str]:
    names = ["node", "npm.cmd" if os.name == "nt" else "npm"]
    if install:
        names.append("uv")
    found = {name: shutil.which(name) for name in names}
    missing = [name for name, path in found.items() if path is None]
    if missing:
        raise RuntimeError("Missing prerequisite: " + ", ".join(missing))
    version = subprocess.check_output([found["node"], "--version"], text=True).strip()
    major = int(version.lstrip("v").split(".")[0])
    minimum = 22
    if major < minimum:
        raise RuntimeError(f"Node.js {minimum} or newer is required for {'setup' if install else 'dev'}; found {version}")
    if major < 24 and warn:
        print(f"Using Node {version}. The current matterviz dependency declares Node >=24; "
              "continuing with Node 22 compatibility support (npm may report an engine warning).", file=sys.stderr, flush=True)
    found["npm"] = found[names[1]]
    return found


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=profile_name, help="Prepare this development profile instead of production data")
    parser.add_argument("--plugin-path", action="append", type=Path, default=[], help="Additional trusted source plugin (repeatable)")
    parser.add_argument("--skip-build", action="store_true", help="Prepare for dev without building the production frontend")
    args = parser.parse_args(argv)
    paths = []
    for path in args.plugin_path:
        path = (ROOT / path).resolve()
        if not (path / "pyproject.toml").is_file():
            parser.error(f"Plugin path must contain a pyproject.toml: {path}")
        paths.append(path)
    commands = prerequisites(install=True)
    # Keep trusted editable plugins; a host sync must not uninstall them.
    subprocess.run([commands["uv"], "sync", "--project", str(ROOT / "backend"), "--locked",
                    "--dev", "--extra", "adk", "--extra", "litellm", "--inexact"], cwd=ROOT, check=True)
    python = backend_python()
    if not python.is_file():
        raise RuntimeError(f"Backend Python is missing after sync: {python}")
    install = [str(python), str(ROOT / "scripts/install-plugins.py")]
    if args.profile:
        install += ["--profile", args.profile]
    for path in paths:
        install += ["--additional-path", str(path)]
    subprocess.run(install, cwd=ROOT, check=True)
    frontend = ROOT / "frontend"
    # This checker uses only Node built-ins, so it also works before npm ci.
    ready = subprocess.run([commands["node"], str(frontend / "scripts/source-packs.mjs"), "--check-host"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if ready:
        print("Reusing installed frontend dependencies.", flush=True)
    else:
        subprocess.run([commands["npm"], "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                       cwd=frontend, check=True)
    if args.skip_build:
        for script in ("build:pack-sdk", "prepare:packs"):
            subprocess.run([commands["npm"], "run", script], cwd=frontend, check=True)
    else:
        # prebuild prepares the SDK and Pack dependencies once.
        subprocess.run([commands["npm"], "run", "build"], cwd=frontend, check=True)
    print(f"Setup complete ({'development profile: ' + args.profile if args.profile else 'production'}).", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
