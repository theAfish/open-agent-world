"""Folder authority stays host-private and follows command configuration."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from backend.execution_config import resolve_sandbox_configuration
from backend.sandbox.environment import folder_environment
from backend.sandbox.environment import host_environment_path
from backend.sandbox.linux import bubblewrap_command
from backend.sandbox.models import FolderMount, ResourceAccess, SandboxValidationError, SandboxBusyError, SandboxSecurityError, SandboxState
from backend.sandbox.windows import WindowsSandboxBackend
from backend.security.execution_folders import folder_store
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client, setup_skill, run
from tests.test_sandbox_workspace import WorkspaceNativeApi


def save_folder(client, node, path, *, access="read_only", revision=None):
    base = f"/api/nodes/{node['id']}"
    if revision is None:
        revision = client.get(base + "/document").json()["revision"]
    return client.put(base + "/environment", json={"expected_revision": revision, "secrets": {},
        "value": {"variables": {"MODELS": {"folder_ref": "models"}}},
        "folders": {"models": {"path": str(path), "access": access}}})


@pytest.mark.parametrize("kind", ["sandbox", "environment"])
def test_folder_binding_is_private_revision_checked_and_not_copied(client, tmp_path, kind):
    path = tmp_path / "model library"
    path.mkdir()
    node = create_node(client, kind)
    before = client.get(f"/api/nodes/{node['id']}/document").json()["revision"]
    saved = save_folder(client, node, path, revision=before)
    assert saved.status_code == 200, saved.text
    assert str(path) not in saved.text
    assert saved.json()["value"]["variables"]["MODELS"] == {"folder_ref": "models"}
    assert client.get(f"/api/nodes/{node['id']}/environment-folders").json() == {
        "models": {"path": str(path), "access": "read_only", "kind": "folder"}}
    assert save_folder(client, node, path, access="read_write", revision=before).status_code == 409
    assert folder_store(client.app.state.services).folder(node['id'], 'models').access == ResourceAccess.READ_ONLY
    imported = create_node(client, kind)
    imported_doc = client.get(f"/api/nodes/{imported['id']}/document").json()
    response = client.put(f"/api/nodes/{imported['id']}/environment", json={
        "value": saved.json()["value"], "expected_revision": imported_doc["revision"], "secrets": {}})
    assert response.status_code == 422
    # Removing the variable through the shared save revokes its private binding.
    response = client.put(f"/api/nodes/{node['id']}/environment", json={
        "value": {"variables": {}}, "expected_revision": saved.json()["revision"], "secrets": {}})
    assert response.status_code == 200
    assert not folder_store(client.app.state.services).configured(node['id'], 'models')
    assert path.is_dir()


def test_global_folder_overrides_and_removal_never_leave_hidden_mounts(client, tmp_path):
    path = tmp_path / "models"
    path.mkdir()
    sandbox = create_node(client, "sandbox")
    response = client.put("/api/settings/sandbox", json={
        "environment_variables": {"MODELS": {"folder_ref": "global-models"}},
        "folders": {"global-models": {"path": str(path)}}})
    assert response.status_code == 200, response.text
    assert response.json()["folder_bindings"]["global-models"]["access"] == "read_only"
    services = client.app.state.services
    mounts = []
    env, _ = resolve_sandbox_configuration(services, sandbox['id'], folder_mounts=mounts)
    assert env == {"MODELS": str(path)} and mounts == [FolderMount('MODELS', str(path))]
    doc = client.get(f"/api/nodes/{sandbox['id']}/document").json()
    response = client.put(f"/api/nodes/{sandbox['id']}/environment", json={"secrets": {},
        "expected_revision": doc["revision"], "value": {"variables": {"models": "plain"}}})
    assert response.status_code == 200
    mounts = []
    assert resolve_sandbox_configuration(services, sandbox['id'], folder_mounts=mounts)[0] == {"models": "plain"}
    assert mounts == []
    assert client.put("/api/settings/sandbox", json={"environment_variables": {}}).status_code == 200
    assert client.get("/api/settings/sandbox").json()["folder_bindings"] == {}
    assert path.is_dir()


def test_missing_folder_is_visible_and_binding_can_be_removed(client, tmp_path):
    path = tmp_path / 'missing-later'
    path.mkdir()
    node = create_node(client, 'sandbox')
    assert save_folder(client, node, path).status_code == 200
    path.rmdir()
    response = client.get(f"/api/sandboxes/{node['id']}/configuration")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary['ready'] is False and summary['variables'][0]['error']
    assert summary['variables'][0]['value'] == str(path)


def test_manual_and_skill_commands_capture_folder_mounts_in_existing_pipeline(runtime_client, tmp_path, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, *_ = setup_skill(client)
    path = tmp_path / 'shared-models'
    path.mkdir()
    assert save_folder(client, sandbox, path).status_code == 200
    captured = []
    original = backend.execute
    async def capture(*args, **kwargs):
        captured.append(kwargs.pop('folder_mounts', ()))
        return await original(*args, **kwargs)
    monkeypatch.setattr(backend, 'execute', capture)
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={'argv': ['cmd.exe']})
    assert response.status_code == 200, response.text
    run(client, agent, sandbox, skill)
    assert captured == [(FolderMount('MODELS', str(path)),)] * 2
    monkeypatch.setattr(backend, 'supports_folder_mounts', False)
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={'argv': ['cmd.exe']})
    assert response.status_code == 422 and 'folder variables' in response.text
    assert len(captured) == 2


@pytest.mark.parametrize("invalid", ["missing", "file", "managed", "relative", "root", "bad-mode"])
def test_folder_validation_is_shared_by_both_settings(client, tmp_path, invalid):
    path = tmp_path / "models"
    path.mkdir()
    access = "read_only"
    if invalid == "missing": path = tmp_path / "absent"
    if invalid == "file":
        path = tmp_path / "file.txt"
        path.write_text("model")
    if invalid == "managed": path = client.app.state.services.settings.data_root
    if invalid == "relative": path = Path("models")
    if invalid == "root": path = Path(tmp_path.anchor)
    if invalid == "bad-mode": access = "execute"
    folder = {"models": {"path": str(path), "access": access}}
    node = create_node(client, "sandbox")
    assert save_folder(client, node, path, access=access).status_code == 422
    response = client.put("/api/settings/sandbox", json={
        "environment_variables": {"MODELS": {"folder_ref": "models"}}, "folders": folder})
    assert response.status_code == 422, response.text
    assert client.get("/api/settings/sandbox").json()["environment_variables"] == {}


def test_linux_folder_mounts_rewrite_paths_and_enforce_read_only():
    folders = [FolderMount("MODELS", "/host/model library"), FolderMount("OUTPUT", "/host/output", ResourceAccess.READ_WRITE)]
    env = folder_environment({"MODELS": "host-path", "REGION": "east"}, folders, "/host/project", ResourceAccess.READ_WRITE)
    assert env == {"MODELS": "/sandbox/folders/MODELS", "OUTPUT": "/sandbox/folders/OUTPUT", "REGION": "east"}
    command = bubblewrap_command(Path('/host/project'), ResourceAccess.READ_WRITE, [], ['true'], env, folder_mounts=folders)
    for folder, flag in zip(folders, ['--ro-bind', '--bind']):
        index = command.index(folder.source)
        assert command[index-1:index+2] == [flag, folder.source, folder.linux_path]
    with pytest.raises(SandboxValidationError, match="different permissions"):
        folder_environment({}, [FolderMount("CHILD", "/host/project/child")], "/host/project", ResourceAccess.READ_WRITE)


@pytest.mark.parametrize("scope", ["global", "sandbox", "environment"])
def test_file_bindings_use_shared_authority_without_authorizing_parent(client, tmp_path, scope):
    path = tmp_path / "model library" / "DPA model.pt"
    path.parent.mkdir()
    path.write_text("model")
    variables = {"DEEPMD_MODEL_PATH": {"file_ref": "model"}}
    bindings = {"model": {"path": str(path), "kind": "file", "access": "read_only"}}
    sandbox = create_node(client, "sandbox")
    node = create_node(client, "environment") if scope == "environment" else sandbox
    if scope == "global":
        response = client.put("/api/settings/sandbox", json={"environment_variables": variables, "folders": bindings})
        assert response.status_code == 200, response.text
        assert response.json()["folder_bindings"] == bindings
    else:
        revision = client.get(f"/api/nodes/{node['id']}/document").json()["revision"]
        response = client.put(f"/api/nodes/{node['id']}/environment", json={
            "value": {"variables": variables}, "secrets": {}, "folders": bindings, "expected_revision": revision})
        assert response.status_code == 200, response.text
        assert str(path) not in response.text
        assert client.get(f"/api/nodes/{node['id']}/environment-folders").json() == bindings
    mounts = []
    env, _ = resolve_sandbox_configuration(client.app.state.services, sandbox['id'],
        environment_id=node['id'] if scope == "environment" else None, folder_mounts=mounts)
    assert env['DEEPMD_MODEL_PATH'] == str(path)
    assert mounts == [FolderMount('DEEPMD_MODEL_PATH', str(path), kind='file')]
    mount = mounts[0]
    assert mount.linux_path == '/sandbox/files/DEEPMD_MODEL_PATH/DPA model.pt'
    command = bubblewrap_command(Path('/project'), ResourceAccess.READ_WRITE, [], ['true'], {}, folder_mounts=mounts)
    assert str(path.parent) not in command
    index = command.index(str(path))
    assert command[index-1:index+2] == ['--ro-bind', str(path), mount.linux_path]


def test_changing_requirement_type_cannot_reuse_folder_authority(client, tmp_path):
    node = create_node(client, "sandbox")
    path = tmp_path / 'models'
    path.mkdir()
    saved = save_folder(client, node, path).json()
    response = client.put(f"/api/nodes/{node['id']}/environment", json={
        "value": {"variables": {"MODEL": {"file_ref": "models"}}}, "secrets": {}, "expected_revision": saved['revision']})
    assert response.status_code == 422 and 'type changed' in response.text


@pytest.mark.parametrize('kind', ['file', 'folder'])
@pytest.mark.parametrize('scope', ['global', 'sandbox'])
def test_unified_path_detects_type_at_preview_and_save(client, tmp_path, kind, scope):
    source = tmp_path / 'resource'
    source.write_text('model') if kind == 'file' else source.mkdir()
    preview = client.post('/api/desktop/inspect-path', json={'path': str(source)})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {'path': str(source), 'kind': kind}
    assert client.get('/api/settings/sandbox').json()['folder_bindings'] == {}
    node = create_node(client, 'sandbox')
    variables = {'DATA': {'path_ref': 'resource'}}
    bindings = {'resource': {'path': str(source), 'access': 'read_only'}}
    if scope == 'global':
        saved = client.put('/api/settings/sandbox', json={'environment_variables': variables, 'folders': bindings})
    else:
        revision = client.get(f"/api/nodes/{node['id']}/document").json()['revision']
        saved = client.put(f"/api/nodes/{node['id']}/environment", json={
            'value': {'variables': variables}, 'folders': bindings, 'secrets': {}, 'expected_revision': revision})
    assert saved.status_code == 200, saved.text
    mounts = []
    resolve_sandbox_configuration(client.app.state.services, node['id'], folder_mounts=mounts)
    assert mounts == [FolderMount('DATA', str(source), kind=kind)]
    summary = client.get(f"/api/sandboxes/{node['id']}/configuration").json()
    assert summary['ready'] and summary['variables'][0]['kind'] == kind
    # A path that changes type on disk cannot silently inherit broader access.
    source.unlink() if kind == 'file' else source.rmdir()
    source.mkdir() if kind == 'file' else source.write_text('replacement')
    summary = client.get(f"/api/sandboxes/{node['id']}/configuration").json()
    assert summary['ready'] is False


def test_unified_path_preview_and_save_reject_missing_resources(client, tmp_path):
    path = str(tmp_path / 'missing')
    assert client.post('/api/desktop/inspect-path', json={'path': path}).status_code == 422
    saved = client.put('/api/settings/sandbox', json={
        'environment_variables': {'DATA': {'path_ref': 'resource'}}, 'folders': {'resource': {'path': path}}})
    assert saved.status_code == 422


def test_path_save_rechecks_type_instead_of_trusting_preview_or_client_kind(client, tmp_path):
    source = tmp_path / 'resource'
    source.write_text('model')
    assert client.post('/api/desktop/inspect-path', json={'path': str(source)}).json()['kind'] == 'file'
    source.unlink()
    source.mkdir()
    saved = client.put('/api/settings/sandbox', json={
        'environment_variables': {'DATA': {'path_ref': 'resource'}},
        'folders': {'resource': {'path': str(source), 'kind': 'file'}}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['folder_bindings']['resource']['kind'] == 'folder'


def test_legacy_binding_can_migrate_to_unified_reference_without_rebinding(client, tmp_path):
    node = create_node(client, 'sandbox')
    source = tmp_path / 'models'
    source.mkdir()
    saved = save_folder(client, node, source).json()
    response = client.put(f"/api/nodes/{node['id']}/environment", json={
        'value': {'variables': {'MODELS': {'path_ref': 'models'}}}, 'secrets': {}, 'expected_revision': saved['revision']})
    assert response.status_code == 200, response.text
    mounts = []
    resolve_sandbox_configuration(client.app.state.services, node['id'], folder_mounts=mounts)
    assert mounts == [FolderMount('MODELS', str(source))]


@pytest.mark.parametrize('invalid', ['directory', 'missing', 'hardlink', 'wrong-kind'])
def test_file_validation_is_shared_and_rejects_wrong_authority(client, tmp_path, invalid):
    path = tmp_path / 'model.pt'
    path.write_text('model')
    if invalid == 'directory': path = tmp_path
    if invalid == 'missing': path = tmp_path / 'missing'
    if invalid == 'hardlink': os.link(path, tmp_path / 'alias.pt')
    variables = {'MODEL': {'file_ref': 'model'}}
    bindings = {'model': {'path': str(path), 'kind': 'folder' if invalid == 'wrong-kind' else 'file'}}
    node = create_node(client, 'sandbox')
    revision = client.get(f"/api/nodes/{node['id']}/document").json()['revision']
    response = client.put(f"/api/nodes/{node['id']}/environment", json={
        'value': {'variables': variables}, 'secrets': {}, 'folders': bindings, 'expected_revision': revision})
    assert response.status_code == 422, response.text
    response = client.put('/api/settings/sandbox', json={'environment_variables': variables, 'folders': bindings})
    assert response.status_code == 422, response.text


def test_source_path_formats_and_runtime_environment_are_distinct():
    assert host_environment_path('/mnt/d/Model library/model.pt', windows=True) == r'D:\Model library\model.pt'
    assert host_environment_path(r'D:\Model library\model.pt', windows=True) == r'D:\Model library\model.pt'
    assert host_environment_path('/home/user/models/model.pt', windows=False) == '/home/user/models/model.pt'
    with pytest.raises(SandboxValidationError, match='specific Linux host'):
        host_environment_path('/home/user/models/model.pt', windows=True)
    with pytest.raises(SandboxValidationError, match='mounted Linux path'):
        host_environment_path(r'D:\Models\model.pt', windows=False)
    mount = FolderMount('MODEL', r'D:\Models\model.pt', kind='file')
    assert folder_environment({}, [mount], r'D:\Project', ResourceAccess.READ_WRITE, windows=True)['MODEL'] == mount.source
    assert folder_environment({}, [mount], '/workspace', ResourceAccess.READ_WRITE)['MODEL'] == '/sandbox/files/MODEL/model.pt'


@pytest.mark.skipif(os.name != 'nt', reason='Windows host aliases')
def test_wsl_drive_spelling_is_canonicalized_at_save(client, tmp_path):
    path = tmp_path / 'model.pt'
    path.write_text('model')
    alias = '/mnt/' + path.drive[0].lower() + '/' + '/'.join(path.parts[1:])
    response = client.put('/api/settings/sandbox', json={
        'environment_variables': {'MODEL': {'file_ref': 'model'}},
        'folders': {'model': {'path': alias, 'kind': 'file'}}})
    assert response.status_code == 200, response.text
    assert response.json()['folder_bindings']['model']['path'] == str(path)


@pytest.mark.asyncio
async def test_windows_folder_grants_are_command_scoped_and_recover_after_failure(tmp_path):
    native = WorkspaceNativeApi()
    backend = WindowsSandboxBackend(tmp_path / "managed", native_api=native)
    path = tmp_path / "models"
    path.mkdir()
    marker = path / "model.pt"
    marker.write_text('keep')
    await backend.create('folder-test')
    await backend.start('folder-test')
    folders = (FolderMount('MODELS', str(path)),)
    await backend.execute('folder-test', ['cmd.exe'], folder_mounts=folders)
    assert native.runs[-1]['environment']['MODELS'] == str(path)
    assert native.workspace_grants[-1][0::2] == (path, True)
    assert native.workspace_revokes[-1][0] == path
    record = await backend._record('folder-test')
    assert not backend._folder_journal(record).exists()
    count = len(native.workspace_grants)
    await backend.execute('folder-test', ['cmd.exe'])
    assert 'MODELS' not in native.runs[-1]['environment'] and len(native.workspace_grants) == count
    # Crash receipt uses the existing workspace identity checks before revocation.
    metadata = path.stat()
    backend._folder_journal(record).write_text(json.dumps([{'path': str(path), 'identity': [metadata.st_dev, metadata.st_ino]}]))
    fresh = WindowsSandboxBackend(tmp_path / "managed", native_api=native)
    await fresh.get('folder-test')
    assert not backend._folder_journal(record).exists()
    assert len(native.workspace_revokes) == 2
    await fresh.destroy('folder-test')
    assert marker.read_text() == 'keep'


@pytest.mark.asyncio
async def test_windows_folder_command_cannot_share_authority_with_concurrent_command(tmp_path):
    native = WorkspaceNativeApi(block_run=True)
    backend = WindowsSandboxBackend(tmp_path / "managed", native_api=native)
    await backend.create('concurrent')
    await backend.start('concurrent')
    path = tmp_path / 'models'
    path.mkdir()
    active = asyncio.create_task(backend.execute('concurrent', ['cmd.exe'], folder_mounts=(FolderMount('MODELS', str(path)),)))
    for _ in range(100):
        if native.runs: break
        await asyncio.sleep(.01)
    try:
        with pytest.raises(SandboxBusyError):
            await backend.execute('concurrent', ['cmd.exe'])
    finally:
        await backend.cancel('concurrent')
        await active
    assert native.workspace_revokes


@pytest.mark.asyncio
async def test_windows_folder_cleanup_failure_keeps_receipt_until_stop(tmp_path, monkeypatch):
    native = WorkspaceNativeApi()
    backend = WindowsSandboxBackend(tmp_path / 'managed', native_api=native)
    await backend.create('cleanup')
    await backend.start('cleanup')
    path = tmp_path / 'models'
    path.mkdir()
    original = native.revoke_workspace
    def fail(*args, **kwargs):
        raise SandboxSecurityError('simulated revocation failure')
    monkeypatch.setattr(native, 'revoke_workspace', fail)
    with pytest.raises(SandboxSecurityError, match='Folder permission cleanup failed'):
        await backend.execute('cleanup', ['cmd.exe'], folder_mounts=(FolderMount('MODELS', str(path)),))
    record = await backend._record('cleanup')
    assert backend._folder_journal(record).is_file()
    assert (await backend.get('cleanup')).state == SandboxState.ERROR
    assert native.workspace_closed
    monkeypatch.setattr(native, 'revoke_workspace', original)
    await backend.terminate('cleanup')
    assert not backend._folder_journal(record).exists()
    await backend.start('cleanup')
    assert (await backend.get('cleanup')).state == SandboxState.READY


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['windows', 'wsl'])
async def test_native_folder_access_and_revocation(tmp_path, runtime):
    if os.name != 'nt' or os.environ.get('OAW_TEST_FOLDER_NATIVE') != '1':
        pytest.skip('set OAW_TEST_FOLDER_NATIVE=1 for Windows and WSL folder acceptance')
    probe = os.environ.get('OAW_TEST_FOLDER_PROBE')
    if runtime == 'windows' and not probe:
        pytest.skip('compile fixtures/folder_access_probe.c and set OAW_TEST_FOLDER_PROBE')
    from backend.sandbox.wsl import WslSandboxBackend
    backend = (WindowsSandboxBackend(tmp_path / 'managed') if runtime == 'windows' else
               WslSandboxBackend(tmp_path / 'managed', distribution=os.environ.get('OAW_TEST_WSL_DISTRO', 'Ubuntu')))
    folder = tmp_path / 'model library'
    folder.mkdir()
    (folder / 'marker.txt').write_text('folder-readable')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private.txt').write_text('not-authorized')
    info = await backend.create('native-folders')
    try:
        await backend.start('native-folders')
        if runtime == 'windows':
            shell = [str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe'), '/d', '/s', '/c']
            read = 'type "%DATA%\\marker.txt"'
            write = 'echo created>"%DATA%\\created.txt"'
            remove = 'del "%DATA%\\created.txt"'
            revoked = f'type "{folder / "marker.txt"}"'
            outside_read = f'type "{outside / "private.txt"}"'
        else:
            shell = ['/bin/sh', '-c']
            read = 'cat "$DATA/marker.txt"'
            write = 'printf created > "$DATA/created.txt"'
            remove = 'rm "$DATA/created.txt"'
            revoked = 'cat /sandbox/folders/DATA/marker.txt'
            outside_read = 'cat "$DATA/../outside/private.txt"'
        readonly = (FolderMount('DATA', str(folder)),)
        writable = (FolderMount('DATA', str(folder), ResourceAccess.READ_WRITE),)
        async def execute(command, mounts=()):
            if runtime == 'windows':
                # A batch file avoids cmd.exe's distinct /c quotation rules.
                (Path(info.workspace) / 'check.cmd').write_text('@echo off\nchcp 65001>nul\n' + command + '\n')
                command = 'check.cmd'
            return await backend.execute('native-folders', [*shell, command], folder_mounts=mounts)
        result = await execute(read, readonly)
        assert result.exit_code == 0 and 'folder-readable' in result.stdout, result
        copy = 'type "%DATA%\\marker.txt">copied.txt' if runtime == 'windows' else 'cp "$DATA/marker.txt" copied.txt'
        result = await execute(copy, readonly)
        assert result.exit_code == 0, result
        copied = await execute('type copied.txt' if runtime == 'windows' else 'cat copied.txt')
        assert 'folder-readable' in copied.stdout
        result = await execute(write, readonly)
        assert result.exit_code != 0 and not (folder / 'created.txt').exists(), result
        result = await execute(outside_read, readonly)
        assert result.exit_code != 0 and 'not-authorized' not in result.stdout, result
        result = await execute(write, writable)
        assert result.exit_code == 0 and (folder / 'created.txt').is_file(), result
        if runtime == 'windows':
            import shutil
            executable = Path(info.workspace) / 'folder-probe.exe'
            shutil.copyfile(probe, executable)
            result = await backend.execute('native-folders', [str(executable)], folder_mounts=readonly, invocation_env={'FOLDER_TEST_ACTION': 'list'})
            assert result.exit_code == 0, result
            result = await backend.execute('native-folders', [str(executable)], folder_mounts=readonly)
            assert result.exit_code != 0 and (folder / 'created.txt').exists(), result
            result = await backend.execute('native-folders', [str(executable)], folder_mounts=writable)
        else:
            result = await execute(remove, writable)
        assert result.exit_code == 0, (result.stdout, result.stderr)
        assert not (folder / 'created.txt').exists()
        result = await execute(revoked)
        assert result.exit_code != 0 and 'folder-readable' not in result.stdout, result
        # A single file uses the same lifecycle, without authorizing siblings.
        model = folder / 'model.pt'
        model.write_text('file-readable')
        file_readonly = (FolderMount('DATA', str(model), kind='file'),)
        file_writable = (FolderMount('DATA', str(model), ResourceAccess.READ_WRITE, 'file'),)
        # cmd's type enumerates a file's parent, which is deliberately ungranted
        # for single-file resources. Input redirection opens only the file.
        file_read = 'findstr . < "%DATA%"' if runtime == 'windows' else 'cat "$DATA"'
        file_write = 'echo modified>"%DATA%"' if runtime == 'windows' else 'printf modified > "$DATA"'
        sibling_read = f'type "{folder / "marker.txt"}"' if runtime == 'windows' else 'cat "$(dirname "$DATA")/marker.txt"'
        async def file_io(action, mounts):
            if runtime == 'windows':
                return await backend.execute('native-folders', [str(executable)], folder_mounts=mounts,
                    invocation_env={'FOLDER_TEST_ACTION': action})
            return await execute(file_read if action == 'read' else file_write, mounts)
        result = await file_io('read', file_readonly)
        assert result.exit_code == 0 and 'file-readable' in result.stdout, result
        result = await execute(file_read, file_readonly)
        assert result.exit_code == 0 and 'file-readable' in result.stdout, result
        result = await file_io('write', file_readonly)
        assert result.exit_code != 0 and model.read_text() == 'file-readable', result
        result = await execute(sibling_read, file_readonly)
        assert result.exit_code != 0 and 'folder-readable' not in result.stdout, result
        if runtime == 'windows':
            result = await backend.execute('native-folders', [str(executable)], folder_mounts=file_readonly, invocation_env={'FOLDER_TEST_ACTION': 'copy'})
        else:
            result = await execute('cp "$DATA" copied.txt', file_readonly)
        assert result.exit_code == 0, result
        result = await execute('echo changed>>copied.txt' if runtime == 'windows' else 'printf changed >> copied.txt')
        assert result.exit_code == 0 and model.read_text() == 'file-readable', result
        result = await file_io('write', file_writable)
        assert result.exit_code == 0 and model.read_text().strip() == 'modified', result
        result = (await backend.execute('native-folders', [str(executable)], invocation_env={'DATA': str(model), 'FOLDER_TEST_ACTION': 'read'})
                  if runtime == 'windows' else await execute('cat /sandbox/files/DATA/model.pt'))
        assert result.exit_code != 0 and 'modified' not in result.stdout, result
    finally:
        await backend.destroy('native-folders')
    assert (folder / 'marker.txt').read_text() == 'folder-readable'
