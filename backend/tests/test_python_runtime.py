"""Atomic managed-Python bootstrap: a failed download never corrupts base."""
import json
from pathlib import Path

import pytest

from backend.sandbox import python_runtime
from backend.sandbox.models import SandboxPreparationError


def make_runtime(tmp_path: Path) -> python_runtime.SharedPythonRuntime:
    runtime = python_runtime.SharedPythonRuntime(tmp_path, darwin_store=tmp_path / "machine-cache")
    runtime._ensure_uv = lambda: "uv"  # type: ignore[method-assign]
    return runtime


def fake_python(root: Path, label: str) -> Path:
    distribution = root / f"cpython-3.12-{label}"
    binary = distribution / "bin" / "python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    stdlib = distribution / "lib" / "python3.12" / "os.py"
    stdlib.parent.mkdir(parents=True)
    stdlib.write_text("# standard library marker\n", encoding="utf-8")
    return binary


def test_darwin_bootstrap_failure_leaves_base_untouched(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)

    def failing_run(argv):
        raise SandboxPreparationError("uv download timed out; invalid tar")

    monkeypatch.setattr(runtime, "_run", failing_run)
    with pytest.raises(SandboxPreparationError):
        runtime._ensure_darwin()
    assert not runtime.base.exists()
    assert not (runtime.root / "base.partial").exists()
    assert not (runtime.root / "base.previous").exists()


def test_darwin_bootstrap_swaps_staging_in_after_verification(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    runtime.base.mkdir(parents=True)
    stale = runtime.base / "cpython-3.12-broken"
    stale.mkdir()
    venv_calls: list[list[str]] = []

    def run(argv):
        arguments = [str(item) for item in argv]
        if "python" in arguments and "--install-dir" in arguments:
            install_dir = Path(arguments[arguments.index("--install-dir") + 1])
            fake_python(install_dir, "fresh")
        if "venv" in arguments:
            venv_calls.append(arguments)
            runtime.python.parent.mkdir(parents=True)
            runtime.python.write_text("#!/bin/sh\n", encoding="utf-8")
            (runtime.venv / "pyvenv.cfg").write_text("new", encoding="utf-8")

    monkeypatch.setattr(runtime, "_run", run)
    runtime._ensure_darwin()
    # The verified staging tree replaced the stale partial base atomically.
    assert "cpython-3.12-fresh" in {path.name for path in runtime.base.iterdir()}
    assert not stale.exists()
    assert not (runtime.root / "base.partial").exists()
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "darwin-bootstrap.commit").exists()
    assert runtime._darwin_base_python() is not None
    # The venv must be created from the interpreter's final path below base:
    # uv records that absolute path inside the venv, so a pre-swap staging
    # path would brick every later invocation.
    assert len(venv_calls) == 1
    interpreter = venv_calls[0][venv_calls[0].index("--python") + 1]
    assert interpreter.startswith(str(runtime.base))
    assert "base.partial" not in interpreter


def test_darwin_bootstrap_restores_old_pair_if_base_swap_fails(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    old_python = fake_python(runtime.base, "old")
    runtime.venv.mkdir(parents=True)
    (runtime.venv / "pyvenv.cfg").write_text("old", encoding="utf-8")

    def run(argv):
        arguments = [str(item) for item in argv]
        if "--install-dir" in arguments:
            fake_python(Path(arguments[arguments.index("--install-dir") + 1]), "new")

    original_rename = Path.rename

    def rename(path, target):
        if path == runtime.root / "base.partial" and target == runtime.base:
            raise OSError("simulated base swap failure")
        return original_rename(path, target)

    monkeypatch.setattr(runtime, "_run", run)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="simulated base swap failure"):
        runtime._ensure_darwin()
    assert old_python.is_file()
    assert (runtime.venv / "pyvenv.cfg").read_text(encoding="utf-8") == "old"
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "venv.previous").exists()
    assert not (runtime.root / "base.partial").exists()


def test_darwin_bootstrap_restores_old_pair_if_venv_creation_fails(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    old_python = fake_python(runtime.base, "old")
    runtime.venv.mkdir(parents=True)
    (runtime.venv / "pyvenv.cfg").write_text("old", encoding="utf-8")

    def run(argv):
        arguments = [str(item) for item in argv]
        if "--install-dir" in arguments:
            fake_python(Path(arguments[arguments.index("--install-dir") + 1]), "new")
        if "venv" in arguments:
            runtime.venv.mkdir(parents=True)
            (runtime.venv / "partial").write_text("incomplete", encoding="utf-8")
            raise SandboxPreparationError("simulated venv failure")

    monkeypatch.setattr(runtime, "_run", run)
    with pytest.raises(SandboxPreparationError, match="simulated venv failure"):
        runtime._ensure_darwin()
    assert old_python.is_file()
    assert (runtime.venv / "pyvenv.cfg").read_text(encoding="utf-8") == "old"
    assert not (runtime.venv / "partial").exists()
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "venv.previous").exists()


