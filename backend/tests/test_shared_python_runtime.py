import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import zipfile

import pytest

from backend.plugins.bootstrap import PluginEnvironmentBootstrap
from backend.plugins.registry import PluginDefinition, PluginDescriptor, PluginRegistry
from backend.sandbox.environment import validate_command_environment
from backend.sandbox.linux import bubblewrap_command, minimal_linux_environment
from backend.sandbox.models import ResourceAccess, SandboxValidationError
from backend.sandbox.python_runtime import SharedPythonRuntime, mutation_lock, validate_requirements


def test_timeout_preserves_live_progress_and_uses_package_deadline(tmp_path, monkeypatch):
    from backend.sandbox.python_runtime import PACKAGE_INSTALL_TIMEOUT
    runtime = SharedPythonRuntime(tmp_path)
    runtime.root.mkdir(parents=True)
    def stalled(argv, **options):
        assert options['timeout'] == PACKAGE_INSTALL_TIMEOUT
        options['stdout'].write(b'Resolved 14 packages\nDownloading scipy (35.9MiB)\n')
        options['stdout'].flush()
        assert 'Downloading scipy' in (runtime.root / 'install-output.log').read_text()
        raise subprocess.TimeoutExpired(argv, options['timeout'])
    monkeypatch.setattr(subprocess, 'run', stalled)
    with pytest.raises(RuntimeError, match='Downloading scipy'):
        runtime._run(['uv', 'pip', 'install', 'ase'])
    records = [json.loads(line) for line in (runtime.root / 'install.log').read_text().splitlines()]
    assert records[0]['state'] == 'running'
    assert records[-1]['state'] == 'failed'
    assert 'Downloading scipy' in records[-1]['output']


@pytest.mark.asyncio
async def test_wsl_deadline_covers_setup_bootstrap_and_package_install(tmp_path, monkeypatch):
    from backend.sandbox.wsl import WslSandboxBackend
    from backend.sandbox.python_runtime import PACKAGE_INSTALL_TIMEOUT, RUNTIME_SETUP_TIMEOUT
    backend = WslSandboxBackend(tmp_path, distribution='Ubuntu')
    async def request(payload, *, timeout):
        assert timeout > 60 + RUNTIME_SETUP_TIMEOUT + 2 * PACKAGE_INSTALL_TIMEOUT
        assert payload['requirements'] == ['ase']
        return {'requirements': ['ase']}
    monkeypatch.setattr(backend, '_request', request)
    assert await backend.prepare_python(['ase']) == {'requirements': ['ase']}


@pytest.mark.parametrize('value', [['--target=/tmp'], ['../evil'], ['x @ https://example.com/x.whl'], ['-r requirements.txt'], ['x;python_version>"3"']])
def test_installer_rejects_options_paths_and_build_inputs(value):
    with pytest.raises(ValueError):
        validate_requirements(value)


@pytest.mark.parametrize('name', ['PYTHONPATH', 'VIRTUAL_ENV', 'PIP_TARGET', 'UV_PROJECT_ENVIRONMENT'])
def test_invocation_cannot_override_python_selection(name):
    with pytest.raises(SandboxValidationError):
        validate_command_environment({name: 'host'})


def test_shared_mutations_and_bootstrap_receipts_are_serialized(tmp_path, monkeypatch):
    active = 0
    peak = 0
    calls = []
    guard = threading.Lock()
    def fake_run(self, argv):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            calls.append(argv)
        time.sleep(.05)
        with guard:
            active -= 1
    def fake_ensure(self):
        self.bin.mkdir(parents=True, exist_ok=True)
        self.python.touch()
    monkeypatch.setattr(SharedPythonRuntime, '_ensure', fake_ensure)
    monkeypatch.setattr(SharedPythonRuntime, '_run', fake_run)
    monkeypatch.setattr(shutil, 'which', lambda name: 'uv')
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(SharedPythonRuntime(tmp_path).prepare_sync, ['sample==1'], 'plugin') for _ in range(2)]
        for future in futures:
            future.result()
    assert peak == 1
    assert len(calls) == 1
    SharedPythonRuntime(tmp_path).prepare_sync(['sample==2'], 'plugin')
    assert len(calls) == 2


def test_linux_runtime_mount_is_readonly_and_workspace_is_separate(tmp_path):
    runtime = SharedPythonRuntime(tmp_path)
    env = minimal_linux_environment()
    runtime.environment(env)
    command = bubblewrap_command(tmp_path / 'workspace', ResourceAccess.READ_WRITE, (),
        runtime.command(['python', '-c', 'pass']), env, python_runtime=runtime)
    index = command.index(str(runtime.venv))
    assert command[index - 1] == '--ro-bind'
    assert command[index + 1] == str(runtime.venv)
    assert 'PYTHONPATH' not in env
    assert env['PATH'].startswith(str(runtime.bin))
    assert command[-3] == str(runtime.python)


def test_lock_serializes_separate_backend_processes(tmp_path):
    code = '''
from pathlib import Path
import sys,time
from backend.sandbox.python_runtime import mutation_lock
root=Path(sys.argv[1])
with mutation_lock(root):
    with (root/'events').open('a') as log: log.write('start\\n')
    time.sleep(.15)
    with (root/'events').open('a') as log: log.write('end\\n')
'''
    processes = [subprocess.Popen([sys.executable, '-c', code, str(tmp_path)]) for _ in range(2)]
    assert all(p.wait(timeout=10) == 0 for p in processes)
    assert (tmp_path / 'events').read_text().splitlines() == ['start', 'end', 'start', 'end']


