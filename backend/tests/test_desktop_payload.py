"""Desktop build ownership and payload boundaries, using temporary files only."""
import importlib.util
import json
from pathlib import Path

import pytest


source = Path(__file__).resolve().parents[2] / 'scripts/package-backend.py'
spec = importlib.util.spec_from_file_location('package_backend', source)
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def test_payload_publication_reclaims_only_its_previous_output(tmp_path):
    destination = tmp_path / 'desktop-payload'
    destination.mkdir()
    (destination / 'version').write_text('old')
    untracked = tmp_path / 'desktop-previous-0000000000'
    untracked.mkdir()
    with packaging.payload_stage(tmp_path) as stage:
        (stage / 'version').write_text('new')
        assert (destination / 'version').read_text() == 'old'
    assert (destination / 'version').read_text() == 'new'
    assert untracked.exists()
    assert not (tmp_path / 'desktop-build.json').exists()
    assert not list(tmp_path.glob('desktop-staging-*'))


@pytest.mark.parametrize('published', [False, True])
def test_failed_and_interrupted_build_preserves_previous_usable_payload(tmp_path, published):
    previous = tmp_path / 'desktop-previous-1111111111'
    previous.mkdir()
    (previous / 'version').write_text('usable')
    abandoned = tmp_path / 'desktop-staging-2222222222'
    abandoned.mkdir()
    (abandoned / 'partial').touch()
    if published:
        destination = tmp_path / 'desktop-payload'
        destination.mkdir()
        (destination / 'version').write_text('new')
    expected = 'new' if published else 'usable'
    (tmp_path / 'desktop-build.json').write_text(json.dumps({'stage': abandoned.name, 'previous': previous.name}))
    with pytest.raises(RuntimeError, match='build failed'):
        with packaging.payload_stage(tmp_path) as stage:
            assert not abandoned.exists()
            assert (tmp_path / 'desktop-payload/version').read_text() == expected
            (stage / 'partial').touch()
            raise RuntimeError('build failed')
    assert (tmp_path / 'desktop-payload/version').read_text() == expected
    assert not list(tmp_path.glob('desktop-staging-*'))


def test_payload_recovery_rejects_unowned_paths(tmp_path):
    (tmp_path / 'desktop-build.json').write_text(json.dumps({'stage': '../user-data', 'previous': 'desktop-previous-1111111111'}))
    with pytest.raises(RuntimeError, match='Invalid desktop build recovery path'):
        with packaging.payload_stage(tmp_path):
            pytest.fail('unowned recovery path accepted')


def test_payload_omits_reference_papers_and_keeps_runtime_models_and_licenses():
    prefix = Path('plugins/xrd/src/oaw_xrd/OAW_XRDfit')
    assert not packaging.runtime_source(prefix / 'src/refs/2602.16372v2.pdf')
    assert packaging.runtime_source(prefix / 'LICENSE')
    assert packaging.runtime_source(prefix / 'src/Extinction/m3gnet/models/MP-2021.2.8-EFS/m3gnet.data-00000-of-00001')
    assert packaging.runtime_source(prefix / 'requirements.lock')
    assert packaging.runtime_source(Path('plugins/office/templates/user-guide.pdf'))
