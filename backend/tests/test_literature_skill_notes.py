from backend.tests.test_literature_records_service import setup_scope, anchors, method, invoke, upload, pdf_bytes
from backend.tests.conftest import create_node
from backend.tests.test_literature_exploration import seed
from backend.node_documents import read_document, write_document
from backend.errors import ResourceValidationError
from unittest.mock import AsyncMock
import pytest


def test_skill_text_objects_are_source_bound_idempotent_and_preserve_edits(client):
    scope, paper, _, _ = setup_scope(client)
    other = create_node(client, 'library.paper', name='Second source')
    upload(client, other['id'], pdf_bytes())
    seed(client, {'id':scope}, other)
    first = anchors(client, scope, paper).json()['sources'][0]
    second = anchors(client, scope, other['id']).json()['sources'][0]
    spec = method(first, sources=[first, second])
    response = invoke(client, scope, 'record', {'kind':'method','value':spec})
    assert response.status_code == 200, response.text
    notes = [node for node in client.get('/api/nodes').json() if node['config'].get('research_projection') == 'paper_skill']
    assert len(notes) == 2 and {n['config']['paper_id'] for n in notes} == {paper,other['id']}
    assert all(node['type'] == 'text' for node in notes)
    services = client.app.state.services
    for node in notes:
        text = services.resources.read_text(node['id']).content
        assert '草稿' in text and 'p2' in text and 'Record measurements' in text
        assert any(edge['source'] == node['config']['paper_id'] and edge['target'] == node['id'] and edge['relationship'] == 'literature.method' for edge in client.get('/api/edges').json())
    note = notes[0]
    assert client.patch(f"/api/nodes/{note['id']}",json={'position':{'x':9321,'y':456}}).status_code == 200
    original = services.resources.read_text(note['id'])
    services.resources.replace_text(note['id'], original.content+'\nMy reading note',expected_revision=original.revision)
    spec = {**spec,'revision':2,'purpose':'Updated method purpose, still unverified.'}
    assert invoke(client,scope,'record',{'kind':'method','value':spec}).status_code == 200
    assert invoke(client,scope,'organize',{'action':'sync'}).status_code == 200
    notes_after = [node for node in client.get('/api/nodes').json() if node['config'].get('research_projection') == 'paper_skill']
    assert {node['id'] for node in notes_after} == {node['id'] for node in notes}
    assert client.get(f"/api/nodes/{note['id']}").json()['position'] == {'x':9321,'y':456}
    assert services.resources.read_text(note['id']).content.endswith('My reading note')
    other_note = next(node for node in notes_after if node['id'] != note['id'])
    assert 'Updated method purpose' in services.resources.read_text(other_note['id']).content


def skill_nodes(client):
    return [node for node in client.get('/api/nodes').json() if node['config'].get('research_projection') == 'paper_skill']


@pytest.mark.parametrize('operation', ['record', 'organize'])
def test_failed_projection_restores_existing_note_bytes_and_resource_revision(client, monkeypatch, operation):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()['sources'][0]
    spec = method(source)
    assert invoke(client, scope, 'record', {'kind': 'method', 'value': spec}).status_code == 200
    services = client.app.state.services
    note = skill_nodes(client)[0]
    before = services.resources.read_text(note['id'])
    before_config = services.world.get_card(note['id']).config
    published = AsyncMock()
    monkeypatch.setattr(type(services), '_publish_resource_modified', published)
    async def fail(*args, **kwargs):
        assert 'New projected purpose' in services.resources.read_text(note['id']).content
        raise ResourceValidationError('Simulated later topology conflict')
    monkeypatch.setattr('backend.literature_exploration.physical_edges', fail)
    if operation == 'record':
        response = invoke(client, scope, 'record', {'kind': 'method', 'value': {**spec, 'revision': 2, 'purpose': 'New projected purpose'}})
    else:
        current = read_document(services, scope)
        value = {**current['value'], 'methods': [{**current['value']['methods'][0], 'revision': 2, 'purpose': 'New projected purpose'}]}
        write_document(services, scope, value, current['revision'])
        response = invoke(client, scope, 'organize', {'action': 'sync'})
    assert response.status_code == 422, response.text
    after = services.resources.read_text(note['id'])
    assert after.content == before.content and after.revision == before.revision and after.updated_at == before.updated_at
    assert services.world.get_card(note['id']).config == before_config
    published.assert_not_awaited()


