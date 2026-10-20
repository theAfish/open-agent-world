"""Controlled Bohrium protocol checks, not real cloud submissions."""
import json

import pytest
from plugins.matcreator.oaw_matcreator.background_jobs import BatchSpecification, prepare, submit, inspect, cancel, definition
from open_agent_world.plugin_api import SubmissionUncertainError


class Context:
    calls = None
    data = None
    def __init__(self):
        self.calls = []
        self.data = {}
    async def execute(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return {"exit_code": 0, "stdout": json.dumps({"ok": True, "data": self.data})}


@pytest.mark.asyncio
async def test_batch_preflight_identity_and_confirmed_cancellation():
    ctx = Context()
    spec = BatchSpecification(name="md", image="explicit-image", command="bash run.sh", project_id="chosen-project",
        machine_type="chosen-machine", input_path="/workspace/md", out_files=["trajectory.extxyz", "monitor.csv"], max_run_time="72h")
    await prepare(ctx, spec)
    ctx.data = {"jobId": "external-123"}
    assert await submit(ctx, spec, "durable-request") == "external-123"
    dry, actual = [c[0] for c in ctx.calls]
    assert definition.sandbox_environments == ('bohr',)
    assert actual[:2] == ['python3', '-c'] and 'NODE_PATH' in actual[2]
    assert "--dry-run" in dry and "--dry-run" not in actual
    assert actual[actual.index("--name") + 1] == "md-oaw-durable-request"
    assert actual[actual.index("--max-run-time") + 1] == "72h" and actual.count("--out-file") == 2
    ctx.data = {"status_name": "succeeded"}
    assert (await inspect(ctx, "external-123")).status == "succeeded"
    ctx.data = {"status_name": "unrecognized"}
    assert (await inspect(ctx, "external-123")).status == "unknown"
    ctx.data = {"confirmed": False}
    with pytest.raises(RuntimeError, match="not confirmed"):
        await cancel(ctx, "external-123")
    ctx.data = {"confirmed": True}
    await cancel(ctx, "external-123")


@pytest.mark.asyncio
async def test_missing_identity_is_uncertain_and_never_retried():
    ctx = Context()
    spec = BatchSpecification(name="md", image="image", command="run", project_id="project", sku_id="sku", max_run_time="24h")
    with pytest.raises(SubmissionUncertainError):
        await submit(ctx, spec, "request")
    assert len(ctx.calls) == 1
