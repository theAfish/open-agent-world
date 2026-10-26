"""Prepare selected trusted source Packs, including their independent frontends.

Run after syncing the host. The loader only discovers code; installing packages
belongs in setup/build, never in an HTTP request or application startup.
"""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from setup import profile_name, run_logged

ROOT = Path(__file__).resolve().parents[1]

PREPARE_DECLARED = """
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
if sys.argv[3]:
    sys.path.insert(0, sys.argv[3])
    os.environ['OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT'] = str(Path(sys.prefix) / 'oaw-runtime-bundle')
from backend.config import _default_data_root
from backend.packs.spec import read_source_pack
from backend.packs.planning import prepare_declared
from backend.packs.adapters import prepare_source_adapter, source_adapter_resource_ids
from backend.packs.lifecycle import ManagedResourceLedger, environment_resources
from backend.packs.installation import PackInstallationManager
from backend.packs.environments import preparation_progress
spec = read_source_pack(Path(sys.argv[2]))
data_root = Path(os.environ.get('OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT') or _default_data_root())
if len(sys.argv) > 4 and sys.argv[4]:
    from backend.development import prepare_profile
    data_root = prepare_profile(sys.argv[4])
installation = PackInstallationManager(data_root)
if not installation.source_enrolled(spec.distribution_id):
    print(f'Skipping uninstalled source Pack: {spec.distribution_id}', flush=True)
    raise SystemExit(10)
ledger = ManagedResourceLedger(data_root)
resources = environment_resources(spec, spec.source_root)
with ledger.lease_many(resources, label='source-setup') as consumer:
    with preparation_progress(f'{spec.owner}: backend dependencies'):
        prepare_source_adapter(spec, data_root, consumer=consumer)
    for name, declaration in spec.environments.items():
        if declaration.target != 'host-worker' or declaration.prepare != 'install':
            continue
        if declaration.platforms and sys.platform not in declaration.platforms:
            raise ValueError(f'Environment {spec.owner}/{name} does not support {sys.platform}')
        with preparation_progress(f'{spec.owner}/{name}'):
            prepare_declared(spec.owner, name, declaration, spec.source_root, data_root)
    with installation.connect(write=True):
        if installation.source_enrolled(spec.distribution_id):
            for resource in resources | source_adapter_resource_ids(spec, data_root):
                ledger.claim('source:' + spec.distribution_id, resource)
"""


