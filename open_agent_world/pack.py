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
    inspect_archive(data)
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
    inspector = commands.add_parser('inspect')
    inspector.add_argument('artifact', type=Path)
    commands.add_parser('schema')
    for name in ('install', 'activate', 'status'):
        command = commands.add_parser(name)
        command.add_argument('--data-root', type=Path, required=True)
        if name == 'install':
            command.add_argument('artifact', type=Path)
        elif name == 'activate':
            command.add_argument('id')
            command.add_argument('version')
    args = parser.parse_args()
    if args.command == 'build':
        print(build(args.source, args.output))
    elif args.command == 'inspect':
        print(inspect_archive(args.artifact.read_bytes()).manifest.model_dump_json(indent=2))
    elif args.command == 'schema':
        print(json.dumps(PackManifest.model_json_schema(), indent=2))
    else:
        # Recovery must not import the failed selected external version. Bundled
        # registrations still reserve their IDs and enforce dependency versions.
        from backend.packs.installation import PackInstallationManager
        from backend.plugins.loader import load_plugin_registry
        manager = PackInstallationManager(args.data_root, load_plugin_registry())
        if args.command == 'install':
            result = manager.install(args.artifact.read_bytes())
        elif args.command == 'activate':
            result = manager.activate(args.id, args.version)
        else:
            result = manager.status()
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
