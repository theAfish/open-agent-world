from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.dependencies import get_services
from backend.services import ApplicationServices


router = APIRouter(tags=["runtime"])


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[str, Field(min_length=1, max_length=100_000)]


class LlmSettingsRequest(BaseModel):
    """Runtime-only connection overrides; never returned by the API."""

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

    def execution_argv(self) -> list[str]:
        if self.argv is not None:
            return self.argv
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        command_prompt = windows / "System32" / "cmd.exe"
        return [
            str(command_prompt),
            "/d",
            "/s",
            "/c",
            self.command or "",
        ]


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
    return await services.run_agent(agent_id, request.prompt)


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


@router.get("/sandboxes/{sandbox_id}")
async def get_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.get_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/start")
async def start_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.start_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/stop")
async def stop_sandbox(
    sandbox_id: str,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.stop_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/execute")
async def execute_sandbox(
    sandbox_id: str,
    request: SandboxExecuteRequest,
    services: ApplicationServices = Depends(get_services),
) -> object:
    return await services.execute_sandbox(
        sandbox_id,
        request.execution_argv(),
        timeout_seconds=request.timeout_seconds,
    )
