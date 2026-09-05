import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from backend import folder_picker
from backend.config import Settings
from backend.errors import ConflictError, RuntimeUnavailableError
from backend.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_picker_transports_paths_as_json_and_preserves_cancel(tmp_path, monkeypatch, cancelled):
    folder = tmp_path / "workspace 中文 ' $()"
    folder.mkdir()
    selected = None if cancelled else str(folder)
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(json.dumps(selected).encode(), b""))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(folder_picker.asyncio, "create_subprocess_exec", launch)
    assert await folder_picker.pick_folder(str(folder)) == selected
    assert json.loads(process.communicate.call_args.args[0]) == {"initial_path": str(folder)}
    assert str(folder) not in launch.call_args.args
    assert list(folder.iterdir()) == []


@pytest.mark.asyncio
async def test_timeout_kills_dialog_and_releases_single_dialog_lock(monkeypatch):
    process = Mock(returncode=None)
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock()
    monkeypatch.setattr(folder_picker.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeUnavailableError, match="timed out"):
        await folder_picker.pick_folder(None)
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
    assert not folder_picker._dialog_lock.locked()


@pytest.mark.asyncio
async def test_duplicate_picker_does_not_launch_another_window(monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(folder_picker.asyncio, "create_subprocess_exec", launch)
    folder_picker._dialog_lock.acquire()
    try:
        with pytest.raises(ConflictError):
            await folder_picker.pick_folder(None)
    finally:
        folder_picker._dialog_lock.release()
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_desktop_has_manual_path_fallback(monkeypatch):
    process = Mock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", b"no desktop"))
    monkeypatch.setattr(folder_picker.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeUnavailableError, match="manually"):
        await folder_picker.pick_folder(None)


@pytest.mark.parametrize("origin,client_host,expected", [
    ("http://localhost:5173", "127.0.0.1", 200),
    ("http://127.0.0.1:5173", "127.0.0.1", 200),
    ("http://[::1]:5173", "::1", 200),
    ("https://example.com", "127.0.0.1", 403),
    ("http://192.168.1.2:5173", "127.0.0.1", 403),
    ("http://localhost:5173", "192.168.1.2", 403),
])
def test_only_local_desktop_requests_can_open_picker(tmp_path, monkeypatch, origin, client_host, expected):
    pick = AsyncMock(return_value=None)
    monkeypatch.setattr(folder_picker, "pick_folder", pick)
    client = TestClient(create_app(Settings.for_data_root(tmp_path)), client=(client_host, 1234))
    response = client.post("/api/desktop/pick-folder", json={"initial_path": None}, headers={"Origin": origin})
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {"path": None}
        pick.assert_awaited_once_with(None)
    else:
        pick.assert_not_called()
