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
def test_setup_routes_all_platforms_through_persistent_interpreter(scripts, monkeypatch, tmp_path, profile, skip_build, ready):
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
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0 if ready or "--check-host" not in command else 1)

    monkeypatch.setattr(setup.subprocess, "run", run)
    args = ["--plugin-path", "external plugin"]
    if profile:
        args += ["--profile", profile]
    if skip_build:
        args += ["--skip-build"]
    setup.main(args)
    commands = [command for command, _ in calls]
    assert commands[0][:2] == ["uv", "sync"]
    assert "--locked" in commands[0] and "--inexact" in commands[0]
    assert commands[1][:2] == [str(python), str(root / "scripts/install-plugins.py")]
    assert commands[1][-2:] == ["--additional-path", str(extra)]
    assert ("--profile" in commands[1]) == bool(profile)
    assert any("ci" in command for command in commands) == (not ready)
    assert [command[-1] for command in commands if command[:2] == ["npm", "run"]] == (
        ["build:pack-sdk", "prepare:packs"] if skip_build else ["build"])
    assert all(kwargs["cwd"] in (root, root / "frontend") for _, kwargs in calls)


def test_setup_stops_after_host_failure(scripts, monkeypatch):
    setup, _, _ = scripts
    monkeypatch.setattr(setup, "prerequisites", lambda **kw: {"uv": "uv"})
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(setup.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        setup.main([])
    assert len(calls) == 1


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
