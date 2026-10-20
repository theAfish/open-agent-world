"""Profile admission and real isolated workers; no live Sandbox or network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from backend.packs.spec import EnvironmentSpec
from backend.sandbox import pack_profiles
from backend.sandbox.materialization import RuntimeBundle, materialize_bundle
from backend.sandbox.models import SandboxValidationError
from open_agent_world.skill_packages import Skill, SkillRuntimeRequirement


def registry(declaration=None, *, enabled=True):
    declaration = declaration or EnvironmentSpec(target="sandbox", python=("example==1",))
    return SimpleNamespace(is_enabled=lambda owner: enabled and owner == "example",
                           pack_specs={"example": SimpleNamespace(environments={"compute": declaration})})


def requirement(**changes):
    return SkillRuntimeRequirement(owner="example", environment="compute", **changes)


def test_declared_profiles_compose_without_host_requirements():
    definition = EnvironmentSpec(target="sandbox", python=("numpy>=2",))
    value = pack_profiles.resolve_profiles(registry(definition), (requirement(),))
    assert value["python"] == ["numpy>=2"]
    assert value["sources"][0]["digest"] == pack_profiles.environment_digest(definition)
    assert "pydantic" not in str(value)


def test_incompatible_same_process_pins_fail_before_preparation():
    plugins = registry(EnvironmentSpec(target="sandbox", python=("example==1",)))
    plugins.pack_specs["example"].environments["other"] = EnvironmentSpec(target="sandbox", python=("example==2",))
    with pytest.raises(SandboxValidationError, match="Conflicting"):
        pack_profiles.resolve_profiles(plugins, (requirement(), SkillRuntimeRequirement(owner="example", environment="other")))


def test_node_cli_dependencies_remain_sandbox_only_and_reject_installer_options():
    definition = EnvironmentSpec(target="sandbox", provider="node", node=("@example/bohr@1.2.3",))
    result = pack_profiles.resolve_profiles(registry(definition), (requirement(),))
    assert result["node"] == ["@example/bohr@1.2.3"]
    assert not result["python"]
    unsafe = EnvironmentSpec(target="sandbox", provider="node", node=("--prefix=/outside",))
    with pytest.raises(SandboxValidationError, match="registry package"):
        pack_profiles.resolve_profiles(registry(unsafe), (requirement(),))


@pytest.mark.parametrize("definition", [
    EnvironmentSpec(target="host-worker"),
    EnvironmentSpec(target="sandbox", management="legacy-shared"),
    EnvironmentSpec(target="sandbox", provider="external", management="external"),
    EnvironmentSpec(target="sandbox", lock="uv.lock"),
    EnvironmentSpec(target="sandbox", resources=("resource.py",)),
])
def test_unsupported_recipes_do_not_fall_back_to_host_installation(definition):
    with pytest.raises(SandboxValidationError):
        pack_profiles.resolve_profiles(registry(definition), (requirement(),))


def test_disabled_missing_and_stale_refs_fail_before_execution():
    with pytest.raises(SandboxValidationError, match="disabled"):
        pack_profiles.resolve_profiles(registry(enabled=False), (requirement(),))
    with pytest.raises(SandboxValidationError, match="not declared"):
        pack_profiles.resolve_profiles(registry(), (SkillRuntimeRequirement(owner="example", environment="missing"),))
    with pytest.raises(SandboxValidationError, match="changed"):
        pack_profiles.resolve_profiles(registry(), (requirement(digest="0" * 64),))


@pytest.mark.parametrize("interpreter", [["bash", "-c"], ["python3", "-c"], ["/usr/local/custom/python"]])
def test_unknown_interpreters_cannot_silently_bypass_profile(interpreter):
    bundle = RuntimeBundle("skills/example", (("scripts/probe.py", b"print(1)"),))
    with pytest.raises(SandboxValidationError, match="interpreter"):
        pack_profiles.bind_profiles(registry(), (requirement(),), bundle, interpreter, "scripts/probe.py", [])


def test_reserved_bundle_directory_is_not_overridden():
    bundle = RuntimeBundle("skills/example", (("_oaw_profile/runner.py", b"bad"), ("script.py", b"print(1)")))
    with pytest.raises(SandboxValidationError, match="reserved"):
        pack_profiles.bind_profiles(registry(), (requirement(),), bundle, ["python3"], "script.py", [])


def test_profile_binding_wraps_same_runtime_mount_and_keeps_arguments(tmp_path):
    bundle = RuntimeBundle("skills/example", (("scripts/probe.py", b"print(1)"),))
    argv, mount = pack_profiles.bind_profiles(registry(), (requirement(),), bundle,
                                             ["python3"], "scripts/probe.py", ["a b", "$(literal)"])
    assert argv[:4] == ["python3", "-I", "-S", "_oaw_profile/runner.py"]
    assert argv[5:] == ["a b", "$(literal)"]
    root = materialize_bundle(tmp_path, mount.bundle)
    assert mount.command(argv, root)[3] == str(root / "_oaw_profile/runner.py")
    assert (root / "scripts/probe.py").read_bytes() == b"print(1)"


def execute_fixture(tmp_path, *, requirements=(), imports=()):
    # This exercises the exact runtime file through a subprocess. OS isolation
    # remains the responsibility of the normal Sandbox adapters, tested apart.
    root = tmp_path / "bundle"
    (root / "_oaw_profile").mkdir(parents=True)
    (root / "scripts").mkdir()
    runner = Path(pack_profiles.__file__).with_name("pack_profile_runner.py")
    shutil.copyfile(runner, root / "_oaw_profile/runner.py")
    (root / "scripts/helper.py").write_text("answer = 42\n")
    (root / "scripts/probe.py").write_text("""import importlib.util,json,sys
