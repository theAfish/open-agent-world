import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from backend.sandbox.python_runtime import SharedPythonRuntime


@pytest.fixture
def installed_probe(tmp_path, monkeypatch):
    if not shutil.which('uv'):
        pytest.skip('uv required for real environment acceptance')
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    with zipfile.ZipFile(wheels / 'oaw_shared_probe-1.0-py3-none-any.whl', 'w') as wheel:
        files = {
            'oaw_shared_probe.py': 'VALUE = "shared-package-ok"\n'
                'def main():\n'
                '    import json, sys\n'
                '    print(json.dumps([VALUE, sys.prefix, sys.argv[1:]]))\n',
            'oaw_shared_probe-1.0.dist-info/entry_points.txt':
                '[console_scripts]\noaw-shared-probe = oaw_shared_probe:main\n',
            'oaw_shared_probe-1.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: oaw-shared-probe\nVersion: 1.0\n',
            'oaw_shared_probe-1.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: oaw-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        }
        files['oaw_shared_probe-1.0.dist-info/RECORD'] = ''.join(f'{name},,\n' for name in files)
        for name, data in files.items():
            wheel.writestr(name, data)
    original = SharedPythonRuntime._run
    installed_launchers = []
    def offline_run(self, argv):
        if 'install' in argv:
            argv = [*argv, '--no-index', '--find-links', str(wheels)]
        result = original(self, argv)
        if 'install' in argv:
            launcher = self.bin / ('oaw-shared-probe.exe' if os.name == 'nt' else 'oaw-shared-probe')
            installed_launchers.append((launcher, launcher.read_bytes()))
        return result
    monkeypatch.setattr(SharedPythonRuntime, '_run', offline_run)
    first = SharedPythonRuntime(tmp_path / 'data with spaces')
    first.prepare_sync(['oaw-shared-probe==1.0'])
    second = SharedPythonRuntime(tmp_path / 'data with spaces')
    second.prepare_sync()
    assert first.python == second.python
    return first, second, installed_launchers


def test_real_venv_persists_wheel_across_instances_and_workspaces(tmp_path, installed_probe):
    first, second, installed_launchers = installed_probe
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
        environment = os.environ.copy()
        second.environment(environment)
        arguments = ['space value', '', 'quote"value', '$literal;value']
        cli = str(second.bin / ('oaw-shared-probe.exe' if os.name == 'nt' else 'oaw-shared-probe'))
        commands = [[cli, *arguments], [str(second.python), '-c',
            'import subprocess,sys; subprocess.run(["oaw-shared-probe", *sys.argv[1:]], check=True)', *arguments]]
        if os.name != 'nt':
            commands.append(['/bin/sh', '-c', 'exec oaw-shared-probe "$@"', 'probe', *arguments])
        for command in commands:
            result = subprocess.run(command, cwd=workspace, env=environment,
                capture_output=True, text=True, check=True)
            value, prefix, argv = json.loads(result.stdout)
            assert value == 'shared-package-ok'
            assert Path(prefix) == first.venv
            assert argv == arguments
    assert 'include-system-site-packages = false' in (first.venv / 'pyvenv.cfg').read_text()
    site = next(first.venv.glob('Lib/site-packages')) if os.name == 'nt' else next(first.venv.glob('lib/python*/site-packages'))
    marker = tmp_path / 'host-startup-hook-ran'
    (site / 'untrusted.pth').write_text(f'import pathlib; pathlib.Path({str(marker)!r}).touch()\n')
    second.prepare_sync(['oaw-shared-probe==1.0'])
    assert not marker.exists(), 'installer must never execute shared Python startup hooks on the host'
    # Upgrade an existing environment without reinstalling or importing packages.
    launcher, original_bytes = installed_launchers[0]
    launcher.write_bytes(original_bytes)
    (first.root / 'ready.json').write_text(json.dumps({'python': str(first.python)}))
    count = len(installed_launchers)
    second.prepare_sync()
    assert len(installed_launchers) == count
    assert not marker.exists(), 'migration must not execute package startup hooks either'
    (site / 'untrusted.pth').unlink()
    result = subprocess.run([str(launcher)], capture_output=True, text=True, check=True)
    assert Path(json.loads(result.stdout)[1]) == first.venv


@pytest.mark.skipif(sys.platform != 'linux', reason='requires native Linux sandbox')
def test_cli_inside_real_linux_sandbox(tmp_path, installed_probe):
    from backend.sandbox.linux import LinuxSandboxBackend
    first, runtime, _ = installed_probe

    async def run():
        available, reason = await LinuxSandboxBackend.probe()
        if not available:
            pytest.skip(reason)
        backend = LinuxSandboxBackend(tmp_path / 'sandbox', python_runtime=runtime)
        await backend.create('probe')
        await backend.start('probe')
        try:
            for command in (
                ['oaw-shared-probe', 'direct'],
                ['/bin/sh', '-c', 'oaw-shared-probe shell'],
                ['python3', '-c', 'import subprocess; subprocess.run(["oaw-shared-probe", "child"], check=True)'],
                ['python', '-c', 'from oaw_shared_probe import main; main()', 'python'],
            ):
                result = await backend.execute('probe', command)
                assert result.exit_code == 0, result.stderr
                value, prefix, arguments = json.loads(result.stdout)
                assert value == 'shared-package-ok'
                assert Path(prefix) == first.venv
                assert arguments
            result = await backend.execute('probe', ['python', '-c',
                'import pathlib,sys; pathlib.Path(sys.prefix, "must-not-write").touch()'])
            assert result.exit_code != 0
            assert not (runtime.venv / 'must-not-write').exists()
        finally:
            await backend.terminate('probe')
    asyncio.run(run())


def test_failed_install_repairs_partial_launchers_and_marks_crash_recovery(installed_probe, monkeypatch):
    _, runtime, installed_launchers = installed_probe
    launcher, original_bytes = installed_launchers[0]

    def interrupted(argv):
        # Model an installer that replaced a launcher before failing. A process
        # crash at this point must also leave the ready receipt invalidated.
        assert json.loads((runtime.root / 'ready.json').read_text())['launcher_version'] == 0
        launcher.write_bytes(original_bytes)
        raise RuntimeError('interrupted install')

    monkeypatch.setattr(runtime, '_run', interrupted)
    with pytest.raises(RuntimeError, match='interrupted install'):
        runtime.prepare_sync(['oaw-shared-probe==1.0'])
    result = subprocess.run([str(launcher)], capture_output=True, text=True, check=True)
    assert Path(json.loads(result.stdout)[1]) == runtime.venv
    assert json.loads((runtime.root / 'ready.json').read_text())['launcher_version'] != 0


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shebangs and hardlinks')
def test_launcher_repair_preserves_cache_links_modes_and_unrelated_files(tmp_path):
    from backend.sandbox.python_launchers import repair_python_launchers
    binary = tmp_path / 'bin'
    binary.mkdir()
    cached = tmp_path / 'cached'
    original = b'#!/usr/bin/python3\nprint("probe")\n'
    cached.write_bytes(original)
    cached.chmod(0o755)
    launcher = binary / 'probe'
    os.link(cached, launcher)
    link = binary / 'link'
    link.symlink_to(cached)
    shell = binary / 'shell'
    shell.write_bytes(b'#!/bin/sh\necho unrelated\n')
    interpreter = binary / 'python'
    interpreter.write_bytes(b'ELF-placeholder')
    repair_python_launchers(binary, Path('/usr/bin/python3'), interpreter)
    assert cached.read_bytes() == original
    assert link.is_symlink()
    assert shell.read_bytes() == b'#!/bin/sh\necho unrelated\n'
    assert interpreter.read_bytes() == b'ELF-placeholder'
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert str(interpreter).encode() in launcher.read_bytes()
    modified = launcher.stat().st_mtime_ns
    repair_python_launchers(binary, Path('/usr/bin/python3'), interpreter)
    assert launcher.stat().st_mtime_ns == modified
