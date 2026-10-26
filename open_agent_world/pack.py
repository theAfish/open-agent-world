"""Public local Pack build/inspect/recovery CLI: python -m open_agent_world.pack."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from backend.packs.archive import inspect_archive
from backend.packs.manifest import PackManifest


def init_project(directory: Path) -> Path:
    """Copy the declarative template with a new, project-local identity."""
    directory = directory.resolve()
    slug = re.sub(r'[^a-z0-9]+', '-', directory.name.lower()).strip('-') or 'pack'
    if not slug[0].isalpha():
        slug = 'pack-' + slug
    pack_id = f'local.{slug[:80].rstrip("-")}-{uuid4().hex[:12]}'
    project_name = pack_id.replace('.', '-')
    template = Path(__file__).resolve().parents[1] / 'examples/packs/notes'
    shutil.copytree(template, directory, ignore=shutil.ignore_patterns('node_modules', 'dist', 'package-lock.json'))
    for name in ('oaw.pack.toml', 'pyproject.toml', 'package.json', 'README.md'):
        path = directory / name
        content = path.read_text(encoding='utf-8').replace('example.notes', pack_id).replace('example-notes', project_name)
        if name == 'oaw.pack.toml':
            content = content.replace('name = "Notes"', 'name = ' + json.dumps(directory.name[:120], ensure_ascii=False))
        path.write_text(content, encoding='utf-8')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    initializer = commands.add_parser('init', help='Create an independent Schema 3 project from the Notes template')
    initializer.add_argument('directory', type=Path)
    initializer.add_argument('--visual', action='store_true', help='Create a visual Card project without a frontend build step')
    project = commands.add_parser('project', help='Inspect and edit the same source files used by Pack IDE; never imports source code')
    project.add_argument('operation', choices=('inspect', 'read', 'write'))
    project.add_argument('source', type=Path)
    project.add_argument('--path', help='Project-relative source file')
    project.add_argument('--content-file', type=Path, help='UTF-8 replacement text for write')
    project.add_argument('--expected-sha256', help='Required existing hash for write; omit only to create a new file')
    project_builder = commands.add_parser('build-project', help='Package a declared source project using prebuilt artifacts')
    project_builder.add_argument('source', type=Path)
    project_builder.add_argument('output', type=Path)
    project_builder.add_argument('--backend-wheel', type=Path, help='Required for advanced Python plugins; omitted for declarative cards')
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
    checker = commands.add_parser('check', help='Strictly activate plugins in a disposable process; any optional failure exits nonzero')
    checker.add_argument('source', type=Path, nargs='?', help='Check only this project, excluding unrelated installed entrypoints')
    checker.add_argument('--plugin-path', type=Path, action='append', default=[])
    checker.add_argument('--data-root', type=Path)
    checker.add_argument('--lifecycle', action='store_true', help='Restore and shut down cards in an offline test profile')
    checker.add_argument('--card', action='append', default=[], help='Create a test card before lifecycle checks (repeatable)')
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
    if args.command == 'init':
        if args.visual:
            from backend.packs.projects import scaffold
            scaffold(args.directory.resolve(), args.directory.name)
            print(args.directory.resolve())
        else:
            print(init_project(args.directory))
    elif args.command == 'project':
        from backend.packs.projects import inspect_project, read_file, write_file
        source = args.source.resolve(strict=True)
        if not (source / 'oaw.pack.toml').is_file() or not (source / 'pyproject.toml').is_file():
            parser.error('project requires a Schema 3 source directory')
        if args.operation == 'inspect':
            result = inspect_project(source)
        elif not args.path:
            parser.error('--path is required for project read/write')
        elif args.operation == 'read':
            result = read_file(source, args.path)
        elif args.content_file is None:
            parser.error('--content-file is required for project write')
        else:
            result = write_file(source, args.path, args.content_file.read_text(encoding='utf-8'), args.expected_sha256)
        print(json.dumps(result, indent=2, ensure_ascii=False))
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
    elif args.command == 'check':
        if (args.lifecycle or args.card) and args.data_root is None:
            # Loaded code pins managed resources for its entire process lifetime.
            # Let the child exit before deleting the disposable profile (Windows
            # cannot remove live lease locks, and releasing them early is unsafe).
            import subprocess
            import sys
            from tempfile import TemporaryDirectory
            with TemporaryDirectory(prefix='oaw-pack-check-') as temporary:
                subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/oaw-pack.py'),
                                *sys.argv[1:], '--data-root', temporary], check=True)
            return
        from backend.plugins.loader import load_plugin_registry
        registry = load_plugin_registry(plugin_directory=args.source, plugin_directories=tuple(args.plugin_path),
            data_root=args.data_root, strict=True, load_entry_points=args.source is None)
        if args.lifecycle or args.card:
            import asyncio
            from backend.config import Settings
            from backend.services import create_services
            from backend.world.models import CardCreate
            from backend.plugins.loader import _check_strict
            services = create_services(Settings.for_data_root(args.data_root), plugins=registry)
            async def check_lifecycle():
                try:
                    for type_id in args.card:
                        services.world.create_card(CardCreate(type=type_id))
                    await services.startup()
                    _check_strict(registry, True)
                finally:
                    try:
                        await services.shutdown()
                    finally:
                        services.close()
            asyncio.run(check_lifecycle())
        print(json.dumps({'plugins': [item.id for item in registry.plugins()], 'failures': registry.load_failures}, indent=2))
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