from helper import answer
print(json.dumps({'answer': answer, 'argv': sys.argv[1:], 'paths': sys.path,
    'host_pydantic': importlib.util.find_spec('pydantic') is not None}))
""")
    recipe = {"format": 1, "python": list(requirements), "node": [], "verify_imports": list(imports),
              "requires_python": [], "sources": [], "mode": "python", "script_path": "scripts/probe.py", "command_id": "command-1"}
    (root / "_oaw_profile/recipe.json").write_text(json.dumps(recipe))
    home = tmp_path / "home"
    environment = {**os.environ, "HOME": str(home), "LOCALAPPDATA": str(home)}
    command = [sys.executable, "-I", "-S", str(root / "_oaw_profile/runner.py"), "command-1", "a b", "$(literal)"]
    return command, environment, home


def test_real_stdlib_profile_isolated_reused_and_scoped_to_sandbox_home(tmp_path):
    command, env, home = execute_fixture(tmp_path)
    first = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    value = json.loads(first.stdout.splitlines()[-1])
    assert value["answer"] == 42
    assert value["argv"] == ["a b", "$(literal)"]
    assert not value["host_pydantic"]
    assert not any(str(Path(sys.prefix) / "Lib/site-packages") == path for path in value["paths"])
    subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    profiles = list((home / ".oaw/pack-environments").iterdir())
    assert len(profiles) == 1
    assert len(list(profiles[0].glob("generation-*"))) == 1
    receipt = json.loads((profiles[0] / "operation.json").read_text())
    assert receipt["status"] == "ready"
    assert receipt["scope"] == "sandbox-writable"
    assert receipt["command_id"] == "command-1"
    (profiles[0] / "selected.json").unlink()
    subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert len(list(profiles[0].glob("generation-*"))) == 1
    assert json.loads((profiles[0] / "operation.json").read_text())["operation_id"] == receipt["operation_id"]


def test_real_profile_installs_local_wheel_without_host_dependency_leak(tmp_path):
    wheel = tmp_path / "oaw_profile_probe-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("oaw_profile_probe/__init__.py", "value = 123\n")
        archive.writestr("oaw_profile_probe-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: oaw-profile-probe\nVersion: 1.0\n")
        archive.writestr("oaw_profile_probe-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("oaw_profile_probe-1.0.dist-info/RECORD", "")
    # A direct local recipe supplies the test wheel without any package index.
    # Public Pack profile refs intentionally allow index requirements only.
    command, env, home = execute_fixture(tmp_path, requirements=(str(wheel),), imports=("oaw_profile_probe",))
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert not json.loads(result.stdout.splitlines()[-1])["host_pydantic"]
    receipt = json.loads(next((home / ".oaw/pack-environments").glob("*/operation.json")).read_text())
    assert {"name": "oaw-profile-probe", "version": "1.0"} in receipt["resolved_python"]


def test_node_entrypoint_uses_private_module_path_and_literal_arguments(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required for the Node entrypoint integration")
    command, env, home = execute_fixture(tmp_path)
    root = tmp_path / "bundle"
    (root / "scripts/probe.js").write_text("console.log(JSON.stringify({args:process.argv.slice(2),nodePath:process.env.NODE_PATH}))")
    recipe_file = root / "_oaw_profile/recipe.json"
    recipe = json.loads(recipe_file.read_text())
    recipe.update(mode="node", script_path="scripts/probe.js")
    recipe_file.write_text(json.dumps(recipe))
    env["NODE_PATH"] = "forbidden-inherited-path"
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    value = json.loads(result.stdout.splitlines()[-1])
    assert value["args"] == ["a b", "$(literal)"]
    assert Path(value["nodePath"]).is_relative_to(home)


def test_inventory_reports_provider_bounds_before_skill_run(tmp_path):
    from backend.packs.planning import environment_status
    declaration = EnvironmentSpec(target="sandbox", resources=("resource.txt",))
    value = environment_status("example", "compute", declaration, None, tmp_path)
    assert value["state"] == "unsupported"
    assert "resources" in value["error"]


def test_skill_runtime_references_survive_serialization_and_are_bounded():
    value = Skill(runtime_requirements=(requirement(),))
    assert Skill.model_validate_json(value.model_dump_json()).runtime_requirements == value.runtime_requirements
    with pytest.raises(ValueError):
        Skill(runtime_requirements=(requirement(),) * 21)


def test_sandbox_cache_shares_recipes_and_collects_only_released_profile_refs(tmp_path, monkeypatch):
    from backend.sandbox.pack_profile_runner import environment_root, runtime_lease
    monkeypatch.setenv('HOME', str(tmp_path))
    profile = '1' * 64
    first = {'owner': 'first', 'environment': 'worker', 'digest': 'a' * 64, 'platforms': []}
    second = {**first, 'owner': 'second'}
    recipe = {'format': 1, 'python': [], 'node': [], 'verify_imports': [], 'requires_python': [],
              'sources': [first], 'ownership': {'profile': profile, 'revision': 1, 'desired': [first, second]}}
    root = environment_root(recipe)
    with runtime_lease(recipe):
        (root / 'payload.txt').write_text('managed')
    recipe['sources'] = [second]
    assert environment_root(recipe) == root
    with runtime_lease(recipe):
        pass
    recipe['ownership'] = {'profile': profile, 'revision': 2, 'desired': [second]}
    with runtime_lease(recipe):
        assert (root / 'payload.txt').read_text() == 'managed'
    assert len(json.loads((root / 'owners.json').read_text())['profiles'][profile]) == 1
    # Another profile still owns the same recipe; this profile cannot delete it.
    other = {**recipe, 'ownership': {'profile': '2' * 64, 'revision': 1, 'desired': [second]}}
    with runtime_lease(other):
        pass
    replacement = {**recipe, 'verify_imports': ['json'], 'sources': [],
                   'ownership': {'profile': profile, 'revision': 3, 'desired': []}}
    with runtime_lease(replacement):
        assert root.exists()
    # Release the second profile and retain an actively executing old command.
    replacement['ownership'] = {'profile': '2' * 64, 'revision': 2, 'desired': []}
    with runtime_lease(recipe):
        with runtime_lease(replacement):
            assert root.exists()
    with runtime_lease(replacement):
        assert not root.exists()


def test_older_sandbox_command_cannot_restore_released_ownership(tmp_path, monkeypatch):
    from backend.sandbox.pack_profile_runner import environment_root, runtime_lease
    monkeypatch.setenv('HOME', str(tmp_path))
    source = {'owner': 'first', 'environment': 'worker', 'digest': 'a' * 64, 'platforms': []}
    recipe = {'format': 1, 'python': [], 'node': [], 'verify_imports': [], 'requires_python': [], 'sources': [source],
              'ownership': {'profile': '1' * 64, 'revision': 1, 'desired': [source]}}
    latest = {**recipe, 'ownership': {'profile': '1' * 64, 'revision': 2, 'desired': []}}
    with runtime_lease(latest):
        pass
    with runtime_lease(recipe):
        assert json.loads((environment_root(recipe) / 'owners.json').read_text())['profiles']['1' * 64] == []


def test_builtin_executable_skill_references_resolve_current_declarations():
    from backend.plugins.loader import load_plugin_registry
    from plugins.atomsculptor.src.oaw_atomsculptor.skill_package import atomsculptor_skills
    from plugins.matcreator.oaw_matcreator.native_environment import PACKAGE
    plugins = load_plugin_registry()
    for skill in atomsculptor_skills().skills:
        if "requirements.txt" in skill.files:
            assert skill.runtime_requirements
            assert pack_profiles.resolve_profiles(plugins, skill.runtime_requirements)["python"]
    local = next(value for value in json.loads((PACKAGE / "packages/core.json").read_text(encoding="utf-8"))["skills"]
                 if value["id"] == "local-structure")
    assert pack_profiles.resolve_profiles(plugins, Skill.model_validate(local).runtime_requirements)["python"] == ["ase<4,>=3.23"]


@pytest.mark.parametrize('argv', [
    ['/custom/python', '-c', 'print(1)'], ['python3.11', '-V'], ['py', '-3', 'probe.py'],
    ['python', '-S', 'probe.py'], ['python', '-uS', '-m', 'probe'],
])
def test_ordinary_commands_reject_profile_bypass(argv):
    with pytest.raises(SandboxValidationError, match='interpreter|bypasses'):
        pack_profiles.bind_command_profiles(registry(), (requirement(),), argv)


def test_uninstalled_pack_cannot_bind_an_already_loaded_runtime():
    plugins = registry()
    plugins.enrolled_pack_owners = set()
    with pytest.raises(SandboxValidationError, match='uninstalled'):
        pack_profiles.bind_command_profiles(plugins, (requirement(),), ['python3', '-V'])


def test_real_ordinary_commands_keep_cwd_module_and_shell_semantics(tmp_path):
    plugins = registry(EnvironmentSpec(target='sandbox', verify_imports=('json',)))
    argv, mount = pack_profiles.bind_command_profiles(plugins, (requirement(),), ['python3', '-V'])
    root = materialize_bundle(tmp_path / 'mount', mount.bundle)
    prefix = [sys.executable, *mount.command(argv, root)[1:5]]
    home = tmp_path / 'home'
    home.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'helper.py').write_text('answer = 42\n')
    source = """import importlib.util,json,os,sys
