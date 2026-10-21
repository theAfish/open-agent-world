"""Prepare a source checkout explicitly; normal dev/start never install packages."""
from __future__ import annotations

import json
import tempfile
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


def main(argv=None) -> int | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=profile_name, help="Prepare this development profile instead of production data")
    parser.add_argument("--plugin-path", action="append", type=Path, default=[], help="Additional trusted source plugin (repeatable)")
    parser.add_argument("--skip-build", action="store_true", help="Prepare for dev without building the production frontend")
    parser.add_argument("--core-only", action="store_true", help="Compatibility alias for the default Core-only preparation")
    parser.add_argument("--with-plugins", action="store_true", help="Explicitly prepare all official Packs and optional Agent SDKs")
    args = parser.parse_args(argv)
    if args.core_only and (args.plugin_path or args.with_plugins):
        parser.error("--core-only cannot be combined with --plugin-path or --with-plugins")
    paths = []
    for path in args.plugin_path:
        path = (ROOT / path).resolve()
        if not (path / "pyproject.toml").is_file():
            parser.error(f"Plugin path must contain a pyproject.toml: {path}")
        paths.append(path)
    commands = prerequisites(install=True)
    # Keep trusted editable plugins; a host sync must not uninstall them.
    extras = ["--extra", "adk", "--extra", "litellm"] if args.with_plugins else []
    subprocess.run([commands["uv"], "sync", "--project", str(ROOT / "backend"), "--locked",
                    "--dev", *extras, "--inexact"], cwd=ROOT, check=True)
    python = backend_python()
    if not python.is_file():
        raise RuntimeError(f"Backend Python is missing after sync: {python}")
    install = [str(python), str(ROOT / "scripts/install-plugins.py"), "--continue-on-error"]
    if args.profile:
        install += ["--profile", args.profile]
    for path in paths:
        install += ["--additional-path", str(path)] if args.with_plugins else [str(path)]
    pack_results = None
    if args.with_plugins or paths:
        with tempfile.TemporaryDirectory(prefix="oaw-setup-") as directory:
            report = Path(directory) / "packs.json"
            result = subprocess.run([*install, "--report", str(report)], cwd=ROOT)
            if result.returncode not in (0, 3) or not report.is_file():
                raise RuntimeError("Optional Pack installer failed before reporting results; Core backend dependencies are prepared. Retry scripts/install-plugins.py.")
            pack_results = json.loads(report.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="oaw-frontend-setup-") as directory:
        frontend_report = Path(directory) / "failures.jsonl"
        build_env = os.environ.copy() if args.with_plugins else {**os.environ, "OPEN_AGENT_WORLD_CORE_ONLY": "1"}
        build_env["OAW_ALLOW_PACK_FAILURES"] = "1"
        build_env["OAW_PACK_FAILURE_REPORT"] = str(frontend_report)
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
                subprocess.run([commands["npm"], "run", script], cwd=frontend, env=build_env, check=True)
        else:
            # prebuild prepares the SDK and Pack dependencies once.
            subprocess.run([commands["npm"], "run", "build"], cwd=frontend, env=build_env, check=True)
        frontend_failures = [json.loads(line) for line in frontend_report.read_text(encoding="utf-8").splitlines()] if frontend_report.is_file() else []
    for failure in frontend_failures:
        row = next((row for row in pack_results or [] if failure['id'] in (row['id'], row.get('owner'))), None)
        if row is not None:
            row['ready'] = False
            row['error'] += "\nFrontend: " + failure['error']
            row['frontend'] = True
        else:
            if pack_results is None:
                pack_results = []
            pack_results.append({**failure, 'ready': False, 'path': '', 'frontend': True})
    failed = [row for row in pack_results or [] if not row["ready"]]
    print("Core ready.", flush=True)
    if pack_results is None:
        print("Optional Packs: not requested (Core-only setup). Use --with-plugins to prepare them.", flush=True)
    else:
        print(f"Optional Packs: {len(pack_results) - len(failed)} ready, {len(failed)} failed", flush=True)
        for row in pack_results:
            print(f"  {row['id']}: {'FAILED' if not row['ready'] else 'ready'}", flush=True)
        for row in failed:
            print(f"Failed: {row['id']}\n{row['error']}\nRetry: " + subprocess.list2cmdline(
                ([str(python), str(ROOT / 'scripts/setup.py'), '--with-plugins'] if row.get('frontend') else
                 [str(python), str(ROOT / 'scripts/install-plugins.py'), row['path']]) +
                (['--profile', args.profile] if args.profile else [])), file=sys.stderr, flush=True)
    if failed:
        print("Setup partially complete; Core remains usable. Repair failed Packs and restart OAW.", file=sys.stderr, flush=True)
        return 3
    print(f"Setup complete ({'development profile: ' + args.profile if args.profile else 'production'}).", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit("Setup failed before Core readiness was confirmed: " + str(exc)) from exc
