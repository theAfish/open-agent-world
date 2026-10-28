"""Pack-specific discovery, compatibility and launch; lifecycle is host-owned."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec
from open_agent_world.plugin_api import AgentConfig, AgentDependencyError, RuntimeInput, RuntimeModelConnection
from plugins.matcreator.oaw_matcreator import native_environment as environment
from plugins.matcreator.oaw_matcreator.native_runtime import MatCreatorRuntime


@pytest.fixture
def installer(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setenv("OPEN_AGENT_WORLD_DATA_ROOT", str(profile))
    monkeypatch.delenv("OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT", raising=False)
    monkeypatch.setattr(environment.sys, "prefix", str(tmp_path / "host"))
    monkeypatch.setattr(environment, "environment_spec", lambda: PythonEnvironmentSpec("matcreator", "native", verify_imports=("json",)))
    return environment.environment_root(), [], None


def test_discovery_uses_profile_and_not_host_interpreter_prefix(installer, monkeypatch):
    expected, _, _ = installer
    monkeypatch.setattr(environment.sys, "prefix", "D:/different-host")
    for key in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(key, raising=False)
    assert environment.environment_root() == expected


def test_missing_environment_reports_actual_backend_and_profile(installer):
    root, _, _ = installer
    with pytest.raises(RuntimeError) as error:
        environment.read_environment()
    assert sys.executable in str(error.value)
    assert str(root) in str(error.value)
    assert "Pack installation" in str(error.value)


def test_setup_publishes_managed_receipt_and_reuses_offline(installer, monkeypatch):
    from backend.packs import environments
    root, _, _ = installer
    monkeypatch.setattr(environments.shutil, "which", lambda _: None)
    environment.install()
    assert environment.read_environment() == (sys.executable, root)
    assert json.loads((root / "ready.json").read_text())["format"] == 1
    environment.install()
    relocated = root.with_name("relocated")
    root.rename(relocated)
    assert environment.read_environment(relocated) == (sys.executable, relocated)


def test_legacy_runtime_is_read_only_fallback_and_setup_migrates_offline(installer, monkeypatch):
    from backend.packs import environments
    root, _, _ = installer
    legacy = environment.legacy_environment_root()
    for module in ("google/adk", "litellm", "json_repair"):
        package = legacy / "packages" / module
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
    (legacy / "ready.json").write_text(json.dumps({"format": 2, "python": "host"}))
    assert environment.read_environment() == (sys.executable, legacy)
    monkeypatch.setattr(environments.shutil, "which", lambda _: pytest.fail("legacy migration must not download"))
    environment.install()
    assert environment.read_environment() == (sys.executable, root)
    assert json.loads((legacy / "ready.json").read_text())["format"] == 2


def test_explicit_desktop_build_root_is_available_offline_after_relocation(installer, monkeypatch, tmp_path):
    from backend.packs import environments
    build_root = Path(sys.prefix) / "oaw-runtime-bundle"
    monkeypatch.setenv("OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT", str(build_root))
    environment.install()
    monkeypatch.delenv("OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT")
    relocated = tmp_path / "relocated-python"
    Path(sys.prefix).rename(relocated)
    monkeypatch.setattr(environment.sys, "prefix", str(relocated))
    monkeypatch.setattr(environments.shutil, "which", lambda _: pytest.fail("bundle launch must not download"))
    executable, root = environment.read_environment()
    assert executable == sys.executable
    assert root.is_relative_to(relocated / "oaw-runtime-bundle")


@pytest.mark.asyncio
async def test_runtime_uses_host_profile_accessor(monkeypatch, tmp_path):
    from plugins.matcreator.oaw_matcreator import native_runtime
    observed = []
    def read(*, data_root):
        observed.append(data_root)
        return sys.executable, data_root / "runtime"
    monkeypatch.setattr(native_runtime, "read_environment", read)
    provider = SimpleNamespace(managed_environment_root=lambda: tmp_path)
    runtime = MatCreatorRuntime(provider)
    await runtime.create_agent(AgentConfig("agent", "Native"))
    info = await runtime.get_agent("agent")
    assert info.details["environment"]["status"] == "ready"
    assert observed == [tmp_path]


def test_worker_dependency_path_excludes_host_site_packages(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "private_probe.py").write_text("value = 'pack-owned'")
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from native_environment import activate
activate(Path(sys.argv[2]))
import private_probe
print(json.dumps({'value': private_probe.value, 'paths': sys.path}))
"""
    value = json.loads(subprocess.check_output([sys.executable, "-I", "-S", "-c", script,
                                               str(environment.PACKAGE), str(tmp_path)], text=True))
    assert value["value"] == "pack-owned"
    assert not any("site-packages" in path for path in value["paths"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows MAX_PATH regression")
def test_worker_tokenizer_cache_writes_use_extended_paths_after_relocation(tmp_path):
    root = tmp_path / ("environment-" + "x" * 70)
    root.mkdir()
    script = """
import os, sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, sys.argv[1])
from native_environment import activate
activate(Path(sys.argv[2]))
cache = Path(os.environ['CUSTOM_TIKTOKEN_CACHE_DIR'])
assert os.environ['TIKTOKEN_CACHE_DIR'] == str(cache)
cache.mkdir(parents=True, exist_ok=True)
target = cache / ('9b5ad71b2ce5302211f9c61530b329a4922fc6a4.' + str(uuid4()) + '.tmp')
assert len(str(target)) > 260
target.write_bytes(b'tokenizer')
assert target.read_bytes() == b'tokenizer'
"""
    for destination in (root, tmp_path / ("relocated-" + "x" * 70)):
        if destination != root:
            root.rename(destination)
        subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
                        str(environment.PACKAGE), str(destination)], check=True)
        assert len(list(destination.rglob("*.tmp"))) == (1 if destination == root else 2)


