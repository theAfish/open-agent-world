import asyncio
from dataclasses import replace
import inspect
import json
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("e2b")
from e2b import AsyncSandbox

from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.sandbox.materialization import RuntimeBundle, RuntimeMount
from backend.sandbox.models import SandboxSecurityError, SandboxState, SandboxStateError, SandboxValidationError


class FakeSDK:
    """No shared host paths in the adapter; test transport models provider RPCs."""
    def __init__(self, root):
        self.root = root
        self.instances, self.created, self.commands, self.killed = {}, [], [], []
        self.fail_create = False
        self.lose_create_response = False
        self.fail_wait = False
        self.hang = False
        self.running = asyncio.Event()

    async def create(self, **kwargs):
        inspect.signature(AsyncSandbox.create).bind(**kwargs)
        self.created.append(kwargs)
        if self.fail_create:
            raise TimeoutError("provider create timed out")
        remote_id = "remote-" + str(len(self.created))
        sandbox = FakeSandbox(self, remote_id, kwargs)
        self.instances[remote_id] = sandbox
        if self.lose_create_response:
            raise TimeoutError("response was lost after create")
        return sandbox

    async def connect(self, remote_id, **kwargs):
        if remote_id not in self.instances:
            from e2b import NotFoundException
            raise NotFoundException("Sandbox expired")
        return self.instances[remote_id]

    async def kill(self, remote_id, **kwargs):
        self.killed.append(remote_id)
        self.instances.pop(remote_id, None)
        return True

    def list(self, query, **kwargs):
        sdk = self
        class Pages:
            has_next = True
            async def next_items(self):
                self.has_next = False
                return [SimpleNamespace(sandbox_id=s.sandbox_id) for s in sdk.instances.values()
                        if all(s.metadata.get(k) == v for k, v in query.metadata.items())]
        return Pages()


class FakeSandbox:
    def __init__(self, sdk, remote_id, kwargs):
        self.sdk, self.sandbox_id, self.metadata = sdk, remote_id, kwargs["metadata"]
        self.commands = self
        self.payload = None

    async def get_info(self):
        return SimpleNamespace(metadata=self.metadata)

    async def set_timeout(self, seconds):
        self.timeout = seconds

    async def run(self, command, **kwargs):
        self.payload = None
        self.sdk.commands.append((command, kwargs))
        sandbox = self
        class Handle:
            pid = 123
            async def wait(self):
                sandbox.sdk.running.set()
                if sandbox.sdk.fail_wait:
                    raise ConnectionError("lost command response")
                if sandbox.sdk.hang:
                    await asyncio.Event().wait()
                if sandbox.payload is not None:
                    # Execute the exact shipped protocol in an isolated Python
                    # interpreter for compatibility testing, NOT OS isolation.
                    args = shlex.split(command)
                    worker = args[3].replace('Path("/workspace")', f"Path({str(sandbox.sdk.root)!r})")
                    process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-c", worker, args[4],
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    out, err = await process.communicate(sandbox.payload.encode())
                    assert process.returncode == 0, err.decode()
                    return SimpleNamespace(exit_code=0, stdout=out.decode(), stderr="")
                callback = kwargs.get("on_stdout")
                if callback:
                    await callback("hello from remote\n")
                return SimpleNamespace(exit_code=0, stdout="hello from remote\n", stderr="")
        return Handle()

    async def send_stdin(self, pid, payload, **kwargs):
        self.payload = payload


@pytest.fixture
def remote(tmp_path):
    workspace = tmp_path / "provider-files"
    workspace.mkdir()
    settings = AliyunSandboxSettings("cn-hangzhou", "private-control-key", "template", "alice-volume", "alice-workspace")
    sdk = FakeSDK(workspace)
    events = []
    async def emit(event):
        events.append(event)
    backend = AliyunSandboxBackend(tmp_path / "profile", settings, emit, sdk=sdk)
    return backend, sdk, events


@pytest.mark.asyncio
async def test_commands_use_managed_isolation_and_never_send_control_credentials(remote, monkeypatch):
    backend, sdk, events = remote
    monkeypatch.setenv("OAW_ALIYUN_SANDBOX_API_KEY", "host-secret-must-not-leak")
    await backend.create("sandbox-a")
    assert not sdk.created  # Card creation and status reads are not cloud creates.
    await backend.start("sandbox-a")
    result = await backend.execute("sandbox-a", ["python3", "-c", "print('hello')"], invocation_env={"EXAMPLE": "value"})
    assert result.exit_code == 0 and result.stdout == "hello from remote\n"
    assert shlex.split(sdk.commands[0][0]) == ["python3", "-c", "print('hello')"]
    assert sdk.commands[0][1]["envs"] == {"EXAMPLE": "value"}
    assert sdk.created[0]["volume_mounts"] == {"/workspace": "alice-volume"}
    assert sdk.created[0]["allow_internet_access"] is False
    assert sdk.created[0]["network"]["allow_public_traffic"] is False
    assert sdk.killed == ["remote-1"] and events[0].payload["text"] == result.stdout
    assert (await backend.get("sandbox-a")).state == SandboxState.STOPPED
    assert "private-control-key" not in backend.path.read_text()


