"""Source editing boundaries and non-executing IDE operations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from backend.errors import ConflictError, NotFoundError
from backend.packs.archive import inspect_archive
from backend.packs.projects import PackProjects, inspect_project, read_file, write_file
from backend.persistence.database import Database
from backend.tests.test_skill_runtime import runtime_client  # noqa: F401 - shared fixture


@pytest.fixture
def projects(tmp_path):
    database = Database(tmp_path / 'profile/world.sqlite3')
    try:
        yield PackProjects(tmp_path / 'profile', database)
    finally:
        database.close()


def test_visual_project_source_roundtrip_external_conflict_and_schema3_build(projects):
    snapshot = projects.create('Calculator', 'test.ide-calculator')
    root = Path(snapshot['path'])
    card = snapshot['cards'][0]
    design = card['design']
    design['function'] = {'fields': [{'key': 'x', 'label': 'X', 'type': 'number', 'default': 2},
                                     {'key': 'y', 'label': 'Y', 'type': 'number', 'default': 3}], 'operation': 'sum'}
    saved = projects.save_design(snapshot['id'], card['id'], design, snapshot['revision'])
    assert saved['revision'] != snapshot['revision']
    source = read_file(root, card['design_path'])
    assert json.loads(source['content'])['function']['operation'] == 'sum'
    artifact, filename = projects.build(snapshot['id'], saved['revision'])
    assert filename == 'test.ide-calculator-0.1.0.oawpack'
    assert inspect_archive(artifact).manifest.schema_version == 3
    (root / card['design_path']).write_text(source['content'] + '\n', encoding='utf-8')
    with pytest.raises(ConflictError, match='changed outside'):
        write_file(root, card['design_path'], '{}', source['sha256'])
    with pytest.raises(ConflictError, match='changed outside'):
        projects.build(snapshot['id'], saved['revision'])
    assert json.loads((root / card['design_path']).read_text(encoding='utf-8'))['function']['operation'] == 'sum'
    assert PackProjects(projects.data_root, projects.database).list()['projects'][0]['id'] == snapshot['id']


def test_python_and_custom_frontend_inspection_preserves_code_and_manifest(projects, tmp_path):
    root = tmp_path / 'external'
    shutil.copytree(Path(__file__).resolve().parents[2] / 'examples/packs/greeter', root)
    manifest = root / 'oaw.pack.toml'
    manifest.write_text(manifest.read_text(encoding='utf-8').replace('1.26', '1.39'), encoding='utf-8')
    python = root / 'src/oaw_pack_example_greeter/__init__.py'
    python.write_text("raise RuntimeError('static editing must never import this file')\n" + python.read_text(encoding='utf-8'), encoding='utf-8')
    with python.open('a', encoding='utf-8') as stream:
        stream.write('\nRelationshipDefinition(id="example.connect", label="Connect", source_types=frozenset({"example.greeter.card"}), target_types=frozenset({"example.target"}))\n')
    before = {str(path): path.read_bytes() for path in [manifest, python, root / 'frontend/index.tsx', root / 'pyproject.toml']}
    snapshot = projects.open(str(root))
    assert snapshot['cards'][0]['id'] == 'example.greeter.card'
    assert snapshot['cards'][0]['frontend']['body'] == 'greeting'
    assert snapshot['relationships'][0]['source_types'] == ['example.greeter.card']
    design = {'face': {'title': 'Scientific card'}, 'function': {'fields': [], 'template': ''}, 'action': 'analyze_structure', 'surfaces': ['body']}
    saved = projects.save_design(snapshot['id'], 'example.greeter.card', design, snapshot['revision'])
    assert saved['cards'][0]['design']['action'] == 'analyze_structure'
    assert manifest.read_bytes().startswith(before[str(manifest)].rstrip())
    for path in [python, root / 'frontend/index.tsx', root / 'pyproject.toml']:
        assert path.read_bytes() == before[str(path)]
    with pytest.raises(ValueError, match='Build Python'):
        projects.build(saved['id'], saved['revision'])


def test_attaching_second_design_preserves_existing_table_and_resources(projects):
    snapshot = projects.create('Two cards', 'test.ide-two')
    root = Path(snapshot['path'])
    manifest = root / 'oaw.pack.toml'
    text = manifest.read_text(encoding='utf-8')
    text += '\n# This handwritten card remains intact.\n[[contributions.cards]]\nid = "test.ide-two.other"\nname = "Other"\n'
    manifest.write_text(text, encoding='utf-8')
    first_bytes = (root / 'designs/card.json').read_bytes()
    snapshot = projects.inspect(snapshot['id'])
    saved = projects.save_design(snapshot['id'], 'test.ide-two.other', snapshot['cards'][0]['design'], snapshot['revision'])
    assert len(saved['cards']) == 2
    assert saved['cards'][1]['design_path'].startswith('designs/card-')
    assert (root / 'designs/card.json').read_bytes() == first_bytes
    assert '# This handwritten card remains intact.' in manifest.read_text(encoding='utf-8')


def test_add_card_and_mixed_interface_discovery(projects, tmp_path):
    snapshot = projects.create('First')
    added = projects.add_card(snapshot['id'], 'Second', snapshot['revision'])
    assert len(added['cards']) == 2
    assert added['cards'][1]['design']['face']['title'] == 'Second'
    root = tmp_path / 'mixed'
    shutil.copytree(Path(__file__).resolve().parents[2] / 'examples/packs/analysis-card', root)
    mixed = projects.open(str(root))
    assert mixed['cards'][0]['kind'] == 'python'
    assert mixed['cards'][0]['actions'] == [{'id': 'analyze_structure'}]
    assert mixed['cards'][0]['source_path'] == 'src/oaw_example_analysis_card/__init__.py'


def test_declarative_version_updates_both_declarations_without_touching_design(projects):
    snapshot = projects.create('Release', 'test.ide-release')
    root = Path(snapshot['path'])
    manifest = root / 'oaw.pack.toml'
    manifest.write_text(manifest.read_text(encoding='utf-8').replace('[distribution]', '[distribution]\nversion = "0.1.0" # release'), encoding='utf-8')
    snapshot = projects.inspect(snapshot['id'])
    design = (root / 'designs/card.json').read_bytes()
    saved = projects.set_version(snapshot['id'], '0.2.0', snapshot['revision'])
    assert saved['version'] == '0.2.0'
    assert 'version = "0.2.0" # release' in manifest.read_text(encoding='utf-8')
    assert (root / 'designs/card.json').read_bytes() == design
    artifact, filename = projects.build(snapshot['id'], saved['revision'])
    assert filename == 'test.ide-release-0.2.0.oawpack'
    assert inspect_archive(artifact).manifest.version == '0.2.0'
    with pytest.raises(ConflictError):
        projects.set_version(snapshot['id'], '0.3.0', snapshot['revision'])
    with pytest.raises(ValueError, match='canonical'):
        projects.set_version(snapshot['id'], 'v0.3', saved['revision'])


def test_build_rejects_wheel_from_previous_source_revision(projects, tmp_path):
    from backend.tests.test_pack_build_project import zip_bytes
    root = tmp_path / 'stale'
    shutil.copytree(Path(__file__).resolve().parents[2] / 'examples/packs/greeter', root)
    wheel_dir = root / 'dist'
    wheel_dir.mkdir()
    wheel = wheel_dir / 'greeter.whl'
    wheel.write_bytes(zip_bytes({'oaw_pack_example_greeter/__init__.py': 'stale = True'}))
    (wheel_dir / 'frontend').mkdir()
    (wheel_dir / 'frontend/index.js').write_text('export default {}', encoding='utf-8')
    snapshot = projects.open(str(root))
    with pytest.raises(ValueError, match='does not match the current source'):
        projects.build(snapshot['id'], snapshot['revision'])


@pytest.mark.parametrize('path', ['../outside.py', '/outside.py', 'C:/outside.py', 'nested/../outside.py',
                                  '.git/config', '.env', '.env.local', '.ssh/id_rsa', 'src\\outside.py'])
def test_project_relative_paths_reject_escape_and_private_files(projects, path):
    snapshot = projects.create('Paths')
    with pytest.raises(ValueError):
        write_file(Path(snapshot['path']), path, 'changed', None)


def test_hard_links_and_unknown_project_handle_are_rejected(projects, tmp_path):
    snapshot = projects.create('Links')
    root = Path(snapshot['path'])
    outside = tmp_path / 'outside.txt'
    outside.write_text('private', encoding='utf-8')
    os.link(outside, root / 'linked.txt')
    with pytest.raises(ValueError, match='hard linked'):
        read_file(root, 'linked.txt')
    with pytest.raises(NotFoundError):
        projects.root('unknown')
    assert outside.read_text(encoding='utf-8') == 'private'


def test_new_external_project_does_not_relax_profile_or_link_boundaries(projects, tmp_path):
    from backend.sandbox.models import SandboxValidationError
    forbidden = projects.data_root / 'database/new-project'
    with pytest.raises(SandboxValidationError, match='application data'):
        projects.create('Forbidden', directory=str(forbidden))
    assert not forbidden.exists()
    with pytest.raises(SandboxValidationError):
        projects.create('Ancestor', directory=str(tmp_path))
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'linked'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='links or junctions'):
            projects.create('Linked', directory=str(link / 'child'))
        assert not (outside / 'child').exists()
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()


def test_invalid_source_remains_editable_with_actionable_diagnostics(projects):
    snapshot = projects.create('Broken')
    root = Path(snapshot['path'])
    manifest = read_file(root, 'oaw.pack.toml')
    changed = write_file(root, 'oaw.pack.toml', 'not valid toml', manifest['sha256'])
    check = projects.check(snapshot['id'])
    assert not check['ok'] and check['level'] == 'static'
    assert check['diagnostics'][0]['severity'] == 'error'
    assert check['diagnostics'][0]['path'] == 'oaw.pack.toml'
    write_file(root, 'oaw.pack.toml', manifest['content'], changed['sha256'])
    assert projects.check(snapshot['id'])['ok']


def test_python_syntax_diagnostic_blocks_build_without_importing_source(projects):
    snapshot = projects.create('Syntax')
    root = Path(snapshot['path'])
    source = "raise RuntimeError('must not execute')\ndef broken(:\n    pass\n"
    write_file(root, 'src/actions.py', source, None)
    checked = projects.check(snapshot['id'])
    assert not checked['ok']
    assert checked['diagnostics'] == [{'severity': 'error', 'path': 'src/actions.py',
                                       'line': 2, 'column': 12, 'message': 'invalid syntax'}]
    assert read_file(root, 'src/actions.py')['content'] == source
    with pytest.raises(ValueError, match='Resolve the project diagnostics'):
        projects.build(snapshot['id'], checked['revision'])


def test_invalid_design_json_has_a_navigable_source_location(projects):
    snapshot = projects.create('Broken design')
    root = Path(snapshot['path'])
    design = read_file(root, 'designs/card.json')
    write_file(root, design['path'], '{\n  "face":\n}', design['sha256'])
    checked = projects.check(snapshot['id'])
    assert not checked['ok']
    diagnostic = checked['diagnostics'][0]
    assert (diagnostic['path'], diagnostic['line'], diagnostic['column']) == ('designs/card.json', 3, 1)


def test_host_api_reuses_pack_request_gate_and_rejects_unopened_paths(client):
    assert client.get('/api/packs/projects').status_code == 403
    headers = {'x-oaw-pack-install': '1'}
    created = client.post('/api/packs/projects/create', headers=headers,
                          json={'name': 'API card', 'distribution_id': 'test.ide-api'})
    assert created.status_code == 201, created.text
    snapshot = created.json()
    assert client.get('/api/packs/projects/unknown/file', headers=headers, params={'path': 'pyproject.toml'}).status_code == 404
    preview = client.post(f'/api/packs/projects/{snapshot["id"]}/preview', headers=headers,
                          json={'design': {'fields': [{'key': 'value', 'label': 'Value', 'type': 'number', 'default': 3}],
                                           'operation': 'multiply'}, 'values': {'value': 7}})
    assert preview.json()['result'] == 7
    checked = client.post(f'/api/packs/projects/{snapshot["id"]}/check', headers=headers,
                          json={'expected_revision': snapshot['revision']})
    assert checked.status_code == 200 and checked.json()['ok']
    built = client.post(f'/api/packs/projects/{snapshot["id"]}/build', headers=headers,
                        json={'expected_revision': snapshot['revision']})
    assert built.status_code == 200, built.text
    assert inspect_archive(built.content).manifest.id == 'test.ide-api'


def test_agent_cannot_select_host_project_roots():
    from backend.api.pack_projects import host_authoring
    from backend.errors import PermissionDeniedError
    from backend.request_context import ActorKind, ActorRef, LOCAL_TENANT, RequestContext
    with pytest.raises(PermissionDeniedError, match='host user'):
        host_authoring(RequestContext('test', ActorRef(ActorKind.AGENT, 'agent'), LOCAL_TENANT, 'delegated'))


def test_agent_project_edit_reuses_live_sandbox_grants_and_ide_conflicts(runtime_client, tmp_path, monkeypatch):
    """Real file/capability integration; the existing fixture mocks OS provisioning."""
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.errors import PermissionDeniedError
    from backend.tests.conftest import create_node
    from tests.test_sandbox_workspace import WorkspaceNativeApi
    client, backend, _ = runtime_client
    monkeypatch.setattr(backend, '_native', WorkspaceNativeApi())
    services = client.app.state.services
    projects = PackProjects(services.settings.data_root, services.database)
    snapshot = projects.create('Agent source', directory=str(tmp_path / 'shared-source'))
    agent = create_node(client, 'agent')
    sandbox = create_node(client, 'sandbox', config={'workspace_path': snapshot['path']})
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': sandbox['id'], 'relationship': 'execute'})
    assert edge.status_code == 201, edge.text
    started = client.post(f'/api/sandboxes/{sandbox["id"]}/start')
    assert started.status_code == 200, started.text
    provider = WorldAgentCapabilityProvider(services)
    request = {'sandbox': sandbox['id'], 'path': 'README.md', 'content': '# Agent edit\n', 'overwrite': True}
    client.portal.call(provider.invoke_tool, agent['id'], 'operation:write_sandbox_text_file', request)
    changed = projects.inspect(snapshot['id'])
    assert changed['revision'] != snapshot['revision']
    assert read_file(Path(snapshot['path']), 'README.md')['content'] == '# Agent edit\n'
    with pytest.raises(ConflictError):
        projects.check(snapshot['id'], snapshot['revision'])
    assert client.delete(f'/api/edges/{edge.json()["id"]}').status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:write_sandbox_text_file', request)


def test_external_cli_uses_same_files_and_hash_guard(projects, tmp_path):
    snapshot = projects.create('CLI')
    root = Path(snapshot['path'])
    source = read_file(root, 'README.md')
    replacement = tmp_path / 'replacement.txt'
    replacement.write_text('External agent edit\n', encoding='utf-8')
    command = [sys.executable, '-m', 'open_agent_world.pack', 'project', 'write', str(root),
               '--path', 'README.md', '--content-file', str(replacement), '--expected-sha256', source['sha256']]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['content'] == 'External agent edit\n'
    assert inspect_project(root)['revision'] != snapshot['revision']
    stale = subprocess.run(command, capture_output=True, text=True)
    assert stale.returncode != 0
    assert read_file(root, 'README.md')['content'] == 'External agent edit\n'
