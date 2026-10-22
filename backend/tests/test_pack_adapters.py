from __future__ import annotations

import io
import sys
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace

import pytest

from backend.packs.adapters import (activate_adapters, check_adapter_dependencies, collect_wheels,
    describe_wheel, prepare_adapters, prepared_adapter_paths, verify_wheel)
from backend.packs.lifecycle import ManagedResourceLedger
from backend.packs.manifest import AdapterDependencies


@pytest.fixture(autouse=True)
def isolated_adapter_import_paths(monkeypatch):
    from backend.packs import adapters
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    monkeypatch.setattr(adapters, '_ACTIVATED_WHEELS', {})
    monkeypatch.setattr(adapters._SharedAdapterFinder, 'roots', {})
    monkeypatch.setattr(adapters._SharedAdapterFinder, 'read_only_roots', set())


def library(name='fixture-helper', version='1.0', *, requires=(), extra=None):
    module = name.replace('-', '_')
    prefix = f'{module}-{version}.dist-info'
    files = {f'{module}/__init__.py': b'VALUE = 42',
             f'{prefix}/METADATA': (f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n' +
                                    ''.join(f'Requires-Dist: {text}\n' for text in requires)).encode(),
             f'{prefix}/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    files.update(extra or {})
    result = io.BytesIO()
    with ZipFile(result, 'w') as archive:
        for path, value in files.items():
            archive.writestr(path, value)
    data = result.getvalue()
    declaration = describe_wheel(data, f'backend/dependencies/{module}-{version}-py3-none-any.whl')
    return declaration, data


@pytest.mark.parametrize('verbose', [False, True])
@pytest.mark.parametrize('returncode', [0, 7])
def test_resolve_adapter_streams_verbose_output_and_preserves_failures(tmp_path, monkeypatch, capfd, verbose, returncode):
    from backend.packs import adapters
    if verbose:
        monkeypatch.setenv('OPEN_AGENT_WORLD_SETUP_VERBOSE', '1')
    else:
        monkeypatch.delenv('OPEN_AGENT_WORLD_SETUP_VERBOSE', raising=False)
    monkeypatch.setattr(adapters.shutil, 'which', lambda name: 'uv')
    monkeypatch.setattr(adapters.metadata, 'distributions', lambda: [])
    original_run = adapters.subprocess.run
    collected = []
    monkeypatch.setattr(adapters, 'collect_wheels', lambda *args: collected.append(args))

    def run(command, **kwargs):
        assert kwargs == dict(capture_output=not verbose, text=True, timeout=1800)
        assert ('--verbose' in command) == verbose
        return original_run([sys.executable, '-c',
                             'import sys; print("downloading fixture"); '
                             'print("resolver detail", file=sys.stderr); '
                             f'sys.exit({returncode})'], **kwargs)

    monkeypatch.setattr(adapters.subprocess, 'run', run)
    spec = SimpleNamespace(backend=SimpleNamespace(python=('fixture-helper==1.0',)))
    directory = tmp_path / 'wheels'
    if returncode:
        with pytest.raises(RuntimeError, match='exit code 7' if verbose else 'resolver detail'):
            adapters.resolve_adapter(spec, directory)
        assert not collected
    else:
        assert adapters.resolve_adapter(spec, directory) == directory
        assert collected == [(spec.backend.python, directory)]
    output = capfd.readouterr()
    assert ('downloading fixture' in output.out) == verbose
    assert ('resolver detail' in output.err) == verbose


def test_prepare_reuses_identical_wheel_with_independent_consumers_and_checks_tamper(tmp_path):
    wheel, data = library()
    adapter = AdapterDependencies(requirements=('fixture-helper==1.0',), wheels=(wheel,))
    first = prepare_adapters(adapter, {wheel.path: data}, tmp_path, consumer='source:first')
    second = prepare_adapters(adapter, {wheel.path: data}, tmp_path, consumer='source:second')
    assert first == second
    ledger = ManagedResourceLedger(tmp_path)
    ledger.release_consumer('source:first')
    ledger.collect()
    assert first[0].exists()
    try:
        activate_adapters(adapter, tmp_path)
        assert str(first[0]) in sys.path
    finally:
        sys.path.remove(str(first[0]))
    (first[0] / 'fixture_helper/__init__.py').write_text('VALUE = 99')
    with pytest.raises(ValueError, match='checksum mismatch'):
        prepared_adapter_paths(adapter, tmp_path, read_only=True)
    ledger.release_consumer('source:second')
    ledger.collect()
    assert not first[0].exists()


def test_adapter_startup_does_not_read_archives_or_scan_contents(tmp_path, monkeypatch):
    from backend.packs import modules
    wheel, data = library('fast-startup-helper')
    adapter = AdapterDependencies(requirements=('fast-startup-helper==1.0',), wheels=(wheel,))
    paths = prepare_adapters(adapter, {wheel.path: data}, tmp_path, consumer='source:fixture')
    def forbidden(*args, **kwargs):
        raise AssertionError('Startup must not read dependency contents or scan the installation')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(modules, '_verify_tree', forbidden)
    monkeypatch.setattr(modules.os, 'walk', forbidden)
    for _ in range(2):
        assert activate_adapters(adapter, tmp_path) == paths


def test_adapter_startup_rejects_foreign_import_root(tmp_path, monkeypatch):
    wheel, data = library('foreign-root-helper')
    adapter = AdapterDependencies(requirements=('foreign-root-helper==1.0',), wheels=(wheel,))
    prepare_adapters(adapter, {wheel.path: data}, tmp_path / 'profile', consumer='source:fixture')
    other = tmp_path / 'foreign/foreign_root_helper'
    other.mkdir(parents=True)
    (other / '__init__.py').write_text('FOREIGN = True')
    monkeypatch.setattr(sys, 'path', [str(other.parent), *sys.path])
    with pytest.raises(ValueError, match='owned by another distribution'):
        activate_adapters(adapter, tmp_path / 'profile')


def test_adapter_startup_rejects_linked_package_without_scanning(tmp_path):
    import os
    wheel, data = library('linked-root-helper')
    adapter = AdapterDependencies(requirements=('linked-root-helper==1.0',), wheels=(wheel,))
    paths = prepare_adapters(adapter, {wheel.path: data}, tmp_path / 'profile', consumer='source:fixture')
    package = paths[0] / 'linked_root_helper'
    outside = tmp_path / 'outside'
    package.rename(outside)
    try:
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(outside), str(package))
        else:
            package.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match='Unsafe materialized Pack module path'):
            activate_adapters(adapter, tmp_path / 'profile')
    finally:
        if package.is_symlink():
            package.unlink()
        elif os.name == 'nt' and package.is_junction():
            package.rmdir()


