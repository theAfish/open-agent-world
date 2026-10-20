import json
import shlex
from types import SimpleNamespace

import pytest

from backend.cloud.aliyun.python_environment import SETUP_SCRIPT
from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.sandbox.models import SandboxPreparationError, SandboxStateError, SandboxValidationError
from backend.tests.test_aliyun_sandbox import FakeSDK


class PythonSDK(FakeSDK):
    def __init__(self, root):
        super().__init__(root)
        self.setups = []
        self.setup_failure = None

    async def create(self, **kwargs):
        sandbox = await super().create(**kwargs)
        original = sandbox.run
        async def run(command, **options):
            argv = shlex.split(command)
            if len(argv) > 3 and argv[3] == SETUP_SCRIPT:
                self.setups.append((json.loads(argv[4]), options))
                async def wait():
                    if self.setup_failure == "unknown":
                        raise ConnectionError("lost setup result")
                    return SimpleNamespace(exit_code=1 if self.setup_failure else 0,
                        stdout=json.dumps({"python_version": "3.13.0", "package_versions": {"numpy": "2.4.0", "scipy": "1.18.0"}}),
                        stderr="setup failed" if self.setup_failure else "")
                return SimpleNamespace(pid=77, wait=wait)
            return await original(command, **options)
        sandbox.run = run
        return sandbox


@pytest.fixture
def cloud(tmp_path):
    sdk = PythonSDK(tmp_path)
    settings = AliyunSandboxSettings("cn-hangzhou", "test-secret", "base", "volume", "profile", python_requirements=("numpy",))
    return AliyunSandboxBackend(tmp_path / "profile", settings, sdk=sdk), sdk


@pytest.mark.asyncio
async def test_python_recipe_is_rebuilt_in_next_vm_and_command_uses_private_environment(cloud):
    backend, sdk = cloud
    await backend.create("s")
    policy = {"network_enabled": True}
    await backend.start_with_policy("s", execution_policy=policy)
    status = await backend.prepare_python(["scipy"])
    assert status["ready"] and status["requirements"] == ["numpy", "scipy"]
    await backend.execute("s", ["python3", "-c", "pass"], execution_policy=policy)
    assert len(sdk.setups) == 1 and sdk.setups[0][1]["user"] == "root"
    assert sdk.commands[0][1]["user"] == "user"
    assert sdk.commands[0][1]["envs"]["PATH"].startswith("/opt/oaw-python/bin:")
    effective = shlex.split(sdk.commands[0][0])
    assert effective[0] == "/usr/bin/env" and effective[1].startswith("PATH=/opt/oaw-python/bin:")
    assert effective[4:] == ["python3", "-c", "pass"]
    assert not (await backend.python_status())["ready"]
    restored = AliyunSandboxBackend(backend.path.parent, backend.settings, sdk=sdk)
    await restored.execute("s", ["sh", "-c", "python3 script.py"], execution_policy=policy)
    assert len(sdk.setups) == 2 and sdk.setups[1][0] == ["numpy", "scipy"]
    assert len(sdk.killed) == 2


@pytest.mark.asyncio
async def test_python_setup_unknown_result_is_never_replayed(cloud):
    backend, sdk = cloud
    await backend.create("s")
    sdk.setup_failure = "unknown"
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    receipt = backend.path.read_bytes()
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    assert backend.path.read_bytes() == receipt and len(sdk.setups) == 1 and not sdk.commands
    await backend.terminate("s")
    assert sdk.killed == ["remote-1"] and not backend._read("s")["python_setup"]


@pytest.mark.asyncio
async def test_offline_invalid_or_failed_python_preparation_never_admits_user_command(cloud):
    backend, sdk = cloud
    await backend.create("s")
    with pytest.raises(SandboxValidationError):
        await backend.prepare_python(["https://invalid.example/package.whl"])
    assert not sdk.created
    with pytest.raises(SandboxPreparationError, match="networking enabled"):
        await backend.execute("s", ["business-command"])
    assert not sdk.setups and not sdk.commands
    await backend.terminate("s")
    sdk.setup_failure = "failed"
    with pytest.raises(SandboxPreparationError, match="installation failed"):
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    assert not sdk.commands


@pytest.mark.asyncio
async def test_failed_extra_package_does_not_poison_saved_recipe_or_reuse_old_verification(cloud):
    backend, sdk = cloud
    await backend.create("s")
    await backend.start_with_policy("s", execution_policy={"network_enabled": True})
    await backend.prepare_python(["scipy"])
    sdk.setup_failure = "failed"
    with pytest.raises(SandboxPreparationError):
        await backend.prepare_python(["missing-package"])
    status = await backend.python_status()
    assert status["requirements"] == ["numpy", "scipy"] and not status["ready"]
    sdk.setup_failure = None
    await backend.prepare_python()
    assert (await backend.python_status())["ready"] and sdk.setups[-1][0] == ["numpy", "scipy"]