@pytest.mark.asyncio
async def test_missing_runtime_never_falls_back_to_host_or_installs_during_run(installer, monkeypatch):
    _, calls, _ = installer
    provider = SimpleNamespace(
        resolve_model=lambda _: RuntimeModelConnection("openai", "test", "http://127.0.0.1", "secret"),
        runtime_state_directory=lambda _: environment.environment_root().parent / "state",
    )
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig("agent", "Native")
    await runtime.create_agent(config)
    info = await runtime.get_agent("agent")
    assert info.details["environment"]["status"] == "missing"

    async def no_worker(*args, **kwargs):
        pytest.fail("An incomplete installation must not start a worker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_worker)
    with pytest.raises(AgentDependencyError, match="Pack installation"):
        async for _ in runtime.execute(config, SimpleNamespace(context_id="session", run_id="run"), RuntimeInput("hello")):
            pass
    assert calls == []
    assert runtime.active == {}


@pytest.mark.asyncio
async def test_default_worker_uses_managed_environment_and_utf8_protocol(tmp_path, monkeypatch):
    from plugins.matcreator.oaw_matcreator import native_runtime as runtime_module
    worker = tmp_path / "native_worker.py"
    worker.write_text('''import json, sys
request = json.loads(sys.stdin.readline())
assert sys.flags.isolated and sys.flags.no_site and sys.flags.utf8_mode
assert sys.argv[1] == '--environment'
print(json.dumps({'type': 'completed', 'text': '\\u4f60\\u597d'}, ensure_ascii=False), flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(runtime_module, "__file__", str(tmp_path / "native_runtime.py"))
    monkeypatch.setattr(runtime_module, "read_environment", lambda: (sys.executable, tmp_path / "managed"))
    provider = SimpleNamespace(
        resolve_model=lambda _: RuntimeModelConnection("openai", "test", "http://127.0.0.1", "secret"),
        runtime_state_directory=lambda _: tmp_path / "state",
    )
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig("agent", "Native")
    await runtime.create_agent(config)
    info = await runtime.get_agent("agent")
    assert info.details["environment"]["status"] == "ready"
    assert info.details["environment"]["python"] == sys.executable
    events = [event async for event in runtime.execute(
        config, SimpleNamespace(context_id="session", run_id="run"), RuntimeInput("hello"))]
    assert events[-1].payload["text"] == "\u4f60\u597d"
    assert runtime.active == {}