def test_failed_projection_removes_only_new_managed_note_files(client, monkeypatch):
    scope, paper, _, _ = setup_scope(client)
    services = client.app.state.services
    source = anchors(client, scope, paper).json()['sources'][0]
    files_before = {path: path.read_bytes() for path in services.resources.text_root.glob('*') if path.is_file()}
    async def fail(*args, **kwargs):
        assert len(list(services.resources.text_root.glob('*'))) > len(files_before)
        raise ResourceValidationError('Simulated edge conflict')
    monkeypatch.setattr('backend.literature_exploration.physical_edges', fail)
    response = invoke(client, scope, 'record', {'kind': 'method', 'value': method(source)})
    assert response.status_code == 422, response.text
    assert not skill_nodes(client)
    assert {path: path.read_bytes() for path in services.resources.text_root.glob('*') if path.is_file()} == files_before


def test_compensation_preserves_external_text_written_after_projection(client, monkeypatch):
    scope, paper, _, _ = setup_scope(client)
    services = client.app.state.services
    source = anchors(client, scope, paper).json()['sources'][0]
    spec = method(source)
    assert invoke(client, scope, 'record', {'kind': 'method', 'value': spec}).status_code == 200
    note = skill_nodes(client)[0]
    record = services.resources.get_record(note['id'])
    path = services.resources.resolve_relative_path(record.relative_path, require_exists=True)
    async def fail(*args, **kwargs):
        path.write_text('External user edit must survive', encoding='utf-8')
        raise ResourceValidationError('Simulated edge conflict')
    monkeypatch.setattr('backend.literature_exploration.physical_edges', fail)
    assert invoke(client, scope, 'record', {'kind': 'method', 'value': {**spec, 'revision': 2, 'purpose': 'Changed'}}).status_code == 422
    assert path.read_text(encoding='utf-8') == 'External user edit must survive'


def test_removed_method_source_retires_note_and_edges_without_erasing_user_text(client, monkeypatch):
    scope, first_id, _, _ = setup_scope(client)
    second = create_node(client, 'library.paper', name='Second source')
    upload(client, second['id'], pdf_bytes())
    seed(client, {'id': scope}, second)
    first_source = anchors(client, scope, first_id).json()['sources'][0]
    second_source = anchors(client, scope, second['id']).json()['sources'][0]
    assert invoke(client, scope, 'record', {'kind': 'method', 'value': method(first_source, sources=[first_source, second_source])}).status_code == 200
    services = client.app.state.services
    old = next(node for node in skill_nodes(client) if node['config']['paper_id'] == first_id)
    active = next(node for node in skill_nodes(client) if node['config']['paper_id'] == second['id'])
    before = services.resources.read_text(old['id'])
    user_text = before.content + '\nUser observation preserved'
    services.resources.replace_text(old['id'], user_text, expected_revision=before.revision)
    published = AsyncMock()
    monkeypatch.setattr(type(services), '_publish_resource_modified', published)
    response = invoke(client, scope, 'record', {'kind': 'method', 'value': method(second_source, revision=2, purpose='Current second-source method')})
    assert response.status_code == 200, response.text
    assert services.world.get_card(old['id']).config['projection_historical'] is True
    assert services.resources.read_text(old['id']).content == user_text
    assert not services.world.get_card(active['id']).config['projection_historical']
    assert services.world.get_card(active['id']).config['revision'] == services.resources.read_text(active['id']).revision
    assert not any(edge['target'] == old['id'] and edge['relationship'] == 'literature.method' for edge in client.get('/api/edges').json())
    current = read_document(services, scope)['value']
    assert next(entity for entity in current['exploration_nodes'] if entity['kind'] == 'method')['node_id'] == active['id']
    assert any(call.args[0].card_id == active['id'] for call in published.await_args_list)
    assert invoke(client, scope, 'organize', {'action': 'sync'}).status_code == 200
    assert not any(edge['target'] == old['id'] and edge['relationship'] == 'literature.method' for edge in client.get('/api/edges').json())


