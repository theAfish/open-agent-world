"""Source startup must reuse prepared environments and fail before side effects."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scripts(monkeypatch):
    setup = load("source_setup", "setup.py")
    monkeypatch.setitem(sys.modules, "setup", setup)
    return setup, load("source_check", "check-source-environment.py"), load("source_dev", "dev.py")


@pytest.mark.parametrize("profile,skip_build,ready", [(None, False, False), ("demo", True, True), ("demo", False, True)])
@pytest.mark.parametrize("verbose", [False, True])
def test_setup_routes_all_platforms_through_persistent_interpreter(scripts, monkeypatch, tmp_path, profile, skip_build, ready, verbose):
    setup, _, _ = scripts
    root = tmp_path / "checkout with spaces"
    python = root / "backend/.venv/python"
    python.parent.mkdir(parents=True)
    python.touch()
    extra = root / "external plugin"
    extra.mkdir()
    (extra / "pyproject.toml").touch()
    monkeypatch.setattr(setup, "ROOT", root)
    monkeypatch.setattr(setup, "backend_python", lambda: python)
    monkeypatch.setattr(setup, "prerequisites", lambda **kw: {key: key for key in ("uv", "node", "npm")})
    monkeypatch.delenv("OPEN_AGENT_WORLD_SETUP_VERBOSE", raising=False)
    monkeypatch.setenv("SETUP_TEST_INHERITED", "preserved")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--report" in command:
            Path(command[command.index("--report") + 1]).write_text("[]")
        return SimpleNamespace(returncode=0 if ready or "--check-host" not in command else 1)

    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(setup, "prepare_runtime", lambda name, *a, **kw: {"id": name, "ready": True})
    args = ["--with-plugins", "--plugin-path", "external plugin"]
    if profile:
        args += ["--profile", profile]
    if skip_build:
        args += ["--skip-build"]
    if verbose:
        args += ["--verbose"]
    setup.main(args)
    commands = [command for command, _ in calls]
    assert commands[0][:2] == ["uv", "sync"]
    assert "--locked" in commands[0] and "--inexact" in commands[0] and "--extra" not in commands[0]
    assert ("--verbose" in commands[0]) == verbose
    install, options = next((cmd, kw) for cmd, kw in calls if "--report" in cmd)
    assert options["env"].get("OPEN_AGENT_WORLD_SETUP_VERBOSE") == ("1" if verbose else None)
    assert options["env"]["SETUP_TEST_INHERITED"] == "preserved"
    assert "OPEN_AGENT_WORLD_SETUP_VERBOSE" not in setup.os.environ
    assert install[:2] == [str(python), str(root / "scripts/install-plugins.py")]
    assert install[install.index("--additional-path") + 1] == str(extra)
    assert ("--profile" in install) == bool(profile)
    assert ("--skip-build" in install) == skip_build
    if not skip_build:
        assert install[install.index("--frontend-output") + 1] == str(root / "frontend/dist/assets/packs")
    assert any("ci" in command for command in commands) == (not ready)
    builds = [cmd for cmd in commands if cmd[0] == "npm" and "run" in cmd]
    assert [cmd[-1] for cmd in builds] == (["build:pack-sdk"] if skip_build else ["build:pack-sdk", "build"])
    assert all(commands.index(cmd) < commands.index(install) for cmd in builds)
    assert all(kwargs["cwd"] in (root, root / "frontend") for _, kwargs in calls)


@pytest.mark.parametrize("phase", ["backend", "frontend"])
def test_setup_stops_after_host_failure(scripts, monkeypatch, tmp_path, phase):
    setup, _, _ = scripts
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(setup, "backend_python", lambda: python)
    monkeypatch.setattr(setup, "prerequisites", lambda **kw: {key: key for key in ("uv", "node", "npm")})
    monkeypatch.setattr(setup, "prepare_runtime", lambda *a, **kw: pytest.fail("Core failure must stop optional work"))
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        if phase == "backend" or command[-1] == "build":
            raise subprocess.CalledProcessError(7, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        setup.main([])
    assert not any("--report" in command for command in calls)
    assert len(calls) == (1 if phase == "backend" else 4)


@pytest.mark.parametrize('mode', ['core', 'ready', 'backend-failed', 'frontend-failed', 'runtime-failed', 'installer-crashed'])
def test_setup_summary_preserves_core_and_reports_partial_failure(scripts, monkeypatch, tmp_path, capsys, mode):
    setup, _, _ = scripts
    python = tmp_path / 'python'
    python.touch()
    monkeypatch.setattr(setup, 'backend_python', lambda: python)
    monkeypatch.setattr(setup, 'prerequisites', lambda **kw: {key: key for key in ('uv', 'node', 'npm')})
    commands, runtimes = [], []
    error = 'frontend dependency missing' if mode == 'frontend-failed' else 'dependency incompatible'
    def run(command, **kwargs):
        commands.append(command)
        if '--report' in command:
            assert 'Core ready' in capsys.readouterr().out
            if mode == 'installer-crashed':
                return SimpleNamespace(returncode=1)
            failed = mode in ('backend-failed', 'frontend-failed')
            Path(command[command.index('--report') + 1]).write_text(json.dumps([
                {'id': 'knowledge.base', 'path': 'plugins/knowledge_base', 'ready': not failed, 'error': error if failed else ''}]))
            return SimpleNamespace(returncode=3 if failed else 0)
        return SimpleNamespace(returncode=0)
    def runtime(name, *args, **kwargs):
        runtimes.append(name)
        return {'id': name, 'ready': mode != 'runtime-failed' or name != 'adk', 'error': 'SDK unavailable'}
    monkeypatch.setattr(setup, 'prepare_runtime', runtime)
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.main(['--skip-build', *(['--core-only'] if mode == 'core' else [])])
    output = capsys.readouterr()
    if mode == 'core':
        assert runtimes == []
        assert not any('--report' in command for command in commands)
        assert 'not requested (Core-only setup)' in output.out
    else:
        assert runtimes == ['adk', 'litellm']
    if mode.endswith('failed') or mode == 'installer-crashed':
        assert result == 3
        assert '1 failed' in output.out
        assert 'Core remains usable' in output.err
        assert 'Setup complete (' not in output.out
    else:
        assert result is None


@pytest.mark.parametrize('selection', ['default', 'core', 'explicit', 'missing'])
def test_setup_selects_bundled_or_only_requested_projects(scripts, monkeypatch, tmp_path, selection):
    setup, _, _ = scripts
    python = tmp_path / 'python'
    python.touch()
    project = tmp_path / 'external'
    if selection != 'missing':
        project.mkdir()
        (project / 'pyproject.toml').touch()
    monkeypatch.setattr(setup, 'backend_python', lambda: python)
    monkeypatch.setattr(setup, 'prerequisites', lambda **kw: {key: key for key in ('uv', 'node', 'npm')})
    calls = []
    monkeypatch.setattr(setup, 'prepare_runtime', lambda name, *a, **kw: {'id': name, 'ready': True})
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--report" in command:
            Path(command[command.index("--report") + 1]).write_text("[]")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(setup.subprocess, 'run', run)
    args = ['--core-only'] if selection == 'core' else ['--plugin-path', str(project)] if selection in ('explicit', 'missing') else []
    setup.main(['--skip-build', *args])
    assert '--extra' not in calls[0][0]
    installs = [command for command, _ in calls if '--report' in command]
    assert len(installs) == int(selection != 'core')
    if selection in ('explicit', 'missing'):
        assert str(project) in installs[0] and '--additional-path' not in installs[0]
    assert all(kw['env']['OPEN_AGENT_WORLD_CORE_ONLY'] == '1' for cmd, kw in calls if cmd[:2] == ['npm', 'run'])


def test_runtime_install_uses_locked_requirements_without_resyncing_host(scripts, monkeypatch, tmp_path):
    setup, _, _ = scripts
    calls = []
    monkeypatch.setattr(setup, 'run_logged', lambda command: calls.append(command) or subprocess.CompletedProcess(command, 0, stdout=''))
    assert setup.prepare_runtime('adk', {'uv': 'uv'}, tmp_path / 'python')['ready']
    assert [cmd[1] for cmd in calls] == ['export', 'pip']
    assert '--locked' in calls[0] and calls[0][calls[0].index('--extra') + 1] == 'adk'
    assert '--require-hashes' in calls[1] and '--no-deps' in calls[1]
    assert calls[1][calls[1].index('-r') + 1] == calls[0][calls[0].index('--output-file') + 1]


def test_install_output_is_live_and_keeps_bounded_failure_detail(scripts, tmp_path, monkeypatch, capsys):
    setup, _, _ = scripts
    signal = tmp_path / 'observed'
    original = sys.stdout
    class Observer:
        def write(self, value):
            if 'resolver progress' in value:
                signal.touch()
            return original.write(value)
        def flush(self):
            return original.flush()
    monkeypatch.setattr(sys, 'stdout', Observer())
    code = (
        "import sys,time; from pathlib import Path; "
        "print('resolver progress', file=sys.stderr, flush=True); "
        "deadline=time.monotonic()+5\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "print('x'*7000); print('resolver failure', file=sys.stderr, flush=True); "
        "sys.exit(7 if Path(sys.argv[1]).exists() else 9)"
    )
    result = setup.run_logged([sys.executable, '-u', '-c', code, str(signal)])
    assert result.returncode == 7, 'progress must arrive before the child exits'
    assert len(result.stdout) <= 6000 and 'resolver failure' in result.stdout
    assert 'resolver progress' in capsys.readouterr().out


@pytest.mark.parametrize("install", [False, True])
@pytest.mark.parametrize("version", ["v22.14.0", "v24.19.0"])
def test_setup_and_dev_accept_node22_and_node24(scripts, monkeypatch, capsys, install, version):
    setup, _, _ = scripts
    requested = []
    monkeypatch.setattr(setup.shutil, "which", lambda name: requested.append(name) or name)
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *args, **kwargs: version + "\n")
    setup.prerequisites(install=install)
    assert ("uv" in requested) == install
    assert ("engine warning" in capsys.readouterr().err) == version.startswith("v22.")


@pytest.mark.parametrize("install", [False, True])
def test_setup_and_dev_reject_node_below_shared_minimum(scripts, monkeypatch, install):
    setup, _, _ = scripts
    monkeypatch.setattr(setup.shutil, "which", lambda name: name)
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *args, **kwargs: "v20.19.0\n")
    with pytest.raises(RuntimeError, match="Node.js 22 or newer"):
        setup.prerequisites(install=install)


def test_plugin_check_rejects_another_checkout_and_stale_entry_points(scripts, monkeypatch, tmp_path):
    _, check, _ = scripts
    root = tmp_path / "plugin with spaces"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1.0"\n'
                                        '[project.entry-points."open_agent_world.plugins"]\nfixture="fixture:create"\n')
    direct = {"url": root.as_uri(), "dir_info": {"editable": True}}
    installed = SimpleNamespace(version="1.0", read_text=lambda name: json.dumps(direct),
                                entry_points=[SimpleNamespace(group="open_agent_world.plugins", name="fixture", value="fixture:create")])
    monkeypatch.setattr(check.metadata, "distribution", lambda name: installed)
    check.check_plugin(root)
    direct["url"] = (tmp_path / "other checkout").as_uri()
    with pytest.raises(RuntimeError, match="not installed editable"):
        check.check_plugin(root)
    direct["url"] = root.as_uri()
    installed.entry_points[0].value = "fixture:old"
    with pytest.raises(RuntimeError, match="entry point.*stale"):
        check.check_plugin(root)


def test_source_enrollment_check_never_initializes_a_profile(scripts, tmp_path):
    _, check, _ = scripts
    root = tmp_path / "missing"
    assert check.unenrolled_sources(root) == set()
    assert not root.exists()
    (root / "packs").mkdir(parents=True)
    with sqlite3.connect(root / "packs/installations.sqlite3") as db:
        db.execute("CREATE TABLE source_states (id TEXT PRIMARY KEY, enrolled INTEGER)")
        db.executemany("INSERT INTO source_states VALUES (?,?)", [("removed", 0), ("enabled", 1)])
    assert check.unenrolled_sources(root) == {"removed"}


def test_check_skips_uninstalled_packs_and_reports_missing_profile_environment(scripts, monkeypatch, tmp_path):
    _, check, _ = scripts
    import backend.packs.adapters as adapters
    import backend.packs.planning as planning
    import backend.packs.spec as specs
    source = tmp_path / "pack"
    source.mkdir()
    (source / "oaw.pack.toml").touch()
    spec = SimpleNamespace(distribution_id="fixture", owner="owner", source_root=source, backend=SimpleNamespace(python=()),
                           environments={"worker": SimpleNamespace(target="host-worker", prepare="install")})
    monkeypatch.setattr(specs, "read_source_pack", lambda path: spec)
    inspected = []
    monkeypatch.setattr(adapters, "_source_adapter_location", lambda *args: inspected.append(args))
    monkeypatch.setattr(adapters, "check_adapter_dependencies", lambda *args, **kwargs: None)
    monkeypatch.setattr(planning, "environment_status", lambda *args: {"state": "missing"})
    monkeypatch.setattr(check, "unenrolled_sources", lambda root: {"fixture"})
    check.check_packs([source], tmp_path / "profile")
    assert not inspected
    monkeypatch.setattr(check, "unenrolled_sources", lambda root: set())
    with pytest.raises(RuntimeError, match="owner/worker is missing"):
        check.check_packs([source], tmp_path / "profile")
    assert not (tmp_path / "profile").exists()


def test_repeated_dev_starts_prepare_once_then_run_vite_and_propagate_failure(scripts, monkeypatch, tmp_path):
    _, _, dev = scripts
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(dev, "backend_python", lambda root: python)
    monkeypatch.setattr(dev, "prerequisites", lambda **kw: {"node": "node", "npm": "npm"})
    monkeypatch.setattr(dev, "available_port", lambda port: port)
    monkeypatch.setattr(sys, "argv", ["dev.py", "--profile", "fixture", "--plugin-path", "extra plugin"])
    commands, children, stopped = [], [], []
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **kwargs: commands.append(command))
    probes = []
    def probe(url, **kwargs):
        probes.append(url)
        return nullcontext()
    monkeypatch.setattr(dev.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=probe))

    def popen(command, **kwargs):
        commands.append(command)
        code = 2 if command[0] == "node" else None
        process = SimpleNamespace(poll=lambda: code, returncode=code)
        children.append(process)
        return process

    monkeypatch.setattr(dev.subprocess, "Popen", popen)
    monkeypatch.setattr(dev, "stop", stopped.append)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="Frontend exited unexpectedly.*2"):
            dev.main()
    assert len(commands) == 8
    assert len(stopped) == 4
    assert all(not any(token in command for token in ("install", "sync", "ci", "--prepare")) for command in commands)
    assert commands[0][1].endswith("check-source-environment.py")
    assert commands[1][-1].endswith("prepare-dev.mjs")
    assert commands[3][0] == "node"
    assert Path(commands[3][1]).as_posix().endswith("vite/bin/vite.js")
    assert commands[3][2:4] == ["--host", "127.0.0.1"]
    assert stopped[:2] == list(reversed(children[:2]))
    assert probes == ["http://127.0.0.1:8000/health/ready"] * 2


def test_failed_preflight_never_launches_processes(scripts, monkeypatch, tmp_path):
    _, _, dev = scripts
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(dev, "backend_python", lambda root: python)
    monkeypatch.setattr(dev, "prerequisites", lambda **kw: {})
    monkeypatch.setattr(sys, "argv", ["dev.py"])
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, command)))
    monkeypatch.setattr(dev.subprocess, "Popen", lambda *a, **kw: pytest.fail("Launched after failed preflight"))
    with pytest.raises(subprocess.CalledProcessError):
        dev.main()


def test_cleanup_does_not_hang_or_mask_failure_when_termination_is_denied(scripts, monkeypatch, capsys):
    _, _, dev = scripts
    waits = []

    def wait(timeout):
        waits.append(timeout)
        raise subprocess.TimeoutExpired('fixture', timeout)

    process = SimpleNamespace(pid=42, stdin=None, poll=lambda: None, wait=wait)
    monkeypatch.setattr(dev.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(dev.os, 'killpg', lambda *args: None, raising=False)
    dev.stop(process)
    assert waits == [10, 10]
    assert 'Unable to stop owned process 42' in capsys.readouterr().err