def test_shared_dependency_conflict_is_found_without_preparation():
    one, _ = library(version='1.0')
    two, _ = library(version='2.0')
    with pytest.raises(ValueError, match='Conflicting shared'):
        check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper',), wheels=(one,)),
                                    AdapterDependencies(requirements=('fixture-helper',), wheels=(two,))])


def test_transitive_extras_are_resolved_and_missing_closure_rejected():
    first, _ = library(requires=('extra-helper==2.0; extra == "fast"',))
    extra, _ = library('extra-helper', '2.0')
    check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper',), wheels=(first,))])
    with pytest.raises(ValueError, match='Missing adapter dependency wheel: extra-helper'):
        check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper[fast]',), wheels=(first,))])
    check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper[fast]',), wheels=(first, extra))])


def test_direct_reference_requires_attested_exact_source():
    wheel, _ = library()
    source = 'git+https://example.invalid/helper.git@' + 'a' * 40
    with pytest.raises(ValueError, match='pinned provenance'):
        check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper @ ' + source,), wheels=(wheel,))])
    check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper @ ' + source,),
                                                    wheels=(wheel.model_copy(update={'source': source}),))])


@pytest.mark.parametrize('extra', [
    {'evil.pth': b'import os'}, {'backend/__init__.py': b''}, {'os.py': b''},
    {'fixture_helper-1.0.data/data/external.txt': b'not library data'},
])
def test_wheels_cannot_inject_path_hooks_host_modules_or_external_data(extra):
    with pytest.raises(ValueError):
        library(extra=extra)