def plugin_paths(directory: Path, *, strict=True, failures=None) -> list[Path]:
    paths = []
    for manifest in sorted(directory.glob("*/pyproject.toml")):
        try:
            project = tomllib.loads(manifest.read_text(encoding="utf-8"))
            if project.get("project", {}).get("entry-points", {}).get("open_agent_world.plugins") or (manifest.parent / "oaw.pack.toml").is_file():
                paths.append(manifest.parent)
        except (OSError, ValueError) as exc:
            if strict:
                raise
            print(f"Optional Pack discovery failed ({manifest}): {exc}", file=sys.stderr)
            if failures is not None:
                failures.append({"id": str(manifest.parent), "path": str(manifest.parent), "ready": False, "error": str(exc)})
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--additional-path", action="append", type=Path, default=[],
                        help="Add to the default checkout plugins instead of replacing them")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", type=Path, help="Install non-editable packages into a desktop payload")
    parser.add_argument("--profile", type=profile_name, help="Prepare dependencies in this development profile, matching backend.launcher")
    parser.add_argument("--constraint", type=Path, help="Preserve the locked host dependency versions")
    parser.add_argument("--report", type=Path, help="Write per-Pack preparation results as JSON")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Prepare frontend dependencies without building assets")
    parser.add_argument("--frontend-output", type=Path, help="Publish built assets into this host assets/packs directory")
    parser.add_argument("--verbose", action="store_true", help="Stream detailed dependency resolver output")
    parser.add_argument("--continue-on-error", action="store_true", help="Prepare each optional Pack independently; report failures without blocking Core setup")
    args = parser.parse_args()
    if args.verbose:
        os.environ['OPEN_AGENT_WORLD_SETUP_VERBOSE'] = '1'
    if args.target and args.profile:
        parser.error("--target and --profile select different preparation destinations")
    discovery_failures = []
    defaults = args.paths or (plugin_paths(ROOT / "plugins", strict=False, failures=discovery_failures) if args.continue_on_error or not args.target else plugin_paths(ROOT / "plugins"))
    paths = list(dict.fromkeys(path.resolve() for path in [*defaults, *args.additional_path]))
    batch = args.continue_on_error or (not args.target and (len(paths) > 1 or discovery_failures))
    if not batch:
        for path in paths:
            if not (path / "pyproject.toml").is_file():
                parser.error(f"Plugin path must contain a pyproject.toml: {path}")
    if not paths and not discovery_failures:
        if args.report:
            args.report.write_text("[]", encoding="utf-8")
        print("Optional Packs: none discovered", flush=True)
        return
    if batch:
        results = discovery_failures
        for path in paths:
            command = [sys.executable, str(Path(__file__).resolve()), str(path), "--python", args.python]
            for name in ("target", "profile", "constraint", "frontend_output"):
                if value := getattr(args, name):
                    command += ["--" + name.replace('_', '-'), str(value)]
            if args.dry_run:
                command.append("--dry-run")
            if args.skip_build:
                command.append("--skip-build")
            try:
                project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
                manifest = path / "oaw.pack.toml"
                declaration = tomllib.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
                identity = declaration.get("distribution", {}).get("id") or project["project"]["name"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                results.append({"id": str(path), "path": str(path), "ready": False, "error": str(exc)})
                continue
            print(f"Preparing {identity} ...", flush=True)
            try:
                result = run_logged(command, cwd=ROOT)
            except OSError as exc:
                result = subprocess.CompletedProcess(command, 1, stdout=str(exc))
            results.append({"id": identity, "owner": declaration.get("plugin", {}).get("owner"), "path": str(path), "ready": result.returncode == 0,
                            "error": (result.stdout.strip()[-4000:] or f"Installer exited with code {result.returncode}") if result.returncode else ""})
        if args.report:
            args.report.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        failed = [row for row in results if not row["ready"]]
        print(f"Optional Packs: {len(results) - len(failed)} {'checked' if args.dry_run else 'ready'}, {len(failed)} failed", flush=True)
        if failed:
            print("Optional Pack preparation failed; Core remains available. Retry install-plugins.py with the individual path:\n"
                  + "\n".join(f"{row['id']}: {row['error']}\nRetry: {subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), row['path'], '--python', args.python, *(['--profile', args.profile] if args.profile else [])])}" for row in failed), file=sys.stderr)
        return 3 if failed else 0
    for path in paths:
        project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        if not (path / "oaw.pack.toml").is_file() and project.get("entry-points", {}).get("open_agent_world.setup"):
            parser.error(f"Setup hooks are unsupported; declare environments in oaw.pack.toml: {path}")
    command = ["uv", "pip", "install", "--no-deps", "--python", args.python]
    # Schema 3 source discovery already adds the source path. Installing an
    # editable copy repeats that authority and leaves stale entrypoint metadata.
    # Legacy sources and packaged desktop targets still need distribution code.
    adapter_paths = [path for path in paths if not (path / 'oaw.pack.toml').is_file() or (args.target
        and tomllib.loads((path / 'pyproject.toml').read_text(encoding='utf-8')).get('project', {}).get('entry-points', {}).get('open_agent_world.plugins'))]
    if args.dry_run:
        command.append("--dry-run")
    if args.constraint:
        command += ["--constraint", str(args.constraint)]
    if args.target:
        command += ["--target", str(args.target), *(str(path.resolve()) for path in adapter_paths)]
    else:
        for path in adapter_paths:
            command += ["--editable", str(path.resolve())]
    if adapter_paths:
        subprocess.run(command, cwd=ROOT, check=True)
    if not args.dry_run:
        for path in paths:
            if (path / "oaw.pack.toml").is_file():
                try:
                    subprocess.run([args.python, "-c", PREPARE_DECLARED, str(ROOT), str(path.resolve()),
                                    str(args.target.resolve()) if args.target else "",
                                    *([args.profile] if args.profile else [])], cwd=ROOT, check=True)
                except subprocess.CalledProcessError as exc:
                    if exc.returncode == 10:
                        continue  # Explicitly removed sources must not regain frontend assets either.
                    raise
            if not args.target:
                prepare_frontend(path, args)


def prepare_frontend(path, args):
    declaration_path = path / 'oaw.pack.toml'
    declaration = tomllib.loads(declaration_path.read_text(encoding='utf-8')) if declaration_path.is_file() else {}
    frontend = declaration.get('components', {}).get('frontend')
    project = path / (frontend.get('project', 'frontend/package.json') if frontend else 'frontend/package.json')
    if not frontend and not (project.parent / 'plugin.json').is_file():
        return
    if not project.resolve().is_relative_to(path.resolve()):
        raise ValueError(f'Pack frontend project escapes its source: {project}')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Preparing a Pack frontend requires Node.js; run setup first.')
    # Selection is explicit: preparing one Pack must never install/build siblings.
    env = {**os.environ, 'OPEN_AGENT_WORLD_CORE_ONLY': '0', 'OAW_ALLOW_PACK_FAILURES': '0'}
    command = [node, str(ROOT / 'frontend/scripts/source-packs.mjs'), '--frontend-path', str(project.parent), '--prepare']
    if not args.skip_build:
        command += ['--build']
        if args.frontend_output:
            command += ['--output', str(args.frontend_output)]
    subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        # The child already printed the cause. Avoid burying it in a traceback
        # containing the entire inline preparation script.
        raise SystemExit(f"Pack preparation failed (exit {exc.returncode}); see the error above.") from None
