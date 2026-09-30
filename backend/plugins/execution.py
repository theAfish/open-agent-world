"""Pure, provider-neutral work-source contracts for trusted plugins.

Plugins decide readiness and acceptance. The host owns dispatch and attempts.
Neither a DAG nor a particular document shape is required by these contracts.
"""
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_parallel: int = Field(default=1, ge=1, le=8)
    pause_on_failure: bool = True


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=64000)
    agent_id: str | None = None
    ready: bool = False
    retryable: bool = False
    completed: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class ExecutionReport(BaseModel):
    """An executor's claims, retained separately from the coordinator's acceptance."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    outcome: Literal["complete", "partial", "blocked", "waiting"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(default="", max_length=4000)
    outputs: list[str] = Field(default_factory=list, max_length=40)
    external_jobs: list[str] = Field(default_factory=list, max_length=20)
    next_step: str = Field(default="", max_length=2000)
    check_after_seconds: float | None = Field(default=None, ge=1, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_report(self):
        if any(not p.strip() or len(p) > 1024 for p in [*self.outputs, *self.external_jobs]):
            raise ValueError("Output paths and external job references must be non-empty and at most 1024 characters")
        if self.outcome == "complete" and not self.evidence:
            raise ValueError("A complete report requires verification evidence")
        if self.outcome != "complete" and not self.next_step:
            raise ValueError("An incomplete report requires a concrete next_step")
        if self.outcome == "waiting" and (not self.external_jobs or self.check_after_seconds is None):
            raise ValueError("Waiting requires external_jobs and check_after_seconds")
        if self.outcome != "waiting" and self.check_after_seconds is not None:
            raise ValueError("Only waiting reports can schedule a check")
        return self


class WorkOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    item_id: str
    run_id: str | None = None
    status: Literal["running", "succeeded", "failed", "cancelled", "interrupted"]
    text: str = ""
    error: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    report: ExecutionReport | None = None


class DelegationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=256)
    library_id: str | None = Field(default=None, min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class DelegationDefer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)
    delay_seconds: float = Field(ge=1, le=86400, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)
    external_jobs: list[str] = Field(default_factory=list, max_length=20)


class DelegationWait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_ids: list[str] = Field(min_length=1, max_length=32)
    wait_mode: Literal["any", "all"] = "all"
    timeout_seconds: float = Field(default=30, ge=0, le=60)


class DelegationStop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class NodeExecutionDefinition:
    items: Callable[[dict[str, Any]], list[WorkItem]]
    apply_outcome: Callable[[dict[str, Any], WorkOutcome], dict[str, Any]]
    policy: Callable[[dict[str, Any]], ExecutionPolicy]
    # Source node -> executor Agent; separate from Agent -> document access.
    executor_relationship: str
    control_capability_kind: str | None = None
    # Agent-directed delegation through an independently authorized Summoning capability.
    # Uses the same work contracts/attempt ledger, without starting a detached DAG batch.
    summoning: bool = False