def test_wheel_metadata_and_hash_are_both_bound():
    wheel, data = library()
    with pytest.raises(ValueError, match='checksum mismatch'):
        verify_wheel(wheel, data + b'tamper')
    with pytest.raises(ValueError, match='metadata mismatch'):
        verify_wheel(wheel.model_copy(update={'version': '2.0'}), data)


def test_same_version_host_cannot_shadow_pinned_git_source(monkeypatch):
    from backend.packs import adapters
    wheel, _ = library()
    source = 'git+https://example.invalid/helper.git@' + 'a' * 40
    wheel = wheel.model_copy(update={'source': source})
    monkeypatch.setattr(adapters, 'host_distributions', lambda: {})
    monkeypatch.setattr(adapters.metadata, 'version', lambda name: '1.0')
    monkeypatch.setattr(adapters, '_installed_source', lambda name, source: False)
    with pytest.raises(ValueError, match='unverified provenance'):
        check_adapter_dependencies([AdapterDependencies(requirements=('fixture-helper @ ' + source,), wheels=(wheel,))])


def test_same_version_host_different_bytes_are_rejected(tmp_path, monkeypatch):
    from backend.packs import adapters
    wheel, data = library()
    (tmp_path / 'fixture_helper').mkdir()
    (tmp_path / 'fixture_helper/__init__.py').write_text('VALUE = 99')
    installed = SimpleNamespace(locate_file=lambda relative: tmp_path / relative)
    monkeypatch.setattr(adapters.metadata, 'distribution', lambda name: installed)
    monkeypatch.setattr(adapters, 'host_optional_distributions', lambda: {})
    with pytest.raises(ValueError, match='differs from the locked adapter wheel'):
        verify_wheel(wheel, data)


def test_explicit_host_provider_reuse_requires_owned_files_and_original_module_path(tmp_path, monkeypatch):
    from backend.packs import adapters
    wheel, data = library('host-provider', extra={'host_provider/tokenizer': b'cache\r\n'})
    _, files = adapters.wheel_contents(data)
    for name, content in files.items():
        target = tmp_path / 'site' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.replace(b'\r\n', b'\n'))
    installed = SimpleNamespace(version='1.0', files=tuple(files),
        locate_file=lambda name: tmp_path / 'site' / name, read_text=lambda name: None)
    distribution = adapters.metadata.distribution
    monkeypatch.setattr(adapters.metadata, 'distribution', lambda name: installed if name == 'host-provider' else distribution(name))
    monkeypatch.setattr(adapters, 'host_optional_distributions', lambda: {'host-provider': '1.0'})
    monkeypatch.setattr(sys, 'path', [str(tmp_path / 'site'), *sys.path])
    assert verify_wheel(wheel, data) == files
    adapter = AdapterDependencies(requirements=('host-provider==1.0',), wheels=(wheel,))
    assert adapters.adapter_library_inventory(adapter) == [
        {'name': 'host-provider', 'version': '1.0', 'management': 'host-provided'}]
    foreign, foreign_data = library('host-provider', extra={'httpx/__init__.py': b'FOREIGN = True'})
    with pytest.raises(ValueError, match='does not own'):
        verify_wheel(foreign, foreign_data)
    source = 'git+https://example.com/provider.git@' + 'd' * 40
    with pytest.raises(ValueError, match='unverified source'):
        verify_wheel(wheel.model_copy(update={'source': source}), data)
    other = tmp_path / 'foreign/host_provider'
    other.mkdir(parents=True)
    (other / '__init__.py').write_text('VALUE = 42')
    monkeypatch.setattr(sys, 'path', [str(other.parent), *sys.path])
    with pytest.raises(ValueError, match='different origin'):
        verify_wheel(wheel, data)


