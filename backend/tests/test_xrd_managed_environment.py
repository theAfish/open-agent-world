import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec
from backend.packs.spec import read_source_pack
from oaw_xrd import runtime_environment, profile


def test_declared_xrd_recipe_matches_runtime_adapter():
    root = Path(__file__).resolve().parents[2] / 'plugins/xrd'
    declaration = read_source_pack(root).environments['scientific']
    assert PythonEnvironmentSpec.from_declaration('research.xrd', 'scientific', declaration, root).fingerprint == runtime_environment.environment_spec().fingerprint


def test_real_xrd_worker_uses_private_local_fixture_wheel(tmp_path, monkeypatch):
    if not shutil.which('uv'):
        pytest.skip('uv required for offline local-wheel installation')
    monkeypatch.setenv('UV_OFFLINE', '1')
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('OAW_XRD_PYTHON', raising=False)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'injected-host-packages'))
    wheel = tmp_path / 'xrd_worker_fixture-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('matching.py', 'import sys\ndef match_patterns(inputs, options, **kwargs):\n return {"fixture": inputs[0], "paths": sys.path}\n')
        archive.writestr('xrd_worker_fixture-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: xrd-worker-fixture\nVersion: 1.0\n')
        archive.writestr('xrd_worker_fixture-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('xrd_worker_fixture-1.0.dist-info/RECORD', '')
    package = tmp_path / 'package'
    package.mkdir()
    for name in ('worker_launcher.py', 'worker.py', 'engine.py'):
        shutil.copyfile(runtime_environment.PACKAGE / name, package / name)
    spec = PythonEnvironmentSpec('research.xrd', 'scientific', (str(wheel),), verify_imports=('matching',))
    monkeypatch.setattr(runtime_environment, 'PACKAGE', package)
    monkeypatch.setattr(runtime_environment, 'environment_spec', lambda: spec)
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'input.json').write_text(json.dumps({'mode': 'match'}), encoding='utf-8')
    (run / 'input-snapshot.json').write_text(json.dumps(['private-science-fixture']), encoding='utf-8')
    data_root = tmp_path / 'profile'
    argv, environment, lease = runtime_environment.worker_command('worker.py', (tmp_path, run), data_root=data_root)
    assert 'PYTHONPATH' not in environment
    with lease:
        subprocess.run(argv, env=environment, check=True, timeout=30, capture_output=True)
    result = json.loads((run / 'result.json').read_text(encoding='utf-8'))
    assert result['fixture'] == 'private-science-fixture'
    assert not any('site-packages' in path or 'injected-host' in path for path in result['paths'])
    assert 'matching' not in sys.modules
    assert PackEnvironmentManager(data_root).status(spec)['status'] == 'ready'


def test_actual_multiphase_protocol_in_stdlib_managed_worker(tmp_path, monkeypatch):
    monkeypatch.delenv('OAW_XRD_PYTHON', raising=False)
    spec = PythonEnvironmentSpec('research.xrd', 'fixture', verify_imports=('json',))
    monkeypatch.setattr(runtime_environment, 'environment_spec', lambda: spec)
    (tmp_path / 'multiphase-input.json').write_text('{}', encoding='utf-8')
    argv, environment, lease = runtime_environment.worker_command('multiphase_worker.py', (tmp_path,), data_root=tmp_path / 'profile')
    with lease:
        completed = subprocess.run(argv, input='{"command":"unsupported"}\n', text=True, encoding='utf-8', capture_output=True, env=environment, timeout=30, check=True)
    assert json.loads(completed.stdout) == {'ok': False, 'error': 'ValueError: Unknown worker command'}


def test_profile_binding_is_concurrent_and_survives_async_threads(tmp_path, monkeypatch):
    monkeypatch.delenv('OAW_XRD_ROOT', raising=False)
    monkeypatch.delenv('OAW_XRD_RUN_ROOT', raising=False)
    async def callback():
        await asyncio.sleep(0)
        task = asyncio.create_task(asyncio.to_thread(profile.roots))
        return await task
    async def execute():
        return await asyncio.gather(profile.bind(callback, tmp_path / 'one')(), profile.bind(callback, tmp_path / 'two')())
    first, second = asyncio.run(execute())
    assert first[0] == tmp_path / 'one/packs-data/research.xrd'
    assert second[0] == tmp_path / 'two/packs-data/research.xrd'
    assert profile._data_root.get() is None


def test_legacy_override_is_explicit_and_never_installs(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_PYTHON', sys.executable)
    monkeypatch.setattr(runtime_environment, 'prepare', lambda *args: pytest.fail('legacy override installed dependencies'))
    argv, _, lease = runtime_environment.worker_command('multiphase_worker.py', (tmp_path,), data_root=tmp_path)
    assert argv[0] == str(Path(sys.executable).resolve())
    assert '-I' in argv and '-S' not in argv
    assert not (tmp_path / 'runtime').exists()