@pytest.mark.parametrize('basis,label', [('metadata', '题录策略草稿'), ('abstract', '摘要研读草稿')])
def test_micro_skill_note_preserves_source_level_without_inventing_pdf_proof(basis, label):
    from backend.literature_skill_notes import skill_note
    item = {'id': 'micro-fixture', 'name': 'A research strategy', 'revision': 1, 'purpose': 'Plan a bounded comparison',
        'status': 'draft', 'limitations': ['Based only on the supplied source level'],
        'steps': [{'instruction': 'Compare reported scope', 'origin': 'research_strategy'}],
        'sources': [{'paper_id': 'paper', 'basis': basis, 'title': 'Fixture title', 'doi': '10.1234/fixture',
            'source_url': 'https://doi.org/10.1234/fixture', 'quote': 'Controlled abstract' if basis == 'abstract' else '',
            'metadata_sha256': 'a' * 64}]}
    note = skill_note('scope', item, 'paper', micro=True)
    assert label in note and '研究策略' in note and '未读取全文' in note
    assert '10.1234/fixture' in note and '题录快照' in note
    assert '/methods/' not in note and '导出 Skill 包' not in note and '文档 ' not in note


@pytest.mark.parametrize('basis', ['metadata', 'abstract'])
def test_micro_skill_projects_text_and_related_path_without_pdf(client, basis):
    from backend.tests.test_literature_micro import fixture, payload, record
    scope, paper = fixture(client, 'Controlled source abstract' if basis == 'abstract' else '')
    response = record(client, scope, payload(client, paper, basis=basis))
    assert response.status_code == 200, response.text
    services = client.app.state.services
    note = skill_nodes(client)[0]
    assert note['config']['projection_kind'] == 'micro_skill'
    content = services.resources.read_text(note['id']).content
    assert ('摘要研读草稿' if basis == 'abstract' else '题录策略草稿') in content
    assert '未读取全文' in content and '导出 Skill 包' not in content
    value = read_document(services, scope)['value']
    entity = next(item for item in value['exploration_nodes'] if item['id'] == 'micro_skill:micro-controlled')
    assert entity['kind'] == 'perspective' and entity['node_id'] == note['id'] and entity['paper_ids'] == [paper]
    assert all(entity['id'] not in road['member_ids'] for road in value['exploration_roads'])
    source = services.world.get_card(paper)
    assert note['position'] == {'x': source.position.x, 'y': source.position.y + source.size.height + 90}
    assert any(link['source'] == 'paper:' + paper and link['target'] == entity['id'] and link['relation'] == 'related'
        for link in value['exploration_links'])
    assert any(edge['source'] == paper and edge['target'] == note['id'] and edge['relationship'] == 'literature.related'
        for edge in client.get('/api/edges').json())
    assert invoke(client, scope, 'organize', {'action': 'sync'}).status_code == 200
    assert [item['id'] for item in skill_nodes(client)] == [note['id']]
    moved = invoke(client, scope, 'organize', {'action': 'move_member', 'road_id': 'trunk', 'entity_id': entity['id']})
    assert moved.status_code == 200, moved.text
    assert invoke(client, scope, 'organize', {'action': 'sync'}).status_code == 200
    assert entity['id'] in next(road for road in read_document(services, scope)['value']['exploration_roads'] if road['id'] == 'trunk')['member_ids']


@pytest.mark.parametrize('strategy', ['close_read', 'method'])
def test_reading_task_prompts_allow_source_bound_micro_skill_without_pdf(client, strategy):
    from backend.tests.test_literature_micro import fixture
    scope, paper = fixture(client)
    board = create_node(client, 'matcreator.tasks')['id']
    services = client.app.state.services
    current = read_document(services, scope)
    write_document(services, scope, {**current['value'], 'task_board_id': board}, current['revision'])
    response = invoke(client, scope, 'organize', {'action': 'stage_task', 'strategy': strategy,
        'paper_id': paper, 'rationale': 'Inspect available source information without a PDF'})
    assert response.status_code == 200, response.text
    task = read_document(services, board)['value']['plans'][0]['tasks'][0]
    assert 'MicroSkill' in task['description'] and 'kind=micro_skill' in task['description']
    assert 'PDF is not a prerequisite' in task['description'] and 'strict source-bound MethodSpec' in task['description']
    assert task['status'] == 'pending' and not client.get('/api/runs').json()
