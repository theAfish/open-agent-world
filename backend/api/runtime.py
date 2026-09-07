from __future__ import annotations

from pathlib import PurePath
from dataclasses import fields
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.runs import RunRecord
from backend.sandbox.settings import SandboxSettings, SandboxSettingsStore
from backend.sandbox.manager import SandboxManager
from backend.sandbox.models import SandboxValidationError


router = APIRouter(tags=["runtime"])


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[str, Field(min_length=1, max_length=100_000)]
    task_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    parent_run_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    detached: bool = False

    @model_validator(mode="after")
    def validate_lineage(self) -> "AgentRunRequest":
        if self.detached and self.parent_run_id is not None:
            raise ValueError("a detached Run cannot also specify parent_run_id")
        return self


class LlmSettingsRequest(BaseModel):
    """Persistent connection settings for ADK's LiteLLM model adapter."""

    model_config = ConfigDict(extra="forbid")

    base_url: Annotated[str, Field(max_length=2_048)] = ""
    api_key: Annotated[str, Field(max_length=4_096)] | None = None
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_secret_change(self) -> "LlmSettingsRequest":
        if self.clear_api_key and self.api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self


class LlmSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key_configured: bool


class SandboxExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Annotated[str, Field(min_length=1, max_length=32_768)] | None = None
    argv: Annotated[list[str], Field(min_length=1, max_length=256)] | None = None
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] | None = None
    environment_id: str | None = None
    target_id: str | None = None

    @model_validator(mode="after")
    def validate_command_shape(self) -> "SandboxExecuteRequest":
        if (self.command is None) == (self.argv is None):
            raise ValueError("provide exactly one of command or argv")
        if self.argv is not None and any(not item or "\x00" in item for item in self.argv):
            raise ValueError("argv entries must be non-empty and NUL-free")
        if self.command is not None and "\x00" in self.command:
            raise ValueError("command must be NUL-free")
        return self

@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    info = await services.get_agent(agent_id)
    # Runtime configs use immutable mapping proxies internally. Build the wire
    # representation explicitly instead of asking FastAPI to serialize those.
    config = {field.name: getattr(info.config, field.name) for field in fields(info.config)}
    config["provider_config"] = dict(info.config.provider_config)
    return {
        "config": config,
        "status": info.status,
        "session_id": info.session_id,
        "active_run_id": info.active_run_id,
        "last_error": info.last_error,
        "details": dict(info.details),
    }


