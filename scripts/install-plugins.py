"""Install trusted checkout plugins and their own declared Python dependencies.

Run after syncing the host. The loader only discovers code; installing packages
belongs in setup/build, never in an HTTP request or application startup.
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path
import subprocess
import sys
import tomllib

from setup import profile_name

ROOT = Path(__file__).resolve().parents[1]

# Compatibility for undeclared legacy plugins only. New declarations route
# host-worker preparation through the same manager as archive installation.
SETUP_HOOKS = """
import os
import sys
from importlib.metadata import distribution
from pathlib import Path
sys.path.insert(0, sys.argv[1])
if sys.argv[3]:
    sys.path.insert(0, sys.argv[3])
    os.environ['OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT'] = str(Path(sys.prefix) / 'oaw-runtime-bundle')
if len(sys.argv) > 4 and sys.argv[4]:
    from backend.development import prepare_profile
    os.environ['OPEN_AGENT_WORLD_DATA_ROOT'] = str(prepare_profile(sys.argv[4]))
for entry in distribution(sys.argv[2]).entry_points:
    if entry.group == 'open_agent_world.setup':
        print(f'Preparing {sys.argv[2]}: {entry.name}', flush=True)
        entry.load()()
"""

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
    raise SystemExit(0)
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
    parser.add_argument("--continue-on-error", action="store_true", help="Prepare each optional Pack independently; report failures without blocking Core setup")
    args = parser.parse_args()
    if args.target and args.profile:
        parser.error("--target and --profile select different preparation destinations")
    discovery_failures = []
    defaults = args.paths or (plugin_paths(ROOT / "plugins", strict=False, failures=discovery_failures) if args.continue_on_error else plugin_paths(ROOT / "plugins"))
    paths = list(dict.fromkeys(path.resolve() for path in [*defaults, *args.additional_path]))
    # Validate the whole request before invoking a package manager.
    for path in paths:
        if not (path / "pyproject.toml").is_file():
            parser.error(f"Plugin path must contain a pyproject.toml: {path}")
    if not paths and not discovery_failures:
        if args.report:
            args.report.write_text("[]", encoding="utf-8")
        print("Optional Packs: none discovered", flush=True)
        return
    if args.continue_on_error:
        results = discovery_failures
        for path in paths:
            command = [sys.executable, str(Path(__file__).resolve()), str(path), "--python", args.python]
            for name in ("target", "profile", "constraint"):
                if value := getattr(args, name):
                    command += ["--" + name, str(value)]
            if args.dry_run:
                command.append("--dry-run")
            try:
                project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
                manifest = path / "oaw.pack.toml"
                declaration = tomllib.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
                identity = declaration.get("distribution", {}).get("id") or project["project"]["name"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                results.append({"id": str(path), "path": str(path), "ready": False, "error": str(exc)})
                continue
            print(f"Preparing {identity} ...", flush=True)
            result = subprocess.run(command, cwd=ROOT, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="", flush=True)
            results.append({"id": identity, "owner": declaration.get("plugin", {}).get("owner"), "path": str(path), "ready": result.returncode == 0,
                            "error": (result.stderr.strip()[-4000:] or f"Installer exited with code {result.returncode}") if result.returncode else ""})
        if args.report:
            args.report.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        failed = [row for row in results if not row["ready"]]
        print(f"Optional Packs: {len(results) - len(failed)} {'checked' if args.dry_run else 'ready'}, {len(failed)} failed", flush=True)
        if failed:
            print("Optional Pack preparation failed; Core remains available. Retry install-plugins.py with the individual path:\n"
                  + "\n".join(f"{row['id']}: {row['error']}\nRetry: {subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), row['path'], '--python', args.python, *(['--profile', args.profile] if args.profile else [])])}" for row in failed), file=sys.stderr)
        return 3 if failed else 0
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
            project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"]
            if (path / "oaw.pack.toml").is_file():
                subprocess.run([args.python, "-c", PREPARE_DECLARED, str(ROOT), str(path.resolve()),
                                str(args.target.resolve()) if args.target else "",
                                *([args.profile] if args.profile else [])], cwd=ROOT, check=True)
            elif project.get("entry-points", {}).get("open_agent_world.setup"):
                subprocess.run([args.python, "-c", SETUP_HOOKS, str(ROOT), project["name"],
                                str(args.target.resolve()) if args.target else "",
                                *([args.profile] if args.profile else [])], cwd=ROOT, check=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        # The child already printed the cause. Avoid burying it in a traceback
        # containing the entire inline preparation script.
        raise SystemExit(f"Pack preparation failed (exit {exc.returncode}); see the error above.") from None
