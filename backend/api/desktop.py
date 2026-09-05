from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import folder_picker
from backend.errors import PermissionDeniedError

router = APIRouter(tags=["desktop"])


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


@router.post("/desktop/pick-folder")
async def pick_folder(body: FolderPickerRequest, request: Request) -> dict[str, str | None]:
    origin = request.headers.get("origin")
    if (request.client is None or not _local_host(request.client.host)
        or (origin is not None and not _local_host(urlsplit(origin).hostname))):
        raise PermissionDeniedError("Browse is available on the backend computer. For a remote server, enter its folder path manually.")
    return {"path": await folder_picker.pick_folder(body.initial_path)}
