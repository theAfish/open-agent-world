"""Native workspace write boundaries, separate from cwd and UI filtering."""
from pathlib import Path
from types import SimpleNamespace
import os
import sys

import pytest

from backend.sandbox.models import ResourceAccess, SandboxSecurityError, SandboxLimits, ResourceAttachment
from backend.sandbox.linux import bubblewrap_command
from backend.sandbox.darwin_seatbelt import seatbelt_profile, DarwinSeatbeltBackend
from backend.sandbox.macos_container import MacosContainerSandboxBackend, _Record


def test_native_policies_keep_root_read_only_and_only_open_owned_directory(tmp_path):
    own, other = tmp_path / 'My session', tmp_path / 'Other session'
    own.mkdir(); other.mkdir()
    alias = ResourceAttachment('box', 'alias', other, 'resources/alias', ResourceAccess.READ_WRITE)
    argv = bubblewrap_command(tmp_path, ResourceAccess.READ_WRITE, [alias], ['echo', 'ok'], {},
        working_directory=own.name, workspace_write_directory=own.name)
    assert ['--ro-bind', str(tmp_path), '/workspace'] == argv[argv.index(str(tmp_path)) - 1:argv.index(str(tmp_path)) + 2]
    assert ['--bind', str(own), '/workspace/My session'] == argv[argv.index(str(own)) - 1:argv.index(str(own)) + 2]
    assert argv[argv.index(str(other)) - 1] == '--ro-bind'  # no writable attachment alias
    profile = seatbelt_profile(tmp_path / 'private', tmp_path, tmp_path / 'home', tmp_path / 'tmp', [alias],
        workspace_write_directory=own.name)
    write_rules = profile.split('(allow file-write*', 1)[1].split('(allow', 1)[0]
    assert f'(subpath "{own}")' in write_rules
    assert f'(subpath "{tmp_path}")' not in write_rules
    assert f'(subpath "{other}")' not in write_rules
    backend = MacosContainerSandboxBackend(tmp_path / 'managed', container_path='/bin/echo',
        python_runtime=SimpleNamespace(root=tmp_path / 'python'))
    command = backend._container_command(_Record('box', tmp_path, workspace_path=str(tmp_path)), None,
        SandboxLimits(), workspace_write_directory=own.name)
    assert f'{tmp_path}:/workspace:ro' in command and f'{own}:/workspace/My session' in command


def test_writable_directory_rejects_hardlink_aliases_and_traversal(tmp_path):
    from backend.sandbox.workspace_access import writable_path
    from backend.sandbox.models import SandboxValidationError
    own = tmp_path / 'own'; own.mkdir()
    outside = tmp_path / 'other.txt'; outside.write_text('keep')
    os.link(outside, own / 'alias.txt')
    with pytest.raises(SandboxSecurityError, match='hardlinked'):
        writable_path(tmp_path, 'own')
    with pytest.raises(SandboxValidationError):
        writable_path(tmp_path, '../outside')


@pytest.mark.parametrize('launch_fails', [False, True])
@pytest.mark.asyncio
async def test_windows_scope_acl_is_active_during_launch_and_restored_afterward(tmp_path, launch_fails):
    from backend.sandbox.windows import WindowsSandboxBackend
    from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
    native = FakeWindowsNativeApi()
    backend = WindowsSandboxBackend(tmp_path / 'managed', native_api=native)
    await backend.create('scoped')
    await backend.start('scoped')
    root = Path((await backend.get('scoped')).workspace)
    own = root / 'Own'; own.mkdir()
    (root / 'Other').mkdir()
    original = native.run_appcontainer
    def launch(*args, **kwargs):
        assert native.grants[-2:] == [(root, True), (own, False)]
        if launch_fails:
            raise SandboxSecurityError('native launch failed')
        return original(*args, **kwargs)
    native.run_appcontainer = launch
    try:
        if launch_fails:
            with pytest.raises(SandboxSecurityError, match='native launch failed'):
                await backend.execute('scoped', ['cmd.exe', '/c', 'echo test'],
                    working_directory='Own', workspace_write_directory='Own')
        else:
            result = await backend.execute('scoped', ['cmd.exe', '/c', 'echo test'],
                working_directory='Own', workspace_write_directory='Own')
            assert result.exit_code == 0
        assert native.grants[-1] == (root, False)
    finally:
        await backend.destroy('scoped')


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS') != '1',
    reason='set OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS=1 on macOS')
@pytest.mark.asyncio
async def test_native_seatbelt_allows_owned_writes_and_denies_sibling_mutation(tmp_path):
    backend = DarwinSeatbeltBackend(tmp_path / 'managed')
    available, reason = await backend.probe()
    assert available, reason
    await backend.create('scoped')
    await backend.start('scoped')
    info = await backend.get('scoped')
    root = Path(info.workspace)
    (root / 'Own').mkdir(); (root / 'Other').mkdir()
    (root / 'Other/input.txt').write_text('keep')
    (root / 'root.txt').write_text('root')
    try:
        result = await backend.execute('scoped', ['/bin/sh', '-c', 'cat ../Other/input.txt; printf yes > result.txt'],
            working_directory='Own', workspace_write_directory='Own')
        assert result.exit_code == 0 and 'keep' in result.stdout
        assert (root / 'Own/result.txt').read_text() == 'yes'
        for command in ['printf bad > ../Other/input.txt', 'rm ../Other/input.txt',
                        'mv ../Other/input.txt ../Other/moved.txt', 'printf bad > ../root.txt']:
            result = await backend.execute('scoped', ['/bin/sh', '-c', command],
                working_directory='Own', workspace_write_directory='Own')
            assert result.exit_code != 0, command
        assert (root / 'Other/input.txt').read_text() == 'keep'
        assert (root / 'root.txt').read_text() == 'root'
        # Global mode has the original complete workspace access.
        result = await backend.execute('scoped', ['/bin/sh', '-c', 'printf global > Other/input.txt'])
        assert result.exit_code == 0 and (root / 'Other/input.txt').read_text() == 'global'
    finally:
        await backend.destroy('scoped')