@router.post("/agents/{agent_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_agent(
    agent_id: str,
    request: AgentRunRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    if (
        request.task_id is None
        and request.parent_run_id is None
        and not request.detached
    ):
        return await services.run_agent(agent_id, request.prompt)
    run = await services._require_run_manager().start_run(
        agent_id,
        request.prompt,
        task_id=request.task_id,
        parent_run_id=request.parent_run_id,
        detached=request.detached,
    )
    return {"accepted": True, "agent_id": agent_id, "run_id": run.run_id}


@router.get("/runs", response_model=list[RunRecord])
async def list_runs(
    agent_id: str | None = None,
    task_id: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> list[RunRecord]:
    return services._require_run_manager().list_runs(
        agent_id=agent_id, task_id=task_id
    )


@router.get("/runs/{run_id}", response_model=RunRecord)
async def get_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RunRecord:
    return services._require_run_manager().get_run(run_id)


@router.get("/runs/{run_id}/children", response_model=list[RunRecord])
async def list_child_runs(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> list[RunRecord]:
    return services._require_run_manager().list_child_runs(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunRecord)
async def cancel_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RunRecord:
    return await services._require_run_manager().cancel_run(run_id)


@router.post("/agents/{agent_id}/stop")
async def stop_agent(
    agent_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    return await services.stop_agent(agent_id)


@router.get("/settings/llm", response_model=LlmSettingsResponse)
async def get_llm_settings(
    services: ApplicationServices = Depends(get_services),
) -> LlmSettingsResponse:
    settings = services.get_llm_connection_settings()
    return LlmSettingsResponse(
        base_url=settings.base_url,
        api_key_configured=settings.api_key_configured,
    )


@router.put("/settings/llm", response_model=LlmSettingsResponse)
async def configure_llm(
    request: LlmSettingsRequest,
    services: ApplicationServices = Depends(get_services),
) -> LlmSettingsResponse:
    settings = await services.configure_llm_connection(
        base_url=request.base_url.strip() or None,
        api_key=request.api_key,
        clear_api_key=request.clear_api_key,
    )
    return LlmSettingsResponse(
        base_url=settings.base_url,
        api_key_configured=settings.api_key_configured,
    )


@router.get("/sandbox/runtimes")
async def sandbox_runtimes(
    refresh: bool = False,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.sandbox_runtimes(refresh=refresh)


@router.get("/settings/sandbox", response_model=SandboxSettings)
async def get_sandbox_settings(
    services: ApplicationServices = Depends(get_services),
) -> SandboxSettings:
    return SandboxSettingsStore(services.database, services.settings.data_root).read()


@router.put("/settings/sandbox", response_model=SandboxSettings)
async def save_sandbox_settings(
    request: SandboxSettings,
    services: ApplicationServices = Depends(get_services),
) -> SandboxSettings:
    if request.runtime != "auto":
        backend = services.sandbox_backend
        if not isinstance(backend, SandboxManager) or request.runtime not in {
            runtime.id for runtime in await backend.registry.catalog()
        }:
            raise SandboxValidationError("Choose a runtime installed on this backend host")
    return SandboxSettingsStore(services.database, services.settings.data_root).save(request)


@router.get("/sandboxes/{sandbox_id}")
async def get_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return jsonable_encoder(await services.get_sandbox(sandbox_id), custom_encoder={PurePath: str})


@router.post("/sandboxes/{sandbox_id}/start")
async def start_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return jsonable_encoder(await services.start_sandbox(sandbox_id), custom_encoder={PurePath: str})


@router.post("/sandboxes/{sandbox_id}/stop")
async def stop_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return jsonable_encoder(await services.stop_sandbox(sandbox_id), custom_encoder={PurePath: str})


@router.post("/sandboxes/{sandbox_id}/execute")
async def execute_sandbox(
    sandbox_id: str,
    request: SandboxExecuteRequest,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.execute_sandbox(
        sandbox_id,
        request.argv,
        command=request.command,
        timeout_seconds=request.timeout_seconds, environment_id=request.environment_id, target_id=request.target_id,
        _keep_on_disconnect=True,
    )


@router.get("/sandboxes/{sandbox_id}/configuration")
async def sandbox_configuration(sandbox_id: str, services=Depends(get_services)):
    from backend.execution_config import configuration_summary
    async with services._node_mutation(read_only=True):
        services._require_card_type(sandbox_id, "sandbox")
        return configuration_summary(services, sandbox_id)


@router.get("/sandboxes/{sandbox_id}/history")
async def sandbox_history(sandbox_id: str, services=Depends(get_services)):
    from backend.sandbox.history import read
    services._require_card_type(sandbox_id, "sandbox")
    return read(services, sandbox_id)


@router.post("/sandboxes/{sandbox_id}/cancel")
async def sandbox_cancel(sandbox_id: str, services=Depends(get_services)):
    services._require_card_type(sandbox_id, "sandbox")
    await services._require_sandbox_backend().cancel(sandbox_id)
    return {"cancelled": True}


@router.get("/sandboxes/{sandbox_id}/files")
async def sandbox_files(sandbox_id: str, operation: str = "roots", root: str = "workspace", path: str = "", services=Depends(get_services)):
    import base64
    if operation not in {"roots", "list", "preview", "download"}:
        raise SandboxValidationError("Unsupported read operation")
    async with services._node_mutation(read_only=True):
        services._require_card_type(sandbox_id, "sandbox")
        try:
            result = await services._require_sandbox_backend().file_operation(sandbox_id, operation, root=root, path=path)
        except FileNotFoundError:
            return {"state": "missing", "message": "File or directory no longer exists"}
        except FileExistsError:
            return {"state": "conflict", "message": "Destination exists"}
        except (PermissionError, OSError):
            return {"state": "permission_denied", "message": "Access denied or path is not a regular file/directory"}
    if operation == "download" and result.get("state") == "ready":
        return Response(base64.b64decode(result["data"]), media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"})
    return result


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str | None = Field(default=None, max_length=2048)


@router.post("/sandboxes/{sandbox_id}/diagnostics")
async def sandbox_diagnostics(sandbox_id: str, request: DiagnosticRequest, services=Depends(get_services)):
    from backend.sandbox_workspace import diagnostics
    return await diagnostics(services, sandbox_id, request.destination)


@router.get("/sandboxes/{sandbox_id}/skills/{skill_id}")
async def sandbox_skill(sandbox_id: str, skill_id: str, services=Depends(get_services)):
    from backend.sandbox_workspace import require_skill
    from backend.sandbox.materialization import RuntimeBundle
    import base64
    from open_agent_world.skill_packages import SkillAsset
    services._require_card_type(sandbox_id, "sandbox")
    snapshot, skill = require_skill(services, skill_id)
    files = [("SKILL.md", skill.instructions.encode()), *[(path, base64.b64decode(value.data_base64) if isinstance(value, SkillAsset) else value.encode()) for path, value in skill.files.items()]]
    bundle = RuntimeBundle(f"skills/{skill_id}", tuple(files), tuple(skill.directories))
    cache = await services._require_sandbox_backend().bundle_status(sandbox_id, bundle)
    current = services._sandbox_commands.get(sandbox_id)
    info = await services.get_sandbox(sandbox_id)
    active = bool(current and current.get("skill_id") == skill_id and info.state.value == "running")
    return {**cache, "revision": snapshot["revision"], "bundle_key": bundle.key, "files": [path for path, _ in files],
        "active_execution": active,
        "status": "active execution" if active else "bundle available",
        "note": "Host-managed, read-only, command-scoped. Cached bundles grant no access; runtime readiness requires diagnostics."}


class SkillCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    source: str
    destination: str
    overwrite: bool = False


@router.post("/sandboxes/{sandbox_id}/copy-skill-resource")
async def sandbox_copy_skill(sandbox_id: str, request: SkillCopyRequest, services=Depends(get_services)):
    from backend.sandbox_workspace import copy_skill
    try:
        return await copy_skill(services, sandbox_id, **request.model_dump())
    except FileExistsError:
        raise SandboxValidationError("Destination exists; explicitly enable overwrite") from None
    except OSError:
        raise SandboxValidationError("Destination is missing or not writable") from None


@router.post("/sandboxes/{sandbox_id}/reset-cache")
async def sandbox_reset_cache(sandbox_id: str, services=Depends(get_services)):
    async with services._node_mutation():
        services._require_card_type(sandbox_id, "sandbox")
        await services._require_sandbox_backend().reset_cache(sandbox_id)
    return {"reset": True, "note": "Runtime cache cleared. Workspace files and outputs preserved."}