def test_activated_pinned_wheel_retains_verified_provenance_without_direct_url(tmp_path, monkeypatch):
    from backend.packs import adapters
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(adapters, '_ACTIVATED_WHEELS', {})
    source = 'git+https://example.com/fixture.git@' + 'a' * 40
    wheel, data = library('pinned-fixture')
    wheel = wheel.model_copy(update={'source': source})
    adapter = AdapterDependencies(requirements=('pinned-fixture @ ' + source,), wheels=(wheel,))
    paths = prepare_adapters(adapter, {wheel.path: data}, tmp_path, consumer='source:fixture')
    activate_adapters(adapter, tmp_path)
    assert adapters.metadata.distribution('pinned-fixture').read_text('direct_url.json') is None
    check_adapter_dependencies([adapter])
    assert adapters.prepared_adapter_paths(adapter, tmp_path) == paths


def test_different_distribution_cannot_take_existing_host_import_root():
    wheel, data = library('fixture-foreign-owner', extra={'httpx/__init__.py': b'VALUE = 99'})
    with pytest.raises(ValueError, match='already owned by another distribution'):
        verify_wheel(wheel, data)


def test_namespace_extension_cannot_replace_an_existing_host_file(tmp_path, monkeypatch):
    namespace = tmp_path / 'existing_namespace/part'
    namespace.mkdir(parents=True)
    (namespace / '__init__.py').write_text('VALUE = 1')
    monkeypatch.setattr(sys, 'path', [str(tmp_path), *sys.path])
    wheel, data = library('fixture-namespace', extra={'existing_namespace/part/__init__.py': b'VALUE = 2'})
    with pytest.raises(ValueError, match='conflicts with existing host file'):
        verify_wheel(wheel, data)


