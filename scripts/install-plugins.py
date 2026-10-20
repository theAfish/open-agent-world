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


def plugin_paths(directory: Path) -> list[Path]:
    return [manifest.parent for manifest in sorted(directory.glob("*/pyproject.toml"))
            if tomllib.loads(manifest.read_text(encoding="utf-8"))
            .get("project", {}).get("entry-points", {}).get("open_agent_world.plugins")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--additional-path", action="append", type=Path, default=[],
                        help="Add to the default checkout plugins instead of replacing them")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", type=Path, help="Install non-editable packages into a desktop payload")
    parser.add_argument("--profile", type=profile_name, help="Prepare dependencies in this development profile, matching backend.launcher")
    parser.add_argument("--constraint", type=Path, help="Preserve the locked host dependency versions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.target and args.profile:
        parser.error("--target and --profile select different preparation destinations")
    paths = list(dict.fromkeys(path.resolve() for path in
                              [*(args.paths or plugin_paths(ROOT / "plugins")), *args.additional_path]))
    # Validate the whole request before invoking a package manager.
    for path in paths:
        if not (path / "pyproject.toml").is_file():
            parser.error(f"Plugin path must contain a pyproject.toml: {path}")
    if not paths:
        return
    command = ["uv", "pip", "install", "--no-deps", "--python", args.python]
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
    main()
