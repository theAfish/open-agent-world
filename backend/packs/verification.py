"""Explicit installation integrity checks, independent of plugin imports."""
from pathlib import Path

from backend.packs.adapters import _source_adapter_location, prepared_adapter_paths
from backend.packs.installation import PackInstallationManager
from backend.packs.spec import read_source_pack


def verify_profile(data_root: Path, *, plugin_directories: tuple[Path, ...] = ()) -> dict:
    root = Path(data_root).resolve()
    if not (root / 'packs/installations.sqlite3').is_file():
        raise ValueError('No Pack installation record at this data root')
    manager = PackInstallationManager(root)
    verified = []
    for manifest in manager.selected().values():
        manager.verify_integrity(manifest)
        verified.append({'id': manifest.id, 'version': manifest.version, 'kind': 'installed'})
    bundled = Path(__file__).resolve().parents[2] / 'plugins'
    seen = set()
    for directory in dict.fromkeys((bundled.resolve(), *plugin_directories)):
        if not directory.is_dir():
            continue
        for source in sorted(directory.iterdir()):
            if not (source / 'oaw.pack.toml').is_file():
                continue
            spec = read_source_pack(source)
            if spec.distribution_id in seen or not manager.source_enrolled(spec.distribution_id):
                continue
            seen.add(spec.distribution_id)
            location = _source_adapter_location(spec, root)
            if location is None:
                # Editable host installations have no managed wheel archive.
                continue
            adapter, authority = location
            prepared_adapter_paths(adapter, authority, read_only=True)
            verified.append({'id': spec.distribution_id, 'version': spec.version, 'kind': 'source-adapter'})
    return {'status': 'verified', 'packs': verified}
