import json
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[2]
configure = runpy.run_path(str(ROOT / 'scripts/configure-desktop-release.py'))['configure']
manifest = runpy.run_path(str(ROOT / 'scripts/collect-desktop-update.py'))['manifest']


def test_signed_release_rejects_incomplete_keys(tmp_path):
    with pytest.raises(SystemExit, match='both updater'):
        configure(tmp_path, {'OAW_SIGNED_RELEASE': 'true'})
    with pytest.raises(SystemExit, match='both updater'):
        configure(tmp_path, {'OAW_UPDATER_PUBLIC_KEY': 'public'})


def test_release_config_contains_only_public_key_and_enables_build(tmp_path):
    folder = tmp_path / 'desktop/src-tauri'
    folder.mkdir(parents=True)
    (folder / 'tauri.conf.json').write_text(json.dumps({'bundle': {'windows': {}}}))
    envfile = tmp_path / 'env'
    configure(tmp_path, {'OAW_UPDATER_PUBLIC_KEY': 'public-key', 'TAURI_SIGNING_PRIVATE_KEY': 'private-key', 'GITHUB_ENV': str(envfile)})
    content = (folder / 'tauri.conf.json').read_text()
    assert 'public-key' in content and 'private-key' not in content
    assert 'OAW_UPDATER_ENABLED=1' in envfile.read_text()


def test_manifest_requires_matching_versions_for_all_three_platforms(tmp_path):
    with pytest.raises(FileNotFoundError):
        manifest(tmp_path)
    platforms = {'windows-x64': 'windows-x86_64', 'macos-arm64': 'darwin-aarch64', 'macos-x86_64': 'darwin-x86_64'}
    for platform, target in platforms.items():
        (tmp_path / f'updater-{platform}.json').write_text(json.dumps({'version': '1.2.3', 'platforms': {target: {'url': 'https://example.com/update', 'signature': 'signed'}}}))
    manifest(tmp_path)
    assert len(json.loads((tmp_path / 'latest.json').read_text())['platforms']) == 3
    path = tmp_path / 'updater-windows-x64.json'
    value = json.loads(path.read_text())
    value['version'] = '1.2.4'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='same version'):
        manifest(tmp_path)
