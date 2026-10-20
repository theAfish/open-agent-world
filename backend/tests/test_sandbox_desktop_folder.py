from unittest.mock import AsyncMock

import pytest

from backend.sandbox.files import file_operation
from backend.sandbox.models import SandboxSecurityError, SandboxValidationError
from backend.sandbox.wsl import WslSandboxBackend


def test_desktop_location_validates_files_folders_and_authority(tmp_path):
    folder = tmp_path / 'nested'
    folder.mkdir()
    (folder / 'file.txt').write_text('content')
    for path in ('nested', 'nested/file.txt'):
        assert file_operation(tmp_path, 'read_only', (), 'desktop_location', path=path) == {'path': str(folder)}
    assert file_operation(tmp_path, 'read_only', (), 'desktop_location') == {'path': str(tmp_path)}
    with pytest.raises(SandboxValidationError):
        file_operation(tmp_path, 'read_write', (), 'desktop_location', path='../outside')
    with pytest.raises(SandboxSecurityError):
        file_operation(tmp_path, 'read_write', (), 'desktop_location', root='resource:unknown')
    with pytest.raises(FileNotFoundError):
        file_operation(tmp_path, 'read_write', (), 'desktop_location', path='missing')


def test_desktop_location_rejects_hardlinks(tmp_path):
    import os
    original = tmp_path / 'original'
    original.write_text('content')
    os.link(original, tmp_path / 'linked')
    with pytest.raises(SandboxSecurityError):
        file_operation(tmp_path, 'read_only', (), 'desktop_location', path='linked')


def test_wsl_desktop_location_maps_validated_worker_path(tmp_path):
    import asyncio
    backend = WslSandboxBackend(tmp_path, distribution='Ubuntu')
    backend._request = AsyncMock(return_value={'path': '/mnt/c/work/nested'})
    result = asyncio.run(backend.file_operation('sandbox', 'desktop_location', root='resource:attached', path=''))
    assert result == {'path': r'\\wsl.localhost\Ubuntu\mnt\c\work\nested'}


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows host workspace mapping')
@pytest.mark.parametrize('external', [True, False])
def test_wsl_desktop_uses_authoritative_windows_workspace(tmp_path, external):
    import asyncio
    backend = WslSandboxBackend(tmp_path, distribution='Ubuntu')
    workspace = tmp_path / 'external' if external else backend._managed_workspace_path('sandbox')
    folder = workspace / 'nested'
    folder.mkdir(parents=True)
    (folder / 'file.txt').write_text('content')
    backend._request = AsyncMock(return_value={
        'workspace_path': str(workspace) if external else None, 'workspace_access': 'read_only'})
    for path in ('nested', 'nested/file.txt'):
        result = asyncio.run(backend.file_operation('sandbox', 'desktop_location', root='workspace', path=path))
        assert result == {'path': str(folder)}


def test_open_folder_endpoint_checks_local_access_and_uses_backend_authority(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from starlette.requests import Request
    from backend.api.runtime import open_sandbox_folder, OpenSandboxFolderRequest
    from backend.errors import PermissionDeniedError

    @asynccontextmanager
    async def mutation(**kwargs):
        yield

    backend = SimpleNamespace(file_operation=AsyncMock(return_value={'path': '/authorized/folder'}))
    services = SimpleNamespace(_node_mutation=mutation, _require_card_type=Mock(),
                               _require_sandbox_backend=lambda: backend)
    opened = Mock()
    monkeypatch.setattr('backend.desktop_folders.open_folder', opened)
    body = OpenSandboxFolderRequest(path='nested/file.txt')
    remote = Request({'type': 'http', 'client': ('192.0.2.1', 123), 'headers': []})
    with pytest.raises(PermissionDeniedError):
        asyncio.run(open_sandbox_folder('sandbox', body, remote, services))
    backend.file_operation.assert_not_called()
    local = Request({'type': 'http', 'client': ('127.0.0.1', 123), 'headers': []})
    assert asyncio.run(open_sandbox_folder('sandbox', body, local, services)) == {'opened': True}
    backend.file_operation.assert_awaited_once_with('sandbox', 'desktop_location', root='workspace', path='nested/file.txt')
    opened.assert_called_once_with('/authorized/folder')
    opened.side_effect = PermissionError('denied')
    with pytest.raises(SandboxValidationError, match='system denied access'):
        asyncio.run(open_sandbox_folder('sandbox', body, local, services))
