"""Real private workers and durable environment lifecycle, without network."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import zipfile

import pytest

from backend.packs import environments as runtime


def spec(**changes):
    return runtime.PythonEnvironmentSpec("fixture", "worker", **changes)


def test_stdlib_worker_prepare_reuses_offline_and_never_changes_host(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec(verify_imports=("json",))
    before = list(sys.path)
    handle = manager.prepare_python(declaration)
    again = manager.prepare_python(declaration)
    assert again == handle
    assert handle.root.is_relative_to(tmp_path / "runtime/environments")
    assert manager.status(declaration)["status"] == "ready"
    assert sys.path == before
    assert handle.python == sys.executable


def test_local_wheel_runs_only_in_private_worker_and_detects_missing_files(tmp_path, monkeypatch):
    if not shutil.which("uv"):
        pytest.skip("uv is required for the local-wheel provider integration")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    wheel = tmp_path / "oaw_env_fixture-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("oaw_env_fixture/__init__.py", "value = 'private-wheel'\n")
        archive.writestr("oaw_env_fixture-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: oaw-env-fixture\nVersion: 1.0\n")
        archive.writestr("oaw_env_fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("oaw_env_fixture-1.0.dist-info/RECORD", "")
    declaration = spec(requirements=(str(wheel),), verify_imports=("oaw_env_fixture",))
    manager = runtime.PackEnvironmentManager(tmp_path / "profile")
    handle = manager.prepare_python(declaration)
    probe = "import json,site,sys;site.addsitedir(sys.argv[1]);import oaw_env_fixture;print(json.dumps([oaw_env_fixture.value,sys.path]))"
    value, paths = json.loads(subprocess.check_output([handle.python, "-I", "-S", "-c", probe,
                                                      str(handle.root / "packages")], text=True))
    assert value == "private-wheel"
    assert not any("site-packages" in path for path in paths)
    assert "oaw_env_fixture" not in sys.modules
    (handle.root / "packages/oaw_env_fixture/__init__.py").unlink()
    assert manager.status(declaration)["status"] == "invalid"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows MAX_PATH regression")
def test_private_imports_and_cache_support_long_staging_paths_and_publish(tmp_path, monkeypatch):
    if not shutil.which("uv"):
        pytest.skip("uv is required for the local-wheel provider integration")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.delenv("CUSTOM_TIKTOKEN_CACHE_DIR", raising=False)
    # Reproduce LiteLLM's import-time cache selection and tiktoken's atomic
    # write, without downloading tokenizer data or installing dependencies.
    source = '''
import os
from pathlib import Path
from uuid import uuid4
cache = Path(os.environ.get("CUSTOM_TIKTOKEN_CACHE_DIR",
    str(Path(__file__).parent / "litellm_core_utils/tokenizers")))
cache.mkdir(parents=True, exist_ok=True)
target = cache / ("9b5ad71b2ce5302211f9c61530b329a4922fc6a4")
temporary = target.with_name(target.name + "." + str(uuid4()) + ".tmp")
temporary.write_bytes(b"tokenizer")
temporary.replace(target)
from .llms.anthropic.experimental_pass_through.context_management.editors import clear_tool_uses
'''
    wheel = tmp_path / "litellm-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("litellm/__init__.py", source)
        parts = "litellm/llms/anthropic/experimental_pass_through/context_management/editors".split("/")
        for length in range(2, len(parts) + 1):
            archive.writestr("/".join(parts[:length]) + "/__init__.py", "")
        archive.writestr("/".join(parts) + "/clear_tool_uses.py", "value = 'private'\n")
        archive.writestr("litellm-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: litellm\nVersion: 1.0\n")
        archive.writestr("litellm-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("litellm-1.0.dist-info/RECORD", "")
    declaration = spec(requirements=(str(wheel),), verify_imports=("litellm",))
    manager = runtime.PackEnvironmentManager(tmp_path / "profile")
    handle = manager.prepare_python(declaration)
    stage = manager.root / "staging" / json.loads(manager._operation_path(declaration).read_text())["stage"]
    assert len(str(stage / "packages/litellm/litellm_core_utils/tokenizers")) + 82 > 260
    assert manager.prepare_python(declaration) == handle
    receipt = json.loads((handle.root / "ready.json").read_text())
    assert receipt["verified_files"][0]["path"] == "packages/litellm/__init__.py"


def test_failure_preserves_previous_environment_and_retry_operation_identity(tmp_path):
    manager = runtime.PackEnvironmentManager(tmp_path)
    previous = spec(verify_imports=("json",))
    old = manager.prepare_python(previous)
    declaration = spec(verify_imports=("pathlib",))

    def fail(_):
        raise RuntimeError("verification failed")

    with pytest.raises(RuntimeError, match="verification failed"):
        manager.prepare_python(declaration, verify=fail)
    operation = manager.status(declaration)
    assert operation["status"] == "failed"
    assert not manager.environment_root(declaration).exists()
    assert manager.read_python(previous) == old
    current = manager.prepare_python(declaration)
    assert current.operation_id == operation["operation_id"]
    assert manager.read_python(previous) == old


def test_concurrent_prepare_publishes_one_operation(tmp_path):
    entered, release = threading.Event(), threading.Event()
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    calls = []

    def slow(handle):
        calls.append(handle)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.prepare_python, declaration, verify=slow)
        assert entered.wait(10)
        second = pool.submit(manager.prepare_python, declaration)
        release.set()
        assert first.result(timeout=20) == second.result(timeout=20)
    assert len(list((tmp_path / "runtime/environments").iterdir())) == 1


def test_ready_stage_after_interrupted_publish_is_reused_without_install(tmp_path, monkeypatch):
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    destination = manager.environment_root(declaration)
    rename = Path.rename

    def interrupted(path, target):
        if target == destination:
            raise OSError("interrupted before publish")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        manager.prepare_python(declaration)
    failed = manager.status(declaration)
    stage = manager.root / "staging" / failed["stage"]
    assert (stage / "ready.json").is_file()
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: pytest.fail("recovery must reuse verified stage"))
    handle = manager.prepare_python(declaration)
    assert handle.operation_id == failed["operation_id"]


def test_published_receipt_reconciles_missing_operation_and_relocates(tmp_path):
    manager = runtime.PackEnvironmentManager(tmp_path / "before")
    declaration = spec()
    handle = manager.prepare_python(declaration)
    manager._operation_path(declaration).unlink()
    target = tmp_path / "after"
    (tmp_path / "before").rename(target)
    moved = runtime.PackEnvironmentManager(target)
    current = moved.prepare_python(declaration)
    assert current.operation_id == handle.operation_id
    assert current.root.is_relative_to(target)
    assert moved.status(declaration)["status"] == "ready"


def test_receipt_cannot_reference_interpreter_outside_environment(tmp_path):
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    handle = manager.prepare_python(declaration)
    path = handle.root / "ready.json"
    receipt = json.loads(path.read_text())
    receipt["python"] = "../../outside/python.exe"
    path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError, match="escapes environment"):
        manager.read_python(declaration)


def test_fingerprint_changes_with_dependency_verifier_abi_and_not_profile(tmp_path, monkeypatch):
    verifier = tmp_path / "verify.py"
    verifier.write_text("print('one')")
    declaration = spec(verify_script=verifier)
    before = declaration.fingerprint
    verifier.write_text("print('two')")
    assert declaration.fingerprint != before
    assert spec(requirements=("example==1",)).fingerprint != spec(requirements=("example==2",)).fingerprint
    old = spec().fingerprint
    monkeypatch.setattr(runtime.sys.implementation, "cache_tag", "other-abi")
    assert spec().fingerprint != old


def test_incompatible_host_uses_private_python_and_relative_relocatable_receipt(tmp_path, monkeypatch):
    calls = []
    selected = []

    def runner(args, *, env):
        calls.append(list(map(str, args)))
        if args[1:3] == ["python", "install"]:
            python = Path(env["UV_PYTHON_INSTALL_DIR"]) / "cpython-3.12" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            selected.append(python)
        elif "-c" in args:
            Path(args[-1]).write_text("[]")

    monkeypatch.setattr(runtime.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runtime.subprocess, "check_output", lambda *a, **k: str(selected[-1]))
    manager = runtime.PackEnvironmentManager(tmp_path / "before", runner=runner)
    declaration = spec(requires_python=">=3.12")
    handle = manager.prepare_python(declaration)
    receipt = json.loads((handle.root / "ready.json").read_text())
    assert receipt["python"] == "python/cpython-3.12/python.exe"
    assert "--no-bin" in calls[0]
    (tmp_path / "before").rename(tmp_path / "after")
    moved = runtime.PackEnvironmentManager(tmp_path / "after").read_python(declaration)
    assert Path(moved.python).is_relative_to(moved.root)
    assert Path(moved.python).is_file()


def test_static_declaration_and_matcreator_launcher_have_identical_identity():
    from backend.packs.spec import read_source_pack
    from plugins.matcreator.oaw_matcreator import native_environment
    root = native_environment.PACKAGE.parent
    source = read_source_pack(root)
    declared = runtime.PythonEnvironmentSpec.from_declaration(source.owner, "native", source.environments["native"], root)
    assert declared.fingerprint == native_environment.environment_spec().fingerprint


def test_status_observes_real_process_lock_and_crash_without_changing_receipts(tmp_path):
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    script = """
import os, sys
from pathlib import Path
from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec, installation_lock, _json_write
manager = PackEnvironmentManager(Path(sys.argv[1]))
spec = PythonEnvironmentSpec('fixture', 'worker')
with installation_lock(manager.root / 'locks' / (spec.fingerprint + '.lock')):
    stage = manager.root / 'staging' / 'interrupted-stage'
    stage.mkdir(parents=True)
    (stage / 'partial.txt').write_text('preserve for recovery')
    _json_write(manager._operation_path(spec), {'status': 'preparing', 'operation_id': 'same-operation',
        'fingerprint': spec.fingerprint, 'stage': stage.name})
    print('locked', flush=True)
    sys.stdin.readline()
    os._exit(9)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(process.stdout.readline).result(timeout=10).strip() == "locked"
        operation_path = manager._operation_path(declaration)
        lock_path = manager.root / "locks" / (declaration.fingerprint + ".lock")
        stage_file = manager.root / "staging/interrupted-stage/partial.txt"
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (operation_path, stage_file)}
        lock_mtime = lock_path.stat().st_mtime_ns
        assert manager.status(declaration)["status"] == "preparing"
        process.stdin.write("exit\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 9
        status = manager.status(declaration)
        assert status["status"] == "failed"
        assert status["error_code"] == "interrupted"
        assert status["operation_id"] == "same-operation"
        assert lock_path.stat().st_mtime_ns == lock_mtime
        assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in before.items())
        handle = manager.prepare_python(declaration)
        assert handle.operation_id == "same-operation"
        assert stage_file.read_text() == "preserve for recovery"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_stale_preparing_without_lock_is_read_only_and_does_not_create_lock(tmp_path):
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    path = manager._operation_path(declaration)
    runtime._json_write(path, {"status": "preparing", "operation_id": "stale"})
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert manager.status(declaration)["error_code"] == "interrupted"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (manager.root / "locks").exists()


