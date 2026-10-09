from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import folder_picker
from backend.errors import PermissionDeniedError
from backend.api.dependencies import get_services

router = APIRouter(tags=["desktop"])


class PathInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


@router.post("/desktop/inspect-path")
async def inspect_path(body: PathInspectionRequest, services=Depends(get_services)):
    """Read-only host UI preview; saving always validates the path again."""
    import asyncio
    from backend.sandbox.settings import SandboxSettingsStore
    from backend.security.execution_folders import inspect_environment_path
    validator = SandboxSettingsStore(services.database, services.settings.data_root).validator
    path, kind = await asyncio.to_thread(inspect_environment_path, body.path, validator)
    return {"path": path, "kind": kind}


def _local_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host or "").is_loopback
    except ValueError:
        return False


class FolderPickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_path: str | None = Field(default=None, max_length=4096)

    @field_validator("initial_path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("folder path must not contain NUL")
        return value


def _require_local_desktop(request: Request) -> None:
    origin = request.headers.get("origin")
    if (request.client is None or not _local_host(request.client.host)
        or (origin is not None and not _local_host(urlsplit(origin).hostname))):
        raise PermissionDeniedError("Browse is available on the backend computer. For a remote server, enter its path manually.")


@router.post("/desktop/pick-folder")
async def pick_folder(body: FolderPickerRequest, request: Request) -> dict[str, str | None]:
    _require_local_desktop(request)
    return {"path": await folder_picker.pick_folder(body.initial_path)}


@router.post("/desktop/pick-file")
async def pick_file(body: FolderPickerRequest, request: Request) -> dict[str, str | None]:
    _require_local_desktop(request)
    return {"path": await folder_picker.pick_file(body.initial_path)}
