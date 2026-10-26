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
def test_undeclared_setup_hooks_are_rejected_before_install(installer, monkeypatch, tmp_path, mode):
    module, plugin, calls = installer
    args = ["install-plugins.py", str(plugin), "--python", "destination-python"]
    if mode == "target":
        args += ["--target", str(tmp_path / "payload")]
    if mode == "dry-run":
        args += ["--dry-run"]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not calls


def test_failed_desktop_install_never_prepares_environments(installer, monkeypatch, tmp_path):
    module, plugin, calls = installer

    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(module.subprocess, "run", fail)
    (plugin / "oaw.pack.toml").write_text("schema_version=3\n")
    monkeypatch.setattr(sys, "argv", ["install-plugins.py", str(plugin), "--target", str(tmp_path / "payload")])
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
    assert len(calls) == (2 if target else 1)
    assert calls[-1][2] == module.PREPARE_DECLARED
    assert calls[-1][-2:] == [str(plugin.resolve()), str(tmp_path / "payload") if target else ""]
    if not target:
        assert not any('--editable' in call for call in calls)


def test_declared_pack_dry_run_never_prepares_environments(installer, monkeypatch):
    module, plugin, calls = installer
    (plugin / "oaw.pack.toml").write_text("schema_version = 3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["install-plugins.py", str(plugin), "--dry-run"])
    module.main()
    assert calls == []


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
    assert stopped.value.code == 10
    assert roots == []


def test_installer_passes_development_profile_to_destination_interpreter(installer, monkeypatch):
    module, plugin, calls = installer
    (plugin / 'oaw.pack.toml').write_text('schema_version=3\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin), '--python', 'destination-python', '--profile', 'pack-dev'])
    module.main()
    assert calls[0][-3:] == [str(plugin.resolve()), '', 'pack-dev']


def test_additional_plugin_keeps_defaults_and_deduplicates_paths(installer, monkeypatch, tmp_path):
    module, plugin, calls = installer
    additional = tmp_path / 'additional plugin'
    additional.mkdir()
    (additional / 'pyproject.toml').write_text('[project]\nname="additional"\n')
    monkeypatch.setattr(module, 'plugin_paths', lambda root, **kwargs: [plugin])
    monkeypatch.setattr(module, 'run_logged', lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, stdout=''))
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', '--additional-path', str(additional), '--additional-path', str(plugin)])
    module.main()
    assert len(calls) == 2
    assert str(plugin) in calls[0] and str(additional) in calls[1]
    assert all(call.count(str(plugin)) <= 1 for call in calls)


def test_invalid_profile_fails_before_installation(installer, monkeypatch):
    module, plugin, calls = installer
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin), '--profile', '../outside'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not calls


def test_partial_preparation_reports_identity_reason_and_continues(installer, monkeypatch, tmp_path, capsys):
    import json
    module, plugin, calls = installer
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'pyproject.toml').write_text('[project]\nname="other"\n')
    (plugin / 'oaw.pack.toml').write_text('[distribution]\nid="knowledge.base"\n')
    report = tmp_path / 'report.json'
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if str(plugin) in command else 0,
            stdout='Incompatible shared adapter dependency: pymupdf4llm<0.4 (available 1.28.2)' if str(plugin) in command else '')
    monkeypatch.setattr(module, 'run_logged', run)
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin), str(other), '--continue-on-error', '--report', str(report), '--profile', 'demo'])
    assert module.main() == 3
    assert len(calls) == 2
    results = json.loads(report.read_text())
    assert [row['ready'] for row in results] == [False, True]
    output = capsys.readouterr()
    assert '1 ready, 1 failed' in output.out
    assert 'knowledge.base: Incompatible shared adapter dependency' in output.err
    assert '--profile demo' in output.err


@pytest.mark.parametrize('platform,machine,version,allowed', [
    ('win32', 'AMD64', '1.28.2', True), ('linux', 'x86_64', '1.28.2', True),
    ('darwin', 'arm64', '1.28.2', True), ('darwin', 'x86_64', '1.28.2', False),
    ('darwin', 'x86_64', '0.3.4', True), ('win32', 'AMD64', '2.0.0', False),
])
def test_pdf_dependency_keeps_api_and_intel_mac_adapter_boundaries(platform, machine, version, allowed):
    import tomllib
    from packaging.requirements import Requirement
    project = Path(__file__).resolve().parents[2] / 'plugins/knowledge_base/pyproject.toml'
    requirements = [Requirement(value) for value in tomllib.loads(project.read_text())['project']['dependencies']]
    active = [r for r in requirements if r.name == 'pymupdf4llm' and r.marker.evaluate(
        {'sys_platform': platform, 'platform_machine': machine})]
    assert len(active) == 1
    assert (version in active[0].specifier) == allowed


