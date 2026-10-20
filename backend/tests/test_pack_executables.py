import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from backend.packs.executables import ExecutableEnvironmentManager, ExecutableEnvironmentSpec, target_platform
from backend.packs.spec import EnvironmentSpec, ExecutableArtifact


def fixture_spec(tmp_path, *, archive='raw', member=None):
    binary = Path(os.environ['COMSPEC']) if os.name == 'nt' else Path('/bin/sh')
    name = 'fixture.exe' if os.name == 'nt' else 'fixture'
    artifact = tmp_path / ('fixture.zip' if archive == 'zip' else 'fixture.bin')
    if archive == 'zip':
        with zipfile.ZipFile(artifact, 'w') as stream:
            stream.write(binary, member or name)
    else:
        shutil.copyfile(binary, artifact)
    declaration = EnvironmentSpec(target='host-worker', provider='executable', artifacts=(ExecutableArtifact(
        platform=target_platform(), path=artifact.name, sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        executable=name, archive=archive, size_bytes=artifact.stat().st_size),),
        verify_argv=('/d', '/c', 'echo fixture-ready') if os.name == 'nt' else ('-c', 'printf fixture-ready'),
        verify_pattern='fixture-ready')
    return ExecutableEnvironmentSpec.from_declaration('fixture', 'native', declaration, tmp_path), declaration


@pytest.mark.parametrize('archive', ['raw', 'zip'])
def test_real_portable_fixture_install_verify_and_tamper(tmp_path, archive):
    spec, _ = fixture_spec(tmp_path, archive=archive)
    manager = ExecutableEnvironmentManager(tmp_path / 'profile')
    handle = manager.prepare(spec)
    assert handle.management == 'managed'
    assert manager.status(spec)['status'] == 'ready'
    assert manager.prepare(spec) == handle
    result = subprocess.run([handle.executable, *spec.verify_argv], capture_output=True, check=True)
    assert b'fixture-ready' in result.stdout
    Path(handle.executable).write_bytes(b'changed')
    assert manager.status(spec)['status'] == 'failed'
    repaired = manager.prepare(spec)
    assert repaired.operation_id == handle.operation_id
    assert manager.status(spec)['status'] == 'ready'


@pytest.mark.parametrize('member', ['../outside.exe', 'ready.json', 'CON.exe', 'folder/../../bad', 'trailing.'])
def test_archive_paths_rejected(tmp_path, member):
    spec, _ = fixture_spec(tmp_path, archive='zip', member=member)
    with pytest.raises(ValueError):
        ExecutableEnvironmentManager(tmp_path / 'profile').prepare(spec)
    assert not (tmp_path / 'outside.exe').exists()


def test_checksum_is_verified_before_execution(tmp_path):
    _, declaration = fixture_spec(tmp_path)
    declaration = declaration.model_copy(update={'artifacts': (declaration.artifacts[0].model_copy(update={'sha256': '0' * 64}),)})
    spec = ExecutableEnvironmentSpec.from_declaration('fixture', 'native', declaration, tmp_path)
    manager = ExecutableEnvironmentManager(tmp_path / 'profile')
    with pytest.raises(ValueError, match='checksum'):
        manager.prepare(spec)
    assert manager.status(spec)['status'] == 'failed'


def test_external_adoption_is_explicit_owned_and_readonly_status(tmp_path, monkeypatch):
    declaration = EnvironmentSpec(target='host-worker', provider='executable', accept_external=True,
        executable_names=('fixture',), verify_argv=('-I', '-c', 'print("external-ready")'), verify_pattern='external-ready')
    spec = ExecutableEnvironmentSpec.from_declaration('fixture', 'native', declaration, tmp_path)
    monkeypatch.setattr(shutil, 'which', lambda name: sys.executable)
    manager = ExecutableEnvironmentManager(tmp_path / 'profile')
    assert manager.status(spec)['status'] == 'missing'
    assert not manager.root.exists()
    handle = manager.prepare(spec)
    assert handle.management == 'external'
    assert not (manager.root / 'environments').exists()
    monkeypatch.setattr(manager, '_probe', lambda *args: pytest.fail('status ran the program'))
    assert manager.status(spec)['status'] == 'ready'
    assert manager.read_external(spec) == handle


def test_source_archive_and_cross_owner_share_content_identity(tmp_path):
    spec, declaration = fixture_spec(tmp_path)
    archive_declaration = declaration.model_copy(update={'artifacts': (declaration.artifacts[0].model_copy(update={'path': 'runtime/fixture.bin'}),)})
    other = ExecutableEnvironmentSpec.from_declaration('another-owner', 'renamed', archive_declaration, tmp_path / 'archive')
    assert other.fingerprint == spec.fingerprint


def test_crash_receipt_readonly_failed_then_same_operation_retry(tmp_path):
    spec, _ = fixture_spec(tmp_path)
    manager = ExecutableEnvironmentManager(tmp_path / 'profile')
    receipt = manager.root / 'operations' / (spec.fingerprint + '.json')
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'status': 'preparing', 'operation_id': 'previous-operation', 'stage': 'preserved'}), encoding='utf-8')
    original = receipt.read_bytes()
    assert manager.status(spec)['status'] == 'failed'
    assert receipt.read_bytes() == original
    assert manager.prepare(spec).operation_id == 'previous-operation'


def test_verified_stage_recovers_without_artifact_download(tmp_path):
    spec, _ = fixture_spec(tmp_path)
    manager = ExecutableEnvironmentManager(tmp_path / 'profile')
    handle = manager.prepare(spec)
    operation_file = manager._operation(spec)
    operation = json.loads(operation_file.read_text(encoding='utf-8'))
    stage = manager.root / 'staging' / operation['stage']
    handle.root.rename(stage / 'payload')
    operation['status'] = 'preparing'
    operation_file.write_text(json.dumps(operation), encoding='utf-8')
    (tmp_path / 'fixture.bin').unlink()
    assert manager.prepare(spec).operation_id == handle.operation_id
