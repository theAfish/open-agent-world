import json
import shlex
from dataclasses import replace
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
        self.renewal_failures = 0
        self.wrong_renewal_identity = False

    async def create(self, **kwargs):
        sandbox = await super().create(**kwargs)
        async def renew(seconds):
            if self.setups and self.renewal_failures:
                self.renewal_failures -= 1
                if self.wrong_renewal_identity:
                    sandbox.metadata = {**sandbox.metadata, "oaw.profile": "another-profile"}
                raise TimeoutError("renewal response lost")
        sandbox.set_timeout = renew
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
@pytest.mark.parametrize(("lifetime", "budget"), [(299, None), (300, 240), (600, 480)])
async def test_python_preparation_budget_respects_vm_lifetime(cloud, lifetime, budget):
    backend, sdk = cloud
    backend.settings = replace(backend.settings, lifetime_seconds=lifetime)
    await backend.create("s")
    if budget is None:
        with pytest.raises(SandboxPreparationError, match="at least 300 seconds"):
            await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
        assert not sdk.setups and not sdk.commands
    else:
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
        assert sdk.setups[0][1]["timeout"] == budget
        assert budget <= lifetime - 60 and len(sdk.commands) == 1


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
async def test_lost_final_renewal_retries_only_same_vm_without_replaying_work(cloud):
    backend, sdk = cloud
    await backend.create("s")
    sdk.renewal_failures = 1
    result = await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    assert result.exit_code == 0
    assert len(sdk.created) == len(sdk.setups) == len(sdk.commands) == 1
    assert sdk.killed == ["remote-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_repeated_or_foreign_vm_renewal_never_admits_user_command(cloud, foreign):
    backend, sdk = cloud
    await backend.create("s")
    sdk.renewal_failures = 2
    sdk.wrong_renewal_identity = foreign
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    receipt = backend._read("s")
    assert receipt["python_verified"] and receipt["python_setup_failure"]["phase"] == "final_lifetime"
    with pytest.raises(SandboxStateError, match="uncertain"):
        await backend.execute("s", ["business-command"], execution_policy={"network_enabled": True})
    assert len(sdk.created) == len(sdk.setups) == 1 and not sdk.commands
    assert sdk.renewal_failures == (1 if foreign else 0)


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