def test_failed_discovery_is_included_even_when_no_pack_can_be_prepared(installer, monkeypatch, tmp_path, capsys):
    import json
    module, _, calls = installer
    plugin = tmp_path / 'plugins' / 'broken'
    plugin.mkdir(parents=True)
    (plugin / 'pyproject.toml').write_text('not valid toml')
    report = tmp_path / 'report.json'
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', '--continue-on-error', '--report', str(report)])
    assert module.main() == 3
    assert not calls
    assert json.loads(report.read_text())[0]['path'] == str(plugin)
    assert '0 ready, 1 failed' in capsys.readouterr().out


def test_removed_source_skips_frontend_preparation(installer, monkeypatch):
    module, plugin, calls = installer
    (plugin / 'oaw.pack.toml').write_text('schema_version=3\n')
    def removed(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(10, command)
    monkeypatch.setattr(module.subprocess, 'run', removed)
    monkeypatch.setattr(module, 'prepare_frontend', lambda *args: pytest.fail('Removed Pack frontend must stay removed'))
    monkeypatch.setattr(sys, 'argv', ['install-plugins.py', str(plugin)])
    module.main()
    assert len(calls) == 1


def test_source_batch_isolates_backend_and_frontend_failures_and_reuses_prepared_pack(tmp_path):
    """Real Python -> per-Pack child -> Node build, without package downloads."""
    import json
    import os
    root = Path(__file__).resolve().parents[2]
    paths = []
    for name in ('broken-backend', 'broken-frontend', 'healthy'):
        project = tmp_path / name
        frontend = project / 'frontend'
        frontend.mkdir(parents=True)
        paths.append(project)
        (project / 'pyproject.toml').write_text(
            f'[project]\nname="{name}"\nversion="1.0.0"\ndependencies=[]\n')
        (project / 'oaw.pack.toml').write_text(
            f'schema_version=3\n[distribution]\nid="test.{name}"\n'
            '[components.frontend]\nentry="frontend/entry.js"\nproject="frontend/package.json"\n'
            '[[contributions.cards]]\n')
        (frontend / 'entry.js').write_text('export default {};')
        (frontend / 'plugin.json').write_text(json.dumps({'apiVersion': 1, 'pluginId': f'test.{name}'}))
        dependencies = {'@oaw/plugin-api': '1.0.0'}
        (frontend / 'package.json').write_text(json.dumps({
            'type': 'module', 'devDependencies': dependencies, 'scripts': {'build': 'node build.mjs'}}))
        (frontend / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': {
            '': {'devDependencies': dependencies}, 'node_modules/@oaw/plugin-api': {'version': '1.0.0'}}}))
        sdk = frontend / 'node_modules/@oaw/plugin-api'
        sdk.mkdir(parents=True)
        (sdk / 'package.json').write_text('{"version":"1.0.0"}')
        (frontend / 'build.mjs').write_text(
            "import {mkdirSync,writeFileSync,readFileSync,existsSync} from 'node:fs';\n"
            "mkdirSync('dist',{recursive:true});\n"
            "const count=existsSync('dist/count')?Number(readFileSync('dist/count','utf8')):0;\n"
            "writeFileSync('dist/count',String(count+1));\n"
            "writeFileSync('dist/index.js','export default {};');\n"
            "writeFileSync('dist/frontend-build.json',JSON.stringify({entry:'index.js',files:[]}));\n"
            + ("console.error('fixture frontend failure'); process.exit(7);\n" if name == 'broken-frontend' else ''))
    (paths[0] / 'pyproject.toml').write_text('invalid toml')
    report = tmp_path / 'report.json'
    output = tmp_path / 'host-assets'
    env = {**os.environ, 'OPEN_AGENT_WORLD_DATA_ROOT': str(tmp_path / 'data'),
           'OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT': '', 'OPEN_AGENT_WORLD_CORE_ONLY': '1', 'UV_OFFLINE': '1'}
    command = [sys.executable, str(root / 'scripts/install-plugins.py'), *map(str, paths), str(paths[-1]),
               '--frontend-output', str(output), '--report', str(report)]
    result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 3, result.stdout + result.stderr
    rows = json.loads(report.read_text())
    assert [row['ready'] for row in rows] == [False, False, True], rows
    assert 'fixture frontend failure' in rows[1]['error']
    assert (output / 'test.healthy/index.js').is_file()
    assert not (output / 'test.broken-frontend').exists()
    repeated = subprocess.run([sys.executable, str(root / 'scripts/install-plugins.py'), str(paths[-1]),
                               '--frontend-output', str(output)], cwd=root, env=env, text=True, capture_output=True, timeout=60)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert '[Pack test.healthy] Reused' in repeated.stdout
    assert (paths[-1] / 'frontend/dist/count').read_text() == '1'