@pytest.mark.asyncio
async def test_restart_connects_same_binding_and_rejects_other_profile(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    await backend.start("s")
    restored = AliyunSandboxBackend(backend.path.parent, backend.settings, sdk=sdk)
    await restored.start("s")
    assert len(sdk.created) == 1
    wrong = AliyunSandboxBackend(backend.path.parent, replace(backend.settings, profile_id="bob"), sdk=sdk)
    with pytest.raises(SandboxSecurityError):
        await wrong.start("s")
    sdk.instances["remote-1"].metadata["oaw.profile"] = "bob"
    with pytest.raises(SandboxSecurityError):
        await restored.start("s")


@pytest.mark.asyncio
async def test_unknown_creation_never_blindly_retries(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    sdk.fail_create = True
    with pytest.raises(SandboxStateError):
        await backend.start("s")
    with pytest.raises(SandboxStateError, match="unknown"):
        await backend.start("s")
    assert len(sdk.created) == 1
    assert json.loads(backend.path.read_text())["request_key"]


@pytest.mark.asyncio
async def test_rejected_credentials_do_not_leave_an_uncertain_creation(remote, monkeypatch):
    from e2b import AuthenticationException
    backend, sdk, _ = remote
    await backend.create("s")
    create = sdk.create

    async def rejected(**kwargs):
        raise AuthenticationException("api key has expired: private-control-key")

    monkeypatch.setattr(sdk, "create", rejected)
    with pytest.raises(SandboxStateError, match="hosting operator must renew") as error:
        await backend.start("s")
    assert "private-control-key" not in str(error.value)
    value = backend._read("s")
    assert value["state"] == "stopped" and value["remote_id"] is None and value["command"] is None
    assert value["create_failure"] == "authentication"
    assert not sdk.commands
    # Stop remains usable without cloud reconciliation; fixing credentials
    # permits the next explicit start, with no automatic retry of the request.
    await backend.terminate("s")
    monkeypatch.setattr(sdk, "create", create)
    await backend.start("s")
    assert len(sdk.created) == 1 and "create_failure" not in backend._read("s")


@pytest.mark.asyncio
async def test_lost_create_response_reconciles_by_metadata(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    sdk.lose_create_response = True
    with pytest.raises(SandboxStateError):
        await backend.start("s")
    await backend.start("s")
    assert len(sdk.created) == 1
    assert json.loads(backend.path.read_text())["remote_id"] == "remote-1"


@pytest.mark.asyncio
async def test_uncertain_command_is_not_replayed_after_restart(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    sdk.fail_wait = True
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("s", ["do-once"])
    record = json.loads(backend.path.read_text())
    assert record["command"]["pid"] == 123 and record["remote_id"] == "remote-1"
    restarted = AliyunSandboxBackend(backend.path.parent, backend.settings, sdk=sdk)
    assert (await restarted.get("s")).state == SandboxState.ERROR
    with pytest.raises(SandboxStateError, match="uncertain"):
        await restarted.execute("s", ["do-once"])
    assert len(sdk.commands) == 1
    await restarted.terminate("s")
    assert sdk.killed == ["remote-1"]


@pytest.mark.asyncio
async def test_cancel_joins_vm_cleanup_and_disallows_concurrent_admission(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    sdk.hang = True
    task = asyncio.create_task(backend.execute("s", ["long-command"]))
    await asyncio.wait_for(sdk.running.wait(), 3)
    with pytest.raises(SandboxStateError, match="one remote"):
        await backend.execute("s", ["another-command"])
    await backend.cancel("s")
    assert task.cancelled() and sdk.killed == ["remote-1"]
    assert not json.loads(backend.path.read_text())["command"]


@pytest.mark.asyncio
async def test_unsupported_capabilities_fail_before_cloud_side_effect(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    with pytest.raises(SandboxValidationError, match="host path"):
        await backend.managed_workspace("s")
    with pytest.raises(SandboxValidationError, match="runtime bundles"):
        await backend.execute("s", ["python3", "run.py"],
            runtime_mount=RuntimeMount(RuntimeBundle("test", (("run.py", b"pass"),)), 1))
    with pytest.raises(SandboxValidationError):
        await backend.attach_resource("s", "secret", Path("host-private"), "copy", "read_only")
    assert not sdk.created and not sdk.commands


@pytest.mark.asyncio
async def test_remote_files_keep_existing_card_protocol_without_host_mounts(remote):
    import base64
    backend, sdk, _ = remote
    await backend.create("s")
    answer = await backend.file_operation("s", "write", path="sample.txt", data=base64.b64encode(b"test data").decode())
    assert answer["written"] == 9
    assert (await backend.file_operation("s", "preview", path="sample.txt"))["text"] == "test data"
    listing = await backend.file_operation("s", "list")
    assert listing["entries"][0]["name"] == "sample.txt"
    with pytest.raises(SandboxValidationError):
        await backend.file_operation("s", "preview", path="../host-private")
    await backend.terminate("s")
    assert (await backend.file_operation("s", "preview", path="sample.txt"))["text"] == "test data"
    assert len(sdk.created) == 2  # Same volume, new execution VM.


@pytest.mark.asyncio
async def test_confirmed_idle_expiry_can_recreate_but_not_repeat_a_command(remote):
    backend, sdk, _ = remote
    await backend.create("s")
    await backend.start("s")
    sdk.instances.clear()
    await backend.start("s")
    assert len(sdk.created) == 2 and not sdk.commands
    assert sdk.created[0]["volume_mounts"] == sdk.created[1]["volume_mounts"]


@pytest.mark.asyncio
async def test_failed_cleanup_retains_remote_receipt(remote, monkeypatch):
    backend, sdk, _ = remote
    await backend.create("s")
    await backend.start("s")
    async def failed(*args, **kwargs):
        raise ConnectionError("private SDK details must not escape")
    monkeypatch.setattr(sdk, "kill", failed)
    with pytest.raises(SandboxStateError, match="cleanup could not be confirmed"):
        await backend.terminate("s")
    receipt = json.loads(backend.path.read_text())
    assert receipt["state"] == "uncertain" and receipt["remote_id"] == "remote-1"
    with pytest.raises(SandboxStateError, match="cleanup outcome is uncertain"):
        await backend.start("s")
    assert len(sdk.created) == 1 and not sdk.commands


@pytest.mark.asyncio
async def test_network_policy_applies_at_creation_and_remains_private(remote):
    backend, sdk, _ = remote
    await backend.create("network")
    policy = {"network_enabled": True, "command_timeout": 60}
    await backend.start_with_policy("network", execution_policy=policy)
    request = sdk.created[0]
    assert request["allow_internet_access"] is True and request["secure"] is True
    assert request["network"]["allow_public_traffic"] is False
    assert "169.254.0.0/16" in request["network"]["deny_out"]
    assert "100.64.0.0/10" in request["network"]["deny_out"]
    assert "allow_out" not in request["network"]
    assert (await backend.get("network")).network_enabled
    await backend.execute("network", ["python3", "-c", "pass"], execution_policy=policy)
    assert len(sdk.created) == 1 and sdk.killed == ["remote-1"]
    await backend.start_with_policy("network", execution_policy={"network_enabled": False})
    assert sdk.created[-1]["allow_internet_access"] is False


@pytest.mark.asyncio
async def test_network_change_cannot_replay_uncertain_command_or_ignore_limits(remote):
    backend, sdk, _ = remote
    await backend.create("network")
    for policy in ({"network_enabled": "true"}, {"memory_bytes": 123}, {"unknown": True}):
        with pytest.raises(SandboxValidationError):
            await backend.start_with_policy("network", execution_policy=policy)
    assert not sdk.created
    sdk.fail_wait = True
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("network", ["once"], execution_policy={"network_enabled": True})
    receipt = backend.path.read_bytes()
    with pytest.raises(SandboxStateError, match="reconcile"):
        await backend.execute("network", ["once"], execution_policy={"network_enabled": False})
    assert backend.path.read_bytes() == receipt and len(sdk.commands) == 1


@pytest.mark.asyncio
async def test_networked_command_after_volume_file_read_replaces_only_idle_vm(remote):
    backend, sdk, _ = remote
    await backend.create("network")
    await backend.file_operation("network", "list")
    assert sdk.created[0]["allow_internet_access"] is False
    await backend.execute("network", ["echo", "online"], execution_policy={"network_enabled": True})
    assert sdk.created[1]["allow_internet_access"] is True
    assert sdk.killed == ["remote-1", "remote-2"]
