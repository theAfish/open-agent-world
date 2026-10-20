"""Provider-neutral contracts for durable external work.

The host owns identities, admission, observation, permissions and notification
delivery. Providers own their remote protocol. No provider may automatically
resubmit an uncertain submission. Specifications must reference credentials
through the authorized Sandbox, never contain credential values.
"""
from dataclasses import dataclass
import re
from typing import Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class JobObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted", "unknown"]
    detail: str = Field(default="", max_length=2000)


class BackgroundJobContext(Protocol):
    agent_id: str
    sandbox_id: str

    async def execute(self, argv: list[str], *, timeout_seconds: float = 60) -> dict: ...
    async def inspect_operation(self, operation_id: str) -> dict: ...


class SubmissionUncertainError(RuntimeError):
    """A side effect may have occurred; inspecting existing work is required."""


@dataclass(frozen=True)
class BackgroundJobProvider:
    specification: type[BaseModel]
    inspect: Callable[[BackgroundJobContext, str], Awaitable[JobObservation]]
    submit: Callable[[BackgroundJobContext, BaseModel, str], Awaitable[str]] | None = None
    cancel: Callable[[BackgroundJobContext, str], Awaitable[None]] | None = None
    prepare: Callable[[BackgroundJobContext, BaseModel], Awaitable[None]] | None = None
    poll_interval_seconds: float = 60
    sandbox_environments: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.sandbox_environments, tuple) or len(self.sandbox_environments) > 20
                or any(not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*', value)
                       for value in self.sandbox_environments)
                or len(set(self.sandbox_environments)) != len(self.sandbox_environments)):
            raise ValueError('Background job Sandbox environments must be at most 20 unique identifiers')


class JobSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    sandbox_id: str = Field(min_length=1)
    request_key: str = Field(min_length=1, max_length=256)
    specification: dict = Field(default_factory=dict)


class JobAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    sandbox_id: str = Field(min_length=1)
    request_key: str = Field(min_length=1, max_length=256)
    external_id: str = Field(min_length=1, max_length=512)
    job_id: str | None = None


class JobInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[str] = Field(default_factory=list, max_length=50)


class JobWait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[str] = Field(min_length=1, max_length=50)


class JobControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    action: Literal["pause", "resume", "cancel", "acknowledge", "detach"]