def test_top_level_library_bytecode_is_discarded_before_reverification(tmp_path, monkeypatch):
    import py_compile
    from backend.packs.adapters import prepared_adapter_paths
    monkeypatch.setattr(sys, 'path', list(sys.path))
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('single_fixture.py', 'RESULT = 91')
        archive.writestr('single_fixture-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: single-fixture\nVersion: 1.0\n')
        archive.writestr('single_fixture-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
    data = output.getvalue()
    wheel = describe_wheel(data, 'backend/dependencies/single_fixture-1.0-py3-none-any.whl')
    adapter = AdapterDependencies(requirements=('single-fixture',), wheels=(wheel,))
    paths = prepare_adapters(adapter, {wheel.path: data}, tmp_path, consumer='source:fixture')
    activate_adapters(adapter, tmp_path)
    try:
        from single_fixture import RESULT
        assert RESULT == 91
        # CI disables implicit bytecode writes. This test deliberately supplies
        # a cache to verify that explicit integrity verification discards it.
        py_compile.compile(str(paths[0] / 'single_fixture.py'), doraise=True)
        assert list((paths[0] / '__pycache__').glob('*.pyc'))
        assert prepared_adapter_paths(adapter, tmp_path) == paths
        assert not (paths[0] / '__pycache__').exists()
    finally:
        sys.modules.pop('single_fixture', None)


def test_shared_regular_package_can_receive_disjoint_extension_wheel(tmp_path, monkeypatch):
    from backend.packs.adapters import _SharedAdapterFinder
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    monkeypatch.setattr(_SharedAdapterFinder, 'roots', {})
    base, base_data = library('shared-fixture')
    prefix = 'fixture_extension-1.0.dist-info'
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('shared_fixture/extension.py', 'RESULT = 73')
        archive.writestr(prefix + '/METADATA', 'Metadata-Version: 2.1\nName: fixture-extension\nVersion: 1.0\nRequires-Dist: shared-fixture==1.0\n')
        archive.writestr(prefix + '/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
    extra_data = output.getvalue()
    extension = describe_wheel(extra_data, 'backend/dependencies/fixture_extension-1.0-py3-none-any.whl')
    adapter = AdapterDependencies(requirements=('fixture-extension',), wheels=(base, extension))
    prepare_adapters(adapter, {base.path: base_data, extension.path: extra_data}, tmp_path, consumer='source:fixture')
    activate_adapters(adapter, tmp_path)
    try:
        from shared_fixture.extension import RESULT
        assert RESULT == 73
        conflict = extension.model_copy(update={'file_hashes': {**extension.file_hashes, 'shared_fixture/__init__.py': 'a' * 64}})
        with pytest.raises(ValueError, match='Conflicting shared adapter file ownership'):
            check_adapter_dependencies([adapter.model_copy(update={'wheels': (base, conflict)})])
    finally:
        for name in list(sys.modules):
            if name.startswith('shared_fixture'):
                del sys.modules[name]


def test_collect_requires_wheels_even_when_nonpublic_dependency_happens_to_be_installed(monkeypatch):
    from backend.packs import adapters
    monkeypatch.setattr(adapters, 'host_distributions', lambda: {})
    with pytest.raises(ValueError, match='Missing adapter dependency wheel'):
        collect_wheels(('pydantic>=2',), None)


def test_source_prepare_keeps_import_path_unchanged_and_restores_after_gc(tmp_path):
    from backend.packs.adapters import prepare_source_adapter, source_adapter_resource_ids
    wheel, data = library()
    wheels = tmp_path / 'wheelhouse'
    wheels.mkdir()
    (wheels / wheel.path.rsplit('/', 1)[-1]).write_bytes(data)
    spec = SimpleNamespace(spec_digest='a' * 64, distribution_id='fixture.source',
                           backend=SimpleNamespace(python=('fixture-helper==1.0',)))
    before = list(sys.path)
    paths = prepare_source_adapter(spec, tmp_path / 'profile', dependency_wheels=wheels)
    assert sys.path == before
    assert source_adapter_resource_ids(spec, tmp_path / 'profile') == {'adapter:' + wheel.sha256}
    ledger = ManagedResourceLedger(tmp_path / 'profile')
    ledger.release_consumer('source:fixture.source')
    ledger.collect()
    assert not paths[0].exists()
    assert prepare_source_adapter(spec, tmp_path / 'profile', dependency_wheels=wheels) == paths
    assert paths[0].exists() and sys.path == before


def test_bundled_source_adapters_are_verified_read_only_and_restored_into_profile(tmp_path, monkeypatch):
    import importlib.util
    import marshal
    import struct
    from backend.packs.adapters import (_SharedAdapterFinder, activate_source_adapter,
        prepare_source_adapter, source_adapter_declaration, source_adapter_resource_ids)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    monkeypatch.setattr(_SharedAdapterFinder, 'roots', {})
    monkeypatch.setattr(_SharedAdapterFinder, 'read_only_roots', set())
    wheel, data = library('bundle-fixture')
    wheels = tmp_path / 'wheelhouse'
    wheels.mkdir()
    (wheels / wheel.path.rsplit('/', 1)[-1]).write_bytes(data)
    spec = SimpleNamespace(spec_digest='b' * 64, distribution_id='fixture.bundle',
                           backend=SimpleNamespace(python=('bundle-fixture==1.0',)))
    prefix = tmp_path / 'desktop-python'
    bundle = prefix / 'oaw-runtime-bundle'
    bundled_paths = prepare_source_adapter(spec, bundle, dependency_wheels=wheels)
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    source = bundled_paths[0] / 'bundle_fixture/__init__.py'
    bytecode = Path(importlib.util.cache_from_source(str(source)))
    bytecode.parent.mkdir()
    bytecode.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0,
        int(source.stat().st_mtime), source.stat().st_size) +
        marshal.dumps(compile('VALUE = 99', str(source), 'exec')))
    before = {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob('*') if path.is_file()}
    profile = tmp_path / 'profile'
    assert source_adapter_declaration(spec, profile).wheels == (wheel,)
    assert source_adapter_resource_ids(spec, profile) == set()
    assert activate_source_adapter(spec, profile) == bundled_paths
    assert not profile.exists()
    try:
        from bundle_fixture import VALUE
        assert VALUE == 42  # Verified source, even if a matching bytecode cache exists.
        assert activate_source_adapter(spec, profile) == bundled_paths
        assert before == {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob('*') if path.is_file()}
        paths = prepare_source_adapter(spec, profile)
        assert paths != bundled_paths and paths[0].is_relative_to(profile)
        assert source_adapter_resource_ids(spec, profile) == {'adapter:' + wheel.sha256}
        ledger = ManagedResourceLedger(profile)
        ledger.release_consumer('source:fixture.bundle')
        ledger.collect()
        assert not paths[0].exists()
        assert source.exists() and bytecode.exists()
        assert before == {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob('*') if path.is_file()}
    finally:
        sys.modules.pop('bundle_fixture', None)


