"""Installation verifies contents; process startup consumes installed metadata."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.packs import modules
from backend.packs.installation import PackInstallationManager
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs
from backend.tests.test_pack_modules import distribution


@pytest.fixture(autouse=True)
def isolated_imports(monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    yield
    for name in list(sys.modules):
        if name.startswith('oaw_module_fixture'):
            del sys.modules[name]


def installed(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(distribution())
    manifest = manager.selected()['module.fixture.release']
    return manager, manifest


def forbidden(*args, **kwargs):
    raise AssertionError('Ordinary startup must not scan, unpack or prepare installed code')


def test_installed_startup_uses_receipt_without_archive_reads_or_tree_scans(tmp_path, monkeypatch):
    manager, manifest = installed(tmp_path)
    assert manager._startup_receipt(manifest)['entrypoint'] == 'oaw_module_fixture:create_plugin'
    read_bytes = Path.read_bytes
    def read(path):
        if path.suffix == '.whl':
            forbidden()
        return read_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(os, 'walk', forbidden)
    monkeypatch.setattr(modules, '_verify_tree', forbidden)
    monkeypatch.setattr(modules, 'materialize_modules', forbidden)
    for _ in range(2):
        registry = create_builtin_registry()
        load_installed_packs(registry, tmp_path)
        assert registry.has_plugin('module.fixture')
        assert sys.modules['oaw_module_fixture'].RESOURCE == 'canonical resource'


def test_old_installation_migrates_once_and_keeps_its_selection(tmp_path, monkeypatch):
    manager, manifest = installed(tmp_path)
    with manager.connect(write=True) as db:
        db.execute('DELETE FROM startup_receipts')
    original = manager.verify_installed
    calls = []
    def verify(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, 'verify_installed', verify)
    for _ in range(2):
        assert manager.startup_entrypoint(manifest) == 'oaw_module_fixture:create_plugin'
    assert len(calls) == 1
    assert manager.selected() == {manifest.id: manifest}


def test_corrupt_old_installation_cannot_gain_a_startup_receipt(tmp_path):
    manager, manifest = installed(tmp_path)
    with manager.connect(write=True) as db:
        db.execute('DELETE FROM startup_receipts')
    wheel = manager.version_path(manifest.id, manifest.version) / manifest.entrypoints.backend
    wheel.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum mismatch'):
        manager.startup_entrypoint(manifest)
    assert manager._startup_receipt(manifest) is None


def test_missing_prepared_code_is_an_error_not_an_implicit_reinstall(tmp_path, monkeypatch):
    manager, manifest = installed(tmp_path)
    digest = manager._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend]
    package = manager.root / 'cache/modules' / digest / manifest.module_name
    package.rename(package.with_name('missing-package'))
    monkeypatch.setattr(modules, 'materialize_modules', forbidden)
    registry = create_builtin_registry()
    load_installed_packs(registry, tmp_path)
    assert not registry.has_plugin('module.fixture')
    assert registry.load_failures[0]['phase'] == 'environment'
    with pytest.raises(RuntimeError, match='Optional plugin checks failed'):
        load_installed_packs(create_builtin_registry(), tmp_path, strict=True)
    assert not package.exists()


def test_module_receipt_digest_cannot_escape_the_cache(tmp_path):
    manager, manifest = installed(tmp_path)
    with pytest.raises(ValueError, match='Invalid installed backend digest'):
        modules.prepared_modules(manifest, manager.root / 'cache/modules', expected_digest='../../../assets')


@pytest.mark.parametrize('change', [{'schema': 999}, {'backend_sha256': 'a' * 64},
                                  {'entrypoint': 'other.module:create_plugin'}, {'requires': None},
                                  {'requires_python': '>=999'}])
def test_startup_rejects_invalid_or_incompatible_receipts(tmp_path, change):
    manager, manifest = installed(tmp_path)
    receipt = {**manager._startup_receipt(manifest), **change}
    with manager.connect(write=True) as db:
        db.execute('UPDATE startup_receipts SET record=?', (json.dumps(receipt),))
    with pytest.raises(ValueError):
        manager.startup_entrypoint(manifest)
    with pytest.raises(ValueError, match='receipt differs'):
        manager.verify_integrity(manifest)


def test_verify_cli_checks_contents_without_importing_or_repairing_code(tmp_path):
    manager, manifest = installed(tmp_path)
    digest = manager._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend]
    source = manager.root / 'cache/modules' / digest / manifest.module_name / '__init__.py'
    command = [sys.executable, '-m', 'open_agent_world.pack', 'verify', '--data-root', str(tmp_path)]
    success = subprocess.run(command, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    assert json.loads(success.stdout)['packs'][0]['id'] == manifest.id
    source.write_text("raise RuntimeError('MUST NOT EXECUTE')", encoding='utf-8')
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode != 0
    assert 'Materialized Pack checksum mismatch' in failed.stderr
    assert 'MUST NOT EXECUTE' not in failed.stderr
    assert source.read_text() == "raise RuntimeError('MUST NOT EXECUTE')"
