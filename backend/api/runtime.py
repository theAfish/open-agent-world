from __future__ import annotations

from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, status
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
    """Runtime-only connection overrides for ADK's LiteLLM model adapter."""

    model_config = ConfigDict(extra="forbid")

    base_url: Annotated[str, Field(max_length=2_048)] = ""
    api_key: Annotated[str, Field(max_length=4_096)] = ""


class SandboxExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Annotated[str, Field(min_length=1, max_length=32_768)] | None = None
    argv: Annotated[list[str], Field(min_length=1, max_length=256)] | None = None
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] | None = None

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
    return await services.get_agent(agent_id)


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


@router.put("/settings/llm")
async def configure_llm(
    request: LlmSettingsRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, bool]:
    return await services.configure_llm_connection(
        base_url=request.base_url.strip() or None,
        api_key=request.api_key or None,
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
        timeout_seconds=request.timeout_seconds,
    )