def test_unreadable_preparation_lock_does_not_claim_liveness(tmp_path, monkeypatch):
    manager = runtime.PackEnvironmentManager(tmp_path)
    declaration = spec()
    runtime._json_write(manager._operation_path(declaration), {"status": "preparing", "operation_id": "unknown"})

    def unavailable(_):
        raise PermissionError("cannot inspect")

    monkeypatch.setattr(runtime, "installation_is_active", unavailable)
    assert manager.status(declaration)["status"] == "failed"
    assert manager.status(declaration)["error_code"] == "status_unavailable"


def legacy_fixture(tmp_path, monkeypatch, *, child_version="2", child_present=True):
    monkeypatch.setattr(runtime.sys, "prefix", str(tmp_path / "host"))
    seed = Path(sys.prefix) / "oaw-runtimes/fixture/old-version"
    packages = seed / "packages"
    packages.mkdir(parents=True)
    (packages / "legacy_fixture.py").write_text("value = 1\n")
    root_info = packages / "legacy_fixture-1.dist-info"
    root_info.mkdir()
    (root_info / "METADATA").write_text(
        'Metadata-Version: 2.1\nName: legacy-fixture\nVersion: 1\n'
        'Requires-Dist: seed-child==2; extra == "worker"\n')
    if child_present:
        child_info = packages / f"seed_child-{child_version}.dist-info"
        child_info.mkdir()
        (child_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: seed-child\nVersion: {child_version}\n")
    (seed / "ready.json").write_text(json.dumps({"format": 2, "python": "host"}))
    return seed, spec(requirements=("legacy-fixture[worker]==1",), verify_imports=("legacy_fixture",))


def test_declared_preparation_discovers_legacy_and_copies_without_network(tmp_path, monkeypatch):
    from backend.packs.planning import prepare_declared
    from backend.packs.spec import EnvironmentSpec
    seed, declaration = legacy_fixture(tmp_path, monkeypatch)
    before = {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*") if p.is_file()}
    monkeypatch.setattr(runtime.shutil, "which", lambda _: pytest.fail("Verified migration must not download"))
    handle = prepare_declared("fixture", "worker", EnvironmentSpec(target="host-worker",
        requires_python=declaration.requires_python, python=declaration.requirements,
        verify_imports=declaration.verify_imports), tmp_path, tmp_path / "profile")
    assert handle.root != seed
    assert (handle.root / "packages/legacy_fixture.py").read_text() == "value = 1\n"
    assert {p.relative_to(seed): p.read_bytes() for p in seed.rglob("*") if p.is_file()} == before
    manager = runtime.PackEnvironmentManager(tmp_path / "profile")
    assert manager.status(declaration)["status"] == "ready"


@pytest.mark.parametrize("child_version,child_present", [("1", True), ("2", False)])
def test_legacy_seed_rejects_wrong_or_missing_transitive_extra(tmp_path, monkeypatch, child_version, child_present):
    seed, declaration = legacy_fixture(tmp_path, monkeypatch, child_version=child_version, child_present=child_present)
    with pytest.raises(ValueError, match="Legacy dependency"):
        runtime.PackEnvironmentManager._verify_legacy_requirements(declaration, seed, "host")
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "uv")
    def runner(args, *, env):
        assert args[1:3] == ["pip", "install"]
        raise RuntimeError("needs download")
    with pytest.raises(RuntimeError, match="needs download"):
        runtime.PackEnvironmentManager(tmp_path / "profile", runner=runner).prepare_python(declaration)


def test_preparation_automatically_reuses_verified_application_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, "prefix", str(tmp_path / "host"))
    declaration = spec(verify_imports=("json",))
    bundled = runtime.PackEnvironmentManager(Path(sys.prefix) / "oaw-runtime-bundle").prepare_python(declaration)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: pytest.fail("Bundle reuse must not install"))
    current = runtime.PackEnvironmentManager(tmp_path / "profile").prepare_python(declaration)
    assert current.root != bundled.root
    assert bundled.root.is_dir()


def test_preparation_preserves_configured_uv_download_cache(tmp_path, monkeypatch):
    cache = tmp_path / "shared-download-cache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "uv")
    def runner(args, *, env):
        assert env["UV_CACHE_DIR"] == str(cache)
        if "-c" in args:
            Path(args[-1]).write_text("[]")
    runtime.PackEnvironmentManager(tmp_path / "profile", runner=runner).prepare_python(spec(requirements=("fixture==1",)))


def test_progress_reports_wait_and_stops_after_failure(capsys):
    import time
    with pytest.raises(RuntimeError):
        with runtime.preparation_progress("Fixture step", interval=.01):
            time.sleep(.05)
            raise RuntimeError("failure")
    output = capsys.readouterr().out
    assert "Fixture step..." in output
    assert "still working" in output
    assert "failed" in output
    time.sleep(.03)
    assert capsys.readouterr().out == ""
