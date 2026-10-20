"""Bohrium batch protocol; all CLI access stays in the authorized OAW Sandbox."""
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator
from open_agent_world.plugin_api import BackgroundJobProvider, JobObservation, SubmissionUncertainError
from .compatibility import BOHR_SCRIPT


class BatchSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1, max_length=1024)
    command: str = Field(min_length=1, max_length=16000)
    project_id: str = Field(min_length=1, max_length=128)
    machine_type: str | None = None
    sku_id: str | None = None
    input_path: str | None = Field(default=None, max_length=1024)
    out_files: list[str] = Field(default_factory=list, max_length=50)
    max_run_time: str = Field(min_length=1, max_length=32, description="Explicit provider computation budget, e.g. 72h.")
    max_wait_time: str = Field(default="30m", min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_selectors(self):
        if bool(self.machine_type) == bool(self.sku_id):
            raise ValueError("Choose exactly one machine_type or sku_id")
        return self


def arguments(spec, request_id):
    # A searchable request identity helps reconcile a lost submit response.
    args = ["batchjob", "submit", "--name", f"{spec.name[:80]}-oaw-{request_id}",
        "--image", spec.image, "--command", spec.command, "--project-id", spec.project_id,
        "--max-run-time", spec.max_run_time, "--max-wait-time", spec.max_wait_time]
    args += ["--machine-type", spec.machine_type] if spec.machine_type else ["--sku-id", spec.sku_id]
    if spec.input_path:
        args += ["--input", spec.input_path]
    for path in spec.out_files:
        args += ["--out-file", path]
    return args


async def call(context, args, *, timeout=60):
    # The host binds this provider's bohr profile for submission and later
    # observations. Resolve its private npm package, never a global CLI shim.
    result = await context.execute(["python3", "-c", BOHR_SCRIPT, *args, "-o", "json", "--no-interactive", "-y"], timeout_seconds=timeout)
    if result.get("exit_code") != 0 or result.get("timed_out") or result.get("cancelled"):
        # Do not echo CLI output containing credentials into durable job metadata.
        raise RuntimeError("Bohrium CLI failed; inspect the Sandbox command receipt")
    value = json.loads(result.get("stdout", ""))
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("Bohrium rejected the request; inspect its command receipt")
    value = value.get("data")
    if not isinstance(value, dict):
        raise ValueError("Bohrium returned an invalid JSON object")
    return value


async def prepare(context, spec):
    await call(context, [*arguments(spec, "preflight"), "--dry-run"])


async def submit(context, spec, request_id):
    try:
        value = await call(context, arguments(spec, request_id), timeout=120)
        identity = value.get("jobId")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("Bohrium submission returned no jobId")
        return identity
    except Exception as exc:
        raise SubmissionUncertainError("Inspect bohr batchjob list for the oaw request ID before attaching existing work. Do not resubmit.") from exc


async def inspect(context, external_id):
    value = await call(context, ["batchjob", "describe", external_id])
    raw = str(value.get("status_name") or "").lower()
    status = {"prepared": "queued", "pending": "queued", "active": "running", "running": "running",
        "succeeded": "succeeded", "failed": "failed", "deleted": "cancelled", "killed": "cancelled",
        "stopped": "cancelled", "terminated": "cancelled", "cancelled": "cancelled", "canceled": "cancelled"}.get(raw, "unknown")
    return JobObservation(status=status, detail=f"Bohrium status: {raw[:100] or 'unknown'}. Collect outputs from this existing job before accepting the scientific result.")


async def cancel(context, external_id):
    value = await call(context, ["batchjob", "kill", external_id])
    if value.get("confirmed") is not True:
        raise RuntimeError("Bohrium termination was not confirmed; inspect the existing job")


definition = BackgroundJobProvider(specification=BatchSpecification, submit=submit, prepare=prepare,
    inspect=inspect, cancel=cancel, poll_interval_seconds=60, sandbox_environments=('bohr',))
