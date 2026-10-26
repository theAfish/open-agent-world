"""Installed Schema 3 behavior, independent of the IDE's rendered preview."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

from backend.packs.archive import inspect_archive
from backend.packs.build import build_project
from backend.packs.installation import PackInstallationManager
from backend.packs.projects import read_file, scaffold, write_file
from backend.plugins.loader import create_builtin_registry, load_installed_packs
from backend.tests.test_pack_build_project import zip_bytes


EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/packs/analysis-card'


def test_mixed_project_visual_edit_preserves_python_and_runs_installed_action(tmp_path):
    root = tmp_path / 'external analysis'
    shutil.copytree(EXAMPLE, root)
    source_path = root / 'src/oaw_example_analysis_card/__init__.py'
    source = source_path.read_bytes()
    before = hashlib.sha256(source).hexdigest()
    design = read_file(root, 'designs/card.json')
    value = json.loads(design['content'])
    value['face']['title'] = 'My cell analyzer'
    write_file(root, 'designs/card.json', json.dumps(value), design['sha256'])

    # A conventional prebuilt wheel keeps this integration test offline. The
    # repository example also builds with the real Hatch backend (see its README).
    prefix = 'oaw_pack_example_analysis_card-0.1.0.dist-info'
    wheel = tmp_path / 'analysis.whl'
    wheel.write_bytes(zip_bytes({
        'oaw_example_analysis_card/__init__.py': source,
        f'{prefix}/METADATA': ('Metadata-Version: 2.1\nName: oaw-pack-example-analysis-card\n'
                             'Version: 0.1.0\nRequires-Dist: pydantic>=2.11,<3\n'),
        f'{prefix}/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        f'{prefix}/entry_points.txt': ('[open_agent_world.plugins]\n'
                                     'example.analysis-card = oaw_example_analysis_card:create_plugin\n'),
        f'{prefix}/RECORD': '',
    }))
    artifact = build_project(root, tmp_path / 'analysis.oawpack', backend_wheel=wheel)
    archive = inspect_archive(artifact.read_bytes())
    assert archive.manifest.schema_version == 3
    with ZipFile(io.BytesIO(archive.files['backend/adapter.whl'])) as packed:
        assert packed.read('oaw_example_analysis_card/__init__.py') == source
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before
    assert 'oaw_example_analysis_card' not in sys.modules

    profile = tmp_path / 'clean profile'
    registry = create_builtin_registry()
    manager = PackInstallationManager(profile, registry)
    assert manager.plan(artifact.read_bytes())['restart_required']
    manager.install(artifact.read_bytes())
    load_installed_packs(registry, profile, safe_only=True)
    assert not registry.has_plugin('example.analysis-card')

    # This fresh process imports only the installed wheel, exercises the ordinary
    # HTTP resource endpoint and proves that visual metadata survived packaging.
    result = subprocess.run([sys.executable, '-c', '''
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.plugins.loader import create_builtin_registry, load_installed_packs
from backend.services import create_services
settings = Settings.for_data_root(Path(sys.argv[1]))
registry = create_builtin_registry()
load_installed_packs(registry, settings.data_root, strict=True)
definition = registry.node_type('example.analysis-card')
assert definition.authoring['face']['title'] == 'My cell analyzer'
services = create_services(settings, plugins=registry)
with TestClient(create_app(settings, services=services), client=('127.0.0.1', 50000)) as client:
    node = client.post('/api/nodes', json={'type': 'example.analysis-card'})
    assert node.status_code == 201, node.text
    url = '/api/nodes/' + node.json()['id'] + '/resource/analyze_structure'
    response = client.post(url, json={'arguments': {'a': 3, 'b': 5, 'c': 7}})
    assert response.status_code == 200, response.text
    assert response.json()['volume'] == 105
    invalid = client.post(url, json={'arguments': {'a': -1, 'b': 5, 'c': 7}})
    assert invalid.status_code == 422, invalid.text
    print(json.dumps(response.json()))
''', str(profile)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['result'] == 'Orthogonal cell volume: 105 A^3'


def test_visual_project_builds_and_executes_without_author_python(tmp_path):
    root = tmp_path / 'visual project'
    scaffold(root, 'Visual greeting', 'example.visual-greeting')
    artifact = build_project(root, tmp_path / 'visual.oawpack')
    profile = tmp_path / 'clean visual profile'
    registry = create_builtin_registry()
    manager = PackInstallationManager(profile, registry)
    assert not manager.plan(artifact.read_bytes())['restart_required']
    manager.install(artifact.read_bytes())
    load_installed_packs(registry, profile, safe_only=True)
    definition = registry.node_type('example.visual-greeting.card')
    from backend.config import Settings
    from backend.main import create_app
    from backend.services import create_services
    from fastapi.testclient import TestClient
    settings = Settings.for_data_root(profile)
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        node = client.post('/api/nodes', json={'type': definition.id})
        assert node.status_code == 201, node.text
        response = client.post(f"/api/nodes/{node.json()['id']}/resource/run", json={'arguments': {'name': 'Schema 3'}})
        assert response.status_code == 200, response.text
        assert 'Schema 3' in response.json()['result']

