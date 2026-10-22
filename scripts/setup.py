"""Prepare Core first, then independent optional components; dev/start never install."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

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
    if major < 22:
        raise RuntimeError(f"Node.js 22 or newer is required for {'setup' if install else 'dev'}; found {version}")
    if major < 24 and warn:
        print(f"Using Node {version}. The current matterviz dependency declares Node >=24; "
              "continuing with Node 22 compatibility support (npm may report an engine warning).", file=sys.stderr, flush=True)
    found["npm"] = found[names[1]]
    return found


def run_logged(command, *, cwd=ROOT, env=None):
    """Stream both child channels and retain a bounded tail for failure summaries."""
    tail = ""
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace",
                          **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {})) as child:
        try:
            for line in child.stdout:
                print(line, end="", flush=True)
                tail = (tail + line)[-6000:]
            return subprocess.CompletedProcess(command, child.wait(), stdout=tail)
        except BaseException:
            child.terminate()
            child.wait()
            raise


def prepare_runtime(name, commands, python, *, verbose=False):
    # Use the existing lock, without syncing away another runtime or changing
    # Core pins. A failed optional SDK must not prevent the other SDK or Packs.
    with tempfile.TemporaryDirectory(prefix="oaw-runtime-setup-") as directory:
        requirements = str(Path(directory) / "requirements.txt")
        for command in (
            [commands["uv"], "export", "--project", str(ROOT / "backend"), "--locked", "--no-dev",
             "--extra", name, "--no-emit-project", "--output-file", requirements, "--quiet"],
            [commands["uv"], "pip", "install", "--python", str(python), "--require-hashes", "--no-deps",
             "-r", requirements, *(["--verbose"] if verbose else [])],
        ):
            result = run_logged(command)
            if result.returncode:
                return {"id": name, "ready": False, "error": result.stdout.strip() or f"Exit {result.returncode}"}
    return {"id": name, "ready": True, "error": ""}


def main(argv=None) -> int | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=profile_name, help="Prepare this development profile instead of production data")
    parser.add_argument("--plugin-path", action="append", type=Path, default=[], help="Prepare only these source projects (repeatable); with --with-plugins, add them to bundled Packs")
    parser.add_argument("--skip-build", action="store_true", help="Prepare for dev without building production assets")
    parser.add_argument("--core-only", action="store_true", help="Prepare only Core and its development tools")
    parser.add_argument("--with-plugins", action="store_true", help="Prepare bundled Packs (the default), plus any --plugin-path projects")
    parser.add_argument("--runtime", action="append", choices=("adk", "litellm"), help="Select optional runtime SDKs; defaults to both with bundled Packs")
    parser.add_argument("--verbose", action="store_true", help="Show detailed dependency resolution output")
    args = parser.parse_args(argv)
    if args.core_only and (args.plugin_path or args.with_plugins or args.runtime):
        parser.error("--core-only cannot be combined with --plugin-path, --with-plugins or --runtime")
    paths = list(dict.fromkeys((ROOT / path).resolve() for path in args.plugin_path))
    # Missing/broken requested projects are optional failures, reported by the
    # per-Pack installer after Core is ready, alongside the remaining projects.
    commands = prerequisites(install=True)
    # Preserve prepared optional SDKs and trusted legacy editable plugins.
    subprocess.run([commands["uv"], "sync", "--project", str(ROOT / "backend"), "--locked", "--dev", "--inexact",
                    *(["--verbose"] if args.verbose else [])], cwd=ROOT, check=True)
    python = backend_python()
    if not python.is_file():
        raise RuntimeError(f"Backend Python is missing after sync: {python}")
    frontend = ROOT / "frontend"
    core_env = {**os.environ, "OPEN_AGENT_WORLD_CORE_ONLY": "1"}
    ready = subprocess.run([commands["node"], str(frontend / "scripts/source-packs.mjs"), "--check-host"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if ready:
        print("Reusing installed frontend dependencies.", flush=True)
    else:
        subprocess.run([commands["npm"], "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                       cwd=frontend, check=True)
    subprocess.run([commands["npm"], "run", "build:pack-sdk"], cwd=frontend, env=core_env, check=True)
    if not args.skip_build:
        # Core is usable before any optional preparation. Do not run prebuild's
        # all-Pack installation hook or rebuild Core after each Pack.
        subprocess.run([commands["npm"], "--ignore-scripts", "run", "build"], cwd=frontend, env=core_env, check=True)
    print("Core ready.", flush=True)
    if args.core_only:
        print("Optional components: not requested (Core-only setup).", flush=True)
        return

    bundled = args.with_plugins or not paths
    results = []
    for name in dict.fromkeys(args.runtime if args.runtime is not None else (["adk", "litellm"] if bundled else [])):
        print(f"Preparing optional runtime {name} ...", flush=True)
        try:
            result = prepare_runtime(name, commands, python, verbose=args.verbose)
        except (OSError, RuntimeError) as exc:
            result = {"id": name, "ready": False, "error": str(exc)}
        results.append(result)
        if not result["ready"]:
            print(f"Runtime {name} unavailable: {result['error']}", file=sys.stderr, flush=True)

    install = [str(python), str(ROOT / "scripts/install-plugins.py"), "--continue-on-error"]
    if args.profile:
        install += ["--profile", args.profile]
    if args.skip_build:
        install += ["--skip-build"]
    else:
        install += ["--frontend-output", str(frontend / "dist/assets/packs")]
    for path in paths:
        install += ["--additional-path", str(path)] if bundled else [str(path)]
    install_env = os.environ.copy()
    if args.verbose:
        install_env["OPEN_AGENT_WORLD_SETUP_VERBOSE"] = "1"
    with tempfile.TemporaryDirectory(prefix="oaw-setup-") as directory:
        report = Path(directory) / "packs.json"
        try:
            result = subprocess.run([*install, "--report", str(report)], cwd=ROOT, env=install_env)
            if result.returncode not in (0, 3) or not report.is_file():
                raise RuntimeError(f"Pack installer exited with code {result.returncode} before reporting results")
            results.extend(json.loads(report.read_text(encoding="utf-8")))
        except (OSError, ValueError, RuntimeError) as exc:
            results.append({"id": "Pack preparation", "ready": False, "error": str(exc)})
            print(f"Pack preparation unavailable: {exc}", file=sys.stderr, flush=True)
    failed = [row for row in results if not row["ready"]]
    print(f"Optional components: {len(results) - len(failed)} ready, {len(failed)} failed", flush=True)
    if failed:
        print("Setup partially complete; Core remains usable. Repair failed components through the Library or retry their preparation.", file=sys.stderr, flush=True)
        return 3
    print(f"Setup complete ({'development profile: ' + args.profile if args.profile else 'production'}).", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit("Setup failed before Core readiness was confirmed: " + str(exc)) from exc
