"""Public local Pack build/inspect/recovery CLI: python -m open_agent_world.pack."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import io

from backend.packs.archive import inspect_archive
from backend.packs.manifest import PackManifest


def build(source: Path, output: Path) -> Path:
    """Package already-built artifacts, never import the Pack or run build hooks."""
    source = source.resolve()
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("Pack input must not contain symlinks")
        if path.is_file():
            name = path.relative_to(source).as_posix()
            if name != 'checksums.json':
                files[name] = path.read_bytes()
    files['checksums.json'] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, indent=2).encode()
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    data = buffer.getvalue()
    inspect_archive(data, check_host_libraries=False)
    if output.exists():
        raise ValueError("Output already exists; choose a new artifact path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('source', type=Path)
    builder.add_argument('output', type=Path)
    project_builder = commands.add_parser('build-project', help='Package a declared source project using prebuilt artifacts')
    project_builder.add_argument('source', type=Path)
    project_builder.add_argument('output', type=Path)
    project_builder.add_argument('--backend-wheel', type=Path, required=True)
    project_builder.add_argument('--frontend-dir', type=Path)
    project_builder.add_argument('--dependency-wheels', type=Path)
    project_builder.add_argument('--plugin-api')
    project_builder.add_argument('--oaw')
    doctor_parser = commands.add_parser('doctor', help='Read source declarations and environment status without installing')
    doctor_parser.add_argument('source', type=Path)
    doctor_parser.add_argument('--data-root', type=Path)
    resolver = commands.add_parser('resolve-adapter', help='Resolve shared adapter dependency wheels without installing into host Python')
    resolver.add_argument('source', type=Path)
    resolver.add_argument('output', type=Path)
    inspector = commands.add_parser('inspect')
    inspector.add_argument('artifact', type=Path)
    commands.add_parser('schema')
    verifier = commands.add_parser('verify', help='Fully verify selected Packs and prepared source dependencies without importing plugin code')
    verifier.add_argument('--data-root', type=Path, required=True)
    verifier.add_argument('--plugin-path', type=Path, action='append', default=[])
    for name in ('install', 'activate', 'status', 'uninstall-plan', 'gc'):
        command = commands.add_parser(name)
        command.add_argument('--data-root', type=Path, required=True)
        if name == 'install':
            command.add_argument('artifact', type=Path)
        elif name == 'activate':
            command.add_argument('id')
            command.add_argument('version')
        elif name == 'uninstall-plan':
            command.add_argument('id')
    args = parser.parse_args()
    if args.command == 'build':
        print(build(args.source, args.output))
    elif args.command == 'build-project':
        from backend.packs.build import build_project
        print(build_project(args.source, args.output, backend_wheel=args.backend_wheel,
                            frontend_dir=args.frontend_dir, dependency_wheels=args.dependency_wheels,
                            plugin_api=args.plugin_api, oaw=args.oaw))
    elif args.command == 'resolve-adapter':
        from backend.packs.adapters import resolve_adapter
        from backend.packs.spec import read_source_pack
        print(resolve_adapter(read_source_pack(args.source), args.output))
    elif args.command == 'doctor':
        from backend.packs.build import doctor
        print(json.dumps(doctor(args.source, data_root=args.data_root), indent=2))
    elif args.command == 'inspect':
        print(inspect_archive(args.artifact.read_bytes()).manifest.model_dump_json(indent=2))
    elif args.command == 'schema':
        print(json.dumps(PackManifest.model_json_schema(), indent=2))
    elif args.command == 'verify':
        from backend.packs.verification import verify_profile
        print(json.dumps(verify_profile(args.data_root, plugin_directories=tuple(args.plugin_path)), indent=2))
    else:
        # Recovery must not import the failed selected external version. Bundled
        # registrations still reserve their IDs and enforce dependency versions.
        from backend.packs.installation import PackInstallationManager
        from backend.plugins.loader import load_plugin_registry
        manager = PackInstallationManager(args.data_root, load_plugin_registry(data_root=args.data_root, load_installed=False))
        if args.command == 'install':
            result = manager.install(args.artifact.read_bytes())
        elif args.command == 'activate':
            result = manager.activate(args.id, args.version)
        elif args.command == 'uninstall-plan':
            result = manager.uninstall_plan(args.id)
            result['note'] = 'World and active work blockers are checked by the running host before removal.'
        elif args.command == 'gc':
            result = manager.reconcile_resources(collect=True)
        else:
            result = manager.status()
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