from helper import answer
print(json.dumps({'answer':answer,'argv':sys.argv[1:],'cwd':os.getcwd(),
 'prefix':sys.prefix,'host_pydantic':importlib.util.find_spec('pydantic') is not None}))
"""
    (workspace / 'probe.py').write_text(source)
    environment = {**os.environ, 'HOME': str(home), 'LOCALAPPDATA': str(home),
                   'PYTHONPATH': str(Path(sys.executable).parent.parent / 'Lib/site-packages')}
    commands = [
        ['python3', '-u', 'probe.py', 'a b', '-S', '$(literal)'],
        ['python', '-c', source, 'a b', '-S', '$(literal)'],
        ['/usr/bin/python3', '-m', 'probe', 'a b', '-S', '$(literal)'],
    ]
    for command in commands:
        result = subprocess.run([*prefix, *command], cwd=workspace, env=environment,
                                capture_output=True, text=True, check=True)
        value = json.loads(result.stdout.splitlines()[-1])
        assert value['answer'] == 42 and value['argv'] == ['a b', '-S', '$(literal)']
        assert Path(value['cwd']) == workspace and Path(value['prefix']).is_relative_to(home)
        assert not value['host_pydantic']
    shell = ['cmd.exe', '/d', '/c', 'python3 probe.py shell'] if os.name == 'nt' else ['sh', '-c', 'python3 probe.py shell']
    result = subprocess.run([*prefix, *shell], cwd=workspace, env=environment,
                            capture_output=True, text=True, check=True)
    value = json.loads(result.stdout.splitlines()[-1])
    assert value['argv'] == ['shell'] and Path(value['prefix']).is_relative_to(home)
    assert not value['host_pydantic']
    profiles = list((home / '.oaw/pack-environments').iterdir())
    assert len(profiles) == 1 and len(list(profiles[0].glob('generation-*'))) == 1
    second_argv, second_mount = pack_profiles.bind_command_profiles(plugins, (requirement(),), ['python', '-m', 'probe'])
    assert second_mount.bundle == mount.bundle


def test_runtime_defaults_follow_invoking_run_and_require_valid_identifiers():
    from backend.plugins.registry import PluginDescriptor
    from backend.runs.manager import RunManager
    descriptor = PluginDescriptor(id='example', version='1', plugin_api_version='1.35', sandbox_environments=('compute',))
    for names in [('compute', 'compute'), ('../outside',), ('',)]:
        with pytest.raises(ValueError):
            PluginDescriptor(id='example', version='1', plugin_api_version='1.35', sandbox_environments=names)
    seen = []
    manager = SimpleNamespace(current_context=SimpleNamespace(agent_id='agent', runtime_provider_id='example.active'),
        plugins=SimpleNamespace(assert_runtime_provider_enabled=seen.append),
        world=SimpleNamespace(get_card=lambda _: 'configured-card'), _optional_provider_id=lambda _: 'example.configured')
    assert RunManager.runtime_provider_id_for_agent(manager, 'agent') == 'example.active'
    assert seen == ['example.active']
    assert RunManager.runtime_provider_id_for_agent(manager, 'other') == 'example.configured'