def test_darwin_bootstrap_rejects_incomplete_venv(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    old_python = fake_python(runtime.base, "old")
    runtime.venv.mkdir(parents=True)
    (runtime.venv / "pyvenv.cfg").write_text("old", encoding="utf-8")

    def run(argv):
        arguments = [str(item) for item in argv]
        if "--install-dir" in arguments:
            fake_python(Path(arguments[arguments.index("--install-dir") + 1]), "new")
        if "venv" in arguments:
            runtime.venv.mkdir(parents=True)
            (runtime.venv / "pyvenv.cfg").write_text("incomplete", encoding="utf-8")

    monkeypatch.setattr(runtime, "_run", run)
    with pytest.raises(SandboxPreparationError, match="usable macOS Python venv"):
        runtime._ensure_darwin()
    assert old_python.is_file()
    assert (runtime.venv / "pyvenv.cfg").read_text(encoding="utf-8") == "old"
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "venv.previous").exists()


def test_darwin_bootstrap_finishes_interrupted_commit_without_restoring_old_pair(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    new_python = fake_python(runtime.base, "new")
    runtime.venv.mkdir(parents=True)
    (runtime.venv / "pyvenv.cfg").write_text("new", encoding="utf-8")
    runtime.python.parent.mkdir(parents=True)
    runtime.python.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_python(runtime.root / "base.previous", "old")
    (runtime.root / "venv.previous").mkdir()
    (runtime.root / "darwin-bootstrap.commit").write_text("ready", encoding="utf-8")

    def failing_run(argv):
        raise SandboxPreparationError("simulated later download failure")

    monkeypatch.setattr(runtime, "_run", failing_run)
    with pytest.raises(SandboxPreparationError, match="later download failure"):
        runtime._ensure_darwin()
    assert new_python.is_file()
    assert (runtime.venv / "pyvenv.cfg").read_text(encoding="utf-8") == "new"
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "venv.previous").exists()
    assert not (runtime.root / "darwin-bootstrap.commit").exists()


def test_darwin_bootstrap_rolls_back_incomplete_committed_pair(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    fake_python(runtime.base, "new")
    runtime.venv.mkdir(parents=True)
    (runtime.venv / "pyvenv.cfg").write_text("incomplete", encoding="utf-8")
    old_python = fake_python(runtime.root / "base.previous", "old")
    (runtime.root / "venv.previous").mkdir()
    (runtime.root / "venv.previous" / "pyvenv.cfg").write_text("old", encoding="utf-8")
    (runtime.root / "darwin-bootstrap.commit").write_text("ready", encoding="utf-8")

    def failing_run(argv):
        raise SandboxPreparationError("simulated later download failure")

    monkeypatch.setattr(runtime, "_run", failing_run)
    with pytest.raises(SandboxPreparationError, match="later download failure"):
        runtime._ensure_darwin()
    assert (runtime.base / old_python.relative_to(runtime.root / "base.previous")).is_file()
    assert (runtime.venv / "pyvenv.cfg").read_text(encoding="utf-8") == "old"
    assert not (runtime.root / "darwin-bootstrap.commit").exists()


def test_darwin_ready_fast_path_cleans_interrupted_commit(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    fake_python(runtime.base, "new")
    runtime.python.parent.mkdir(parents=True)
    runtime.python.write_text("#!/bin/sh\n", encoding="utf-8")
    (runtime.venv / "pyvenv.cfg").write_text("new", encoding="utf-8")
    fake_python(runtime.root / "base.previous", "old")
    (runtime.root / "venv.previous").mkdir()
    (runtime.root / "darwin-bootstrap.commit").write_text("ready", encoding="utf-8")
    (runtime.root / "ready.json").write_text(json.dumps({
        "darwin_runtime_version": python_runtime.DARWIN_RUNTIME_VERSION,
        "launcher_version": python_runtime.LAUNCHER_VERSION,
    }), encoding="utf-8")
    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime, "_repair_launchers", lambda: None)
    monkeypatch.setattr(runtime, "_run", lambda _: (_ for _ in ()).throw(
        AssertionError("an interrupted cleanup must not redownload Python")))

    runtime.prepare_sync()
    assert not (runtime.root / "base.previous").exists()
    assert not (runtime.root / "venv.previous").exists()
    assert not (runtime.root / "darwin-bootstrap.commit").exists()