def test_source_preparation_checks_selected_but_not_loaded_adapter_versions(tmp_path, monkeypatch):
    from backend.packs.adapters import prepare_source_adapter
    from backend.packs.installation import PackInstallationManager
    first, _ = library(version='1.0')
    selected = SimpleNamespace(adapter=AdapterDependencies(requirements=('fixture-helper==1.0',), wheels=(first,)))
    monkeypatch.setattr(PackInstallationManager, 'selected', lambda self: {'selected.fixture': selected})
    wheel, data = library(version='2.0')
    wheels = tmp_path / 'wheelhouse'
    wheels.mkdir()
    (wheels / wheel.path.rsplit('/', 1)[-1]).write_bytes(data)
    spec = SimpleNamespace(spec_digest='c' * 64, distribution_id='fixture.source',
                           backend=SimpleNamespace(python=('fixture-helper==2.0',)))
    with pytest.raises(ValueError, match='Conflicting shared adapter dependency'):
        prepare_source_adapter(spec, tmp_path / 'profile', dependency_wheels=wheels)
    assert not (tmp_path / 'profile/packs/adapter-wheels').exists()


def test_archive_dependency_is_verified_prepared_and_imported_from_shared_cache(tmp_path, monkeypatch):
    import hashlib
    import json
    from backend.tests.test_pack_modules import distribution, zipped
    from backend.packs.archive import zip_files, inspect_archive
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.builtin import create_builtin_registry
    from backend.plugins.loader import load_installed_packs
    monkeypatch.setattr(sys, 'path', list(sys.path))
    with ZipFile(io.BytesIO(distribution())) as archive:
        files = zip_files(archive)
    with ZipFile(io.BytesIO(files['backend/fixture.whl'])) as archive:
        adapter_files = zip_files(archive)
    adapter_files['oaw_module_fixture/__init__.py'] += b'\nfrom fixture_helper import VALUE\n'
    adapter_files['oaw_module_fixture-1.0.0.dist-info/METADATA'] += b'Requires-Dist: fixture-helper==1.0\n'
    files['backend/fixture.whl'] = zipped(adapter_files)
    wheel, data = library()
    manifest = json.loads(files['manifest.json'])
    manifest['adapter'] = AdapterDependencies(requirements=('fixture-helper==1.0',), wheels=(wheel,)).model_dump(mode='json')
    files['manifest.json'] = json.dumps(manifest).encode()
    files[wheel.path] = data
    files.pop('checksums.json')
    files['checksums.json'] = json.dumps({name: hashlib.sha256(value).hexdigest() for name, value in files.items()}).encode()
    artifact = zipped(files)
    inspect_archive(artifact)
    assert not (tmp_path / 'packs').exists()
    try:
        manager = PackInstallationManager(tmp_path, create_builtin_registry())
        manager.install(artifact)
        load_installed_packs(create_builtin_registry(), tmp_path)
        assert sys.modules['oaw_module_fixture'].VALUE == 42
        assert 'adapter-wheels' in sys.modules['fixture_helper'].__file__
    finally:
        for name in list(sys.modules):
            if name.startswith(('fixture_helper', 'oaw_module_fixture')):
                del sys.modules[name]
