"""Build a self-contained Windows Python payload from a clean interpreter and uv.lock."""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


def run(arguments, *, root, env, quiet=False):
    print("Running:", " ".join(str(part) for part in arguments), flush=True)
    subprocess.run([str(part) for part in arguments], cwd=root, env=env, check=True,
                   stdout=subprocess.DEVNULL if quiet else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="A full Windows CPython installation (not a frozen executable)")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("The installer pipeline currently targets Windows x64. Use scripts/start.py on Linux/macOS.")
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if not uv:
        parser.error("Install uv before building the desktop package")
    if not (root / "frontend/dist/index.html").is_file():
        parser.error("Build the frontend first")
    source_info = json.loads(subprocess.check_output([args.python, "-I", "-c",
        "import json,sys,struct; print(json.dumps({'base':sys.base_prefix,'version':sys.version,'bits':struct.calcsize('P')*8}))"], text=True))
    if source_info["bits"] != 64:
        parser.error("Build with a 64-bit Python interpreter")
    source = Path(source_info["base"])
    if not (source / "python.exe").is_file() or not (source / "Lib/venv").is_dir():
        parser.error("Use a full CPython installation with the standard library and venv module")
    build_root = root / ".open-agent-world"
    stage = build_root / ("desktop-staging-" + uuid4().hex[:10])
    stage.mkdir(parents=True)
    python_root = stage / "python"
    python_root.mkdir()
    for pattern in ("python*.exe", "python*.dll", "python*.zip", "vcruntime*.dll", "LICENSE*"):
        for item in source.glob(pattern):
            if item.is_file():
                shutil.copy2(item, python_root / item.name)
    for name in ("Lib", "DLLs"):
        if (source / name).is_dir():
            shutil.copytree(source / name, python_root / name,
                ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pth", "sitecustomize.py", "usercustomize.py", "test", "tests"))
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(root / ".uv-cache")
    env["UV_PYTHON_INSTALL_DIR"] = str(build_root / "build-python")
    requirements = stage / "requirements.txt"
    run([uv, "export", "--project", "backend", "--locked", "--no-dev", "--extra", "adk", "--extra", "litellm",
         "--no-emit-project", "--output-file", requirements], root=root, env=env, quiet=True)
    run([uv, "pip", "install", "--python", python_root / "python.exe", "--target", python_root / "Lib/site-packages",
         "--require-hashes", "--no-deps", "--only-binary", ":all:", "-r", requirements], root=root, env=env)
    # Honor repository ignores, including local .env files, caches and user data.
    # New implementation files are included even before a developer commits them.
    source_files = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                            "--", "backend", "open_agent_world", "plugins"], cwd=root).decode("utf-8").split("\0")
    for name in set(source_files) - {""}:
        relative = Path(name)
        if "tests" in relative.parts or name == "backend/development.py":
            continue
        source_file = root / relative
        if not source_file.is_file():
            continue
        if source_file.is_symlink() or not source_file.resolve().is_relative_to(root):
            raise RuntimeError(f"Package source must be a regular checkout file: {relative}")
        target_file = stage / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
    # Debug reset code is neither imported nor shipped with the installed backend.
    shutil.copytree(root / "frontend/dist", stage / "frontend/dist")
    (stage / "tools").mkdir()
    shutil.copy2(uv, stage / "tools/uv.exe")
    shutil.copy2(root / "scripts/desktop-entry.py", stage / "launch.py")
    (stage / "build-info.json").write_text(json.dumps({"built_at": datetime.now(UTC).isoformat(),
        "python": source_info["version"], "platform": "windows-x64", "backend_dependencies": "requirements.txt"}, indent=2), encoding="utf-8")
    env["PATH"] = str(stage / "tools") + os.pathsep + env.get("PATH", "")
    run([python_root / "python.exe", "-I", "-B", stage / "launch.py", "--self-test"], root=stage, env=env)
    destination = build_root / "desktop-payload"
    # Both paths are fixed build outputs in this checkout; keep the previous package
    # until the newly staged interpreter, plugins, and Sandbox venv passed validation.
    if destination.exists():
        if destination.is_symlink() or destination.resolve().parent != build_root.resolve():
            raise RuntimeError("Unexpected desktop payload path")
        destination.rename(build_root / ("desktop-previous-" + uuid4().hex[:10]))
    stage.rename(destination)
    print(f"Desktop payload ready: {destination}", flush=True)


if __name__ == "__main__":
    main()
