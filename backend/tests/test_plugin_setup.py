"""The standard installer owns trusted Pack preparation, including desktop builds."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "install-plugins.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("plugin_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = tmp_path / "example"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text('''[project]
name = "example-pack"
[project.entry-points."open_agent_world.plugins"]
example = "example:create_plugin"
[project.entry-points."open_agent_world.setup"]
native = "example:prepare"
''')
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    return module, plugin, calls


@pytest.mark.parametrize("mode", ["editable", "target", "dry-run"])
def test_plugin_hooks_follow_successful_install_in_destination_python(installer, monkeypatch, tmp_path, mode):
    module, plugin, calls = installer
    args = ["install-plugins.py", str(plugin), "--python", "destination-python"]
    if mode == "target":
        args += ["--target", str(tmp_path / "payload")]
    if mode == "dry-run":
        args += ["--dry-run"]
    monkeypatch.setattr(sys, "argv", args)
    module.main()
    assert calls[0][:6] == ["uv", "pip", "install", "--no-deps", "--python", "destination-python"]
    assert len(calls) == (1 if mode == "dry-run" else 2)
    if mode != "dry-run":
        assert calls[1][:2] == ["destination-python", "-c"]
        assert calls[1][-2:] == ["example-pack", str(tmp_path / "payload") if mode == "target" else ""]


def test_failed_dependency_install_never_runs_hooks(installer, monkeypatch):
    module, plugin, calls = installer

    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(module.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", ["install-plugins.py", str(plugin)])
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert len(calls) == 1


@pytest.mark.parametrize("target", [False, True])
def test_declared_pack_uses_shared_manager_instead_of_setup_hook(installer, monkeypatch, tmp_path, target):
    module, plugin, calls = installer
    (plugin / "oaw.pack.toml").write_text("schema_version = 3\n", encoding="utf-8")
    args = ["install-plugins.py", str(plugin), "--python", "destination-python"]
    if target:
        args += ["--target", str(tmp_path / "payload")]
    monkeypatch.setattr(sys, "argv", args)
    module.main()
    assert len(calls) == 2
    assert calls[1][2] == module.PREPARE_DECLARED
    assert module.SETUP_HOOKS not in calls[1]
    assert calls[1][-2:] == [str(plugin.resolve()), str(tmp_path / "payload") if target else ""]


def test_declared_pack_dry_run_never_prepares_environments(installer, monkeypatch):
    module, plugin, calls = installer
    (plugin / "oaw.pack.toml").write_text("schema_version = 3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["install-plugins.py", str(plugin), "--dry-run"])
    module.main()
    assert len(calls) == 1


def test_generic_preparation_checks_declaration_and_preserves_desktop_bundle(installer, monkeypatch, tmp_path):
    import backend.packs.adapters as adapters
    import backend.packs.planning as planning
    import backend.packs.spec as specs
    from backend.packs.lifecycle import ManagedResourceLedger
    module, _, _ = installer
    declarations = {
        "engine": specs.EnvironmentSpec(target="host-worker", prepare="install"),
        "on-demand": specs.EnvironmentSpec(target="host-worker", prepare="on-use"),
        "sandbox": specs.EnvironmentSpec(target="sandbox", prepare="on-use"),
    }
    roots, prepared = [], []

    def prepare_adapter(spec, data_root, *, consumer):
        assert consumer.startswith('live:')
        roots.append(data_root)
    monkeypatch.setattr(adapters, 'prepare_source_adapter', prepare_adapter)
    monkeypatch.setattr(adapters, 'source_adapter_resource_ids', lambda *args: set())
    monkeypatch.setattr(planning, 'prepare_declared', lambda owner, name, declaration, root, data_root:
                        prepared.append((owner, name, root)))
    monkeypatch.setattr(specs, "read_source_pack", lambda root: specs.PackSpec(
        distribution_id='example.distribution', version='1.0.0', name='Fixture', backend=specs.BackendComponent(),
        owner="example.owner", source_root=root, environments=declarations))
    monkeypatch.setattr(sys, 'prefix', str(tmp_path / 'destination'))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["-c", str(tmp_path), str(tmp_path / "source"), str(tmp_path / "target")])
    monkeypatch.setenv("OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT", str(tmp_path / "old-root"))
    exec(module.PREPARE_DECLARED, {})
    assert roots == [Path(sys.prefix) / "oaw-runtime-bundle"]
    assert prepared == [("example.owner", "engine", tmp_path / "source")]
    with ManagedResourceLedger(roots[0]).connect() as db:
        claims = db.execute('SELECT consumer,lease FROM claims').fetchall()
    assert claims and all(row['consumer'] == 'source:example.distribution' and row['lease'] is None for row in claims)


def test_development_preparation_uses_launchers_profile_and_skips_uninstalled_sources(installer, monkeypatch, tmp_path):
    import backend.development as development
    import backend.packs.adapters as adapters
    import backend.packs.spec as specs
    from backend.packs.installation import PackInstallationManager
    module, _, _ = installer
    monkeypatch.setattr(development, 'PROFILES', tmp_path / 'profiles')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'argv', ['-c', str(tmp_path), str(tmp_path / 'source'), '', 'pack-dev'])
    monkeypatch.setenv('OPEN_AGENT_WORLD_DATA_ROOT', str(tmp_path / 'production'))
    monkeypatch.delenv('OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT', raising=False)
    monkeypatch.setattr(specs, 'read_source_pack', lambda root: specs.PackSpec(
        distribution_id='example.distribution', version='1.0.0', name='Fixture', backend=specs.BackendComponent(),
        owner='example.owner', source_root=root))
    roots = []
    monkeypatch.setattr(adapters, 'prepare_source_adapter', lambda spec, data_root, **kwargs: roots.append(data_root))
    monkeypatch.setattr(adapters, 'source_adapter_resource_ids', lambda *args: set())
    exec(module.PREPARE_DECLARED, {})
    root = development.profile_path('pack-dev')
    assert roots == [root]
    development.validate_profile(root)
    assert not (tmp_path / 'production').exists()
    with PackInstallationManager(root).connect(write=True) as db:
        db.execute("INSERT INTO source_states VALUES ('example.distribution',0)")
    roots.clear()
    with pytest.raises(SystemExit) as stopped:
        exec(module.PREPARE_DECLARED, {})
    assert stopped.value.code == 0
    assert roots == []


def test_installer_passes_development_profile_to_destination_interpreter(installer, monkeypatch):
    module, plugin, calls = installer
    (plugin / 'oaw.pack.toml').write_text('schema_version=3\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin), '--python', 'destination-python', '--profile', 'pack-dev'])
    module.main()
    assert calls[1][-3:] == [str(plugin.resolve()), '', 'pack-dev']


def test_additional_plugin_keeps_defaults_and_deduplicates_paths(installer, monkeypatch, tmp_path):
    module, plugin, calls = installer
    additional = tmp_path / 'additional plugin'
    additional.mkdir()
    (additional / 'pyproject.toml').write_text('[project]\nname="additional"\n')
    monkeypatch.setattr(module, 'plugin_paths', lambda root: [plugin])
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', '--additional-path', str(additional), '--additional-path', str(plugin)])
    module.main()
    assert calls[0].count('--editable') == 2
    assert str(plugin) in calls[0] and str(additional) in calls[0]


def test_invalid_profile_fails_before_installation(installer, monkeypatch):
    module, plugin, calls = installer
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin), '--profile', '../outside'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not calls