@pytest.mark.asyncio
async def test_cancellation_drains_the_mutation_before_returning(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    def prepare(self, *args):
        with mutation_lock(self.root):
            entered.set()
            assert release.wait(5)
            completed.set()
    monkeypatch.setattr(SharedPythonRuntime, 'prepare_sync', prepare)
    task = asyncio.create_task(SharedPythonRuntime(tmp_path).prepare(['sample']))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()


def plugin_registry(requirements=('sample==1',)):
    registry = PluginRegistry()
    registry.install(PluginDefinition(PluginDescriptor(id='test.runtime', version='1',
        plugin_api_version='1.0', python_requirements=requirements), lambda r: None))
    return registry


@pytest.mark.asyncio
async def test_bootstrap_discovery_restart_change_failure_retry_and_removal(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    fail = False
    installs = []
    async def catalog():
        return [SimpleNamespace(id='test', available=True)]
    async def prepare(target, requirements, bootstrap_key=None):
        entered.set()
        await release.wait()
        if fail:
            raise RuntimeError('wheel unavailable')
        installs.append(requirements)
    manager = SimpleNamespace(registry=SimpleNamespace(catalog=catalog), prepare_python=prepare)
    bootstrap = PluginEnvironmentBootstrap(tmp_path, plugin_registry(), manager)
    assert bootstrap.records()[0]['state'] == 'discovered'
    initialized = bootstrap.records()[0]['initialized_at']
    bootstrap.enqueue()
    await entered.wait()
    assert bootstrap.records()[0]['state'] == 'environment_pending'
    release.set()
    await bootstrap.shutdown()
    assert bootstrap.records()[0]['state'] == 'environment_ready'
    restarted = PluginEnvironmentBootstrap(tmp_path, plugin_registry(('sample==2',)), manager)
    assert restarted.records()[0]['initialized_at'] == initialized
    assert restarted.records()[0]['state'] == 'discovered'
    fail = True
    restarted.enqueue()
    await restarted.shutdown()
    assert restarted.records()[0]['state'] == 'environment_failed'
    assert 'wheel unavailable' in restarted.records()[0]['error']
    fail = False
    restarted.enqueue()
    await restarted.shutdown()
    assert restarted.records()[0]['state'] == 'environment_ready'
    retained = tmp_path / 'runtime' / 'python' / 'package-marker'
    retained.parent.mkdir(exist_ok=True)
    retained.touch()
    removed = PluginEnvironmentBootstrap(tmp_path, PluginRegistry(), manager)
    assert removed.records() == []
    assert retained.exists()


def test_real_venv_persists_wheel_across_instances_and_workspaces(tmp_path, monkeypatch):
    if not shutil.which('uv'):
        pytest.skip('uv required for real environment acceptance')
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    with zipfile.ZipFile(wheels / 'oaw_shared_probe-1.0-py3-none-any.whl', 'w') as wheel:
        files = {
            'oaw_shared_probe.py': 'VALUE = "shared-package-ok"\n',
            'oaw_shared_probe-1.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: oaw-shared-probe\nVersion: 1.0\n',
            'oaw_shared_probe-1.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: oaw-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        }
        files['oaw_shared_probe-1.0.dist-info/RECORD'] = ''.join(f'{name},,\n' for name in files)
        for name, data in files.items():
            wheel.writestr(name, data)
    original = SharedPythonRuntime._run
    def offline_run(self, argv):
        if 'install' in argv:
            argv = [*argv, '--no-index', '--find-links', str(wheels)]
        return original(self, argv)
    monkeypatch.setattr(SharedPythonRuntime, '_run', offline_run)
    first = SharedPythonRuntime(tmp_path / 'data')
    first.prepare_sync(['oaw-shared-probe==1.0'])
    second = SharedPythonRuntime(tmp_path / 'data')
    second.prepare_sync()
    assert first.python == second.python
    for name in ('workspace-a', 'workspace-b'):
        workspace = tmp_path / name
        workspace.mkdir()
        result = subprocess.run([str(second.python), '-I', '-c',
            'import oaw_shared_probe,sys,json; print(json.dumps([oaw_shared_probe.VALUE,sys.prefix,sys.path]))'],
            cwd=workspace, capture_output=True, text=True, check=True)
        value, prefix, paths = json.loads(result.stdout)
        assert value == 'shared-package-ok'
        assert Path(prefix) == first.venv
        assert str(Path(sys.prefix) / 'Lib' / 'site-packages') not in paths
    assert 'include-system-site-packages = false' in (first.venv / 'pyvenv.cfg').read_text()
    site = next(first.venv.glob('Lib/site-packages')) if os.name == 'nt' else next(first.venv.glob('lib/python*/site-packages'))
    marker = tmp_path / 'host-startup-hook-ran'
    (site / 'untrusted.pth').write_text(f'import pathlib; pathlib.Path({str(marker)!r}).touch()\n')
    second.prepare_sync(['oaw-shared-probe==1.0'])
    assert not marker.exists(), 'installer must never execute shared Python startup hooks on the host'
