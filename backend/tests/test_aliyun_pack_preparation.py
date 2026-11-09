"""Portable offline dependency output, using real Pack wheel/receipt APIs."""
import importlib
from importlib import metadata
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from backend.packs import adapters
from backend.packs.planning import environment_provider
from backend.packs.spec import read_source_pack
from backend.tests.test_aliyun_operator_execution import worker
from backend.tests.test_pack_adapters import isolated_adapter_import_paths, library  # noqa: F401
from backend.tests.test_pack_spec import source as source_pack


@pytest.mark.parametrize('from_build_runtime', [False, True])
def test_preparation_materializes_and_activates_dependencies_in_candidate(tmp_path, monkeypatch, from_build_runtime):
    roots = [tmp_path / name for name in ('source', 'previous')]
    for root in roots:
        (root / 'plugins').mkdir(parents=True)
        pack = source_pack(root / 'plugins', '''
[environments.worker]
target = "host-worker"
provider = "python"
management = "managed"
prepare = "install"
verify_imports = ["json"]
verify_script = "verify.py"
''')
        # Windows manual releases used CRLF; GitHub artifacts use LF. Both
        # verifiers must run, but unchanged dependencies should remain offline.
        newline = b'\r\n' if root == roots[1] else b'\n'
        (pack / 'verify.py').write_bytes(b'import json' + newline)
        project = pack / 'pyproject.toml'
        project.write_text(project.read_text().replace('pydantic>=2,<3', 'fixture-release-helper==1.0'))
    project = roots[0] / 'plugins/sample/pyproject.toml'
    project.write_text(project.read_text().replace('1.2.0', '1.2.1'))
    current = read_source_pack(project.parent)
    old = read_source_pack(roots[1] / 'plugins/sample')
    assert old.spec_digest != current.spec_digest
    bundle = tmp_path / 'candidate'
    bundle.mkdir()
    wheel, data = library('fixture-release-helper')
    house = tmp_path / 'wheels'
    house.mkdir()
    (house / Path(wheel.path).name).write_bytes(data)
    prefix = tmp_path / 'independent-build-runtime'
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    manager, old_recipe = environment_provider(old.owner, 'worker', old.environments['worker'], old.source_root, bundle)
    old_handle = manager.prepare_python(old_recipe)
    (old_handle.root / 'packages/prepared.txt').write_text('reuse the prepared dependencies')
    old_receipt = (old_handle.root / 'ready.json').read_bytes()
    from backend.packs import environments
    monkeypatch.setattr(environments.shutil, 'which', lambda _: pytest.fail('Environment reuse must remain offline'))
    if from_build_runtime:
        adapters.prepare_source_adapter(current, prefix / 'oaw-runtime-bundle', dependency_wheels=house)
    else:
        adapters.prepare_source_adapter(old, bundle, dependency_wheels=house)

    def no_network(*args, **kwargs):
        raise RuntimeError('Offline release preparation cannot resolve dependencies from the Internet')

    monkeypatch.setattr(adapters, 'resolve_adapter', no_network)
    from backend.cloud.aliyun import app
    checked = []

    def registry(root):
        location = adapters._source_adapter_location(current, bundle, include_bundle=False)
        assert location is not None and location[1] == bundle
        assert metadata.distribution('fixture-release-helper').version == '1.0'
        assert importlib.import_module('fixture_release_helper').VALUE == 42
        checked.append(root)
        return SimpleNamespace(load_failures=[])

    monkeypatch.setattr(app, 'approved_plugins', registry)
    try:
        result = worker.prepare(roots[0], roots[1], tmp_path / 'scratch', bundle)
        assert result == {'prepared_packs': ['example.runtime']}
        assert checked == [tmp_path / 'scratch/probe']
        adapter, root = adapters._source_adapter_location(current, bundle, include_bundle=False)
        assert root == bundle and adapters.prepared_adapter_paths(adapter, bundle, read_only=True)
        manager, recipe = environment_provider(current.owner, 'worker', current.environments['worker'], current.source_root, bundle)
        handle = manager.read_python(recipe)
        assert handle.fingerprint != old_handle.fingerprint
        assert (handle.root / 'packages/prepared.txt').read_text() == 'reuse the prepared dependencies'
        assert (old_handle.root / 'ready.json').read_bytes() == old_receipt
    finally:
        sys.modules.pop('fixture_release_helper', None)
