"""Convert legacy single-Agent Barracks into a separate, reviewable data directory.

Run with the application stopped:
    python -m backend.migrations.barracks OLD_DATA_ROOT NEW_DATA_ROOT
The source is never modified. Unsupported team templates fail before copying.
"""
import argparse
import asyncio
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from backend.errors import PluginCompatibilityError


def check_legacy(database):
    with database.transaction() as connection:
        legacy = connection.execute("SELECT id FROM cards WHERE type = 'oaw.barracks.template' LIMIT 1").fetchone()
        legacy_edge = connection.execute("SELECT e.id FROM edges e JOIN cards c ON c.id=e.source_id WHERE e.relationship='oaw.barracks.summon' AND c.type='agent' LIMIT 1").fetchone()
        old_documents = connection.execute("SELECT v.scope_id,v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id JOIN cards c ON c.id=s.owner_id WHERE s.scope_kind='node_document' AND v.key='document' AND c.type='oaw.barracks'").fetchall()
        documents = [(row[0], json.loads(row[1])) for row in old_documents]
        if legacy or legacy_edge or any('templates' in value and value['templates'] != [] for _, value in documents):
            raise PluginCompatibilityError("Legacy Barracks data requires migration. Stop the application and run: python -m backend.migrations.barracks OLD_DATA_ROOT NEW_DATA_ROOT. The source remains untouched; use the new data directory after reviewing the result.")
        # Empty legacy libraries carry no snapshots or topology to migrate.
        # Normalize only after all checks pass, so rejected worlds stay untouched.
        for scope, value in documents:
            if value.get('templates') == []:
                del value['templates']
                connection.execute("UPDATE state_values SET value_json=? WHERE scope_id=? AND key='document'", (json.dumps(value), scope))


def inspect_source(root):
    connection = sqlite3.connect(f"{(root / 'database/world.sqlite3').as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        documents = {row['owner_id']: json.loads(row['value_json']) for row in connection.execute(
            "SELECT s.owner_id,v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id WHERE s.scope_kind='node_document' AND v.key='document'")}
        cards = [dict(row) for row in connection.execute("SELECT * FROM cards")]
        entries = [(card, documents[card['id']]) for card in cards if card['type'] == 'oaw.barracks.template']
        for card in cards:
            if card['type'] == 'oaw.barracks':
                for value in documents.get(card['id'], {}).get('templates', []):
                    if not value.get('node_id'):
                        entries.append(({**card, 'id': str(uuid4()), 'parent_id': card['id'], 'name': value['name']}, value))
        legacy_ids = {card['id'] for card, _ in entries}
        for edge in connection.execute("SELECT * FROM edges WHERE relationship='oaw.barracks.summon'"):
            if edge['target_id'] in legacy_ids:
                raise ValueError("A legacy direct template connection cannot be widened to an entire Barracks automatically. Remove that connection in the old version before migrating.")
        from backend.plugins.loader import load_plugin_registry
        registry = load_plugin_registry()
        for _, value in entries:
            nodes = value.get('blueprint', {}).get('nodes', [])
            if any(n['type'] == 'legion' for n in nodes):
                raise ValueError("Restore legacy Legion templates in the old version before migrating; their team context must remain explicit.")
            agents = [n for n in nodes if 'core.agent' in registry.node_type(n['type']).traits]
            if len(agents) != 1 or agents[0]['key'] != value['entry_agent_key']:
                raise ValueError("Multi-Agent legacy templates must be restored as Legions in the old version before migrating; their team topology will not be silently flattened.")
            for node in nodes:
                if node['type'] == 'legion' or node in agents:
                    continue
                if not registry.relationship_options(agents[0]['type'], node['type']):
                    raise ValueError(f"{node['type']} cannot connect to the Agent; restore this legacy template in the old version before migrating.")
        return entries
    finally:
        connection.close()


async def migrate(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Choose a new output directory outside the source directory")
    entries = inspect_source(source)
    shutil.copytree(source, destination)
    marker = destination / 'MIGRATION_INCOMPLETE'
    marker.write_text('Do not use this directory until migration completes.', encoding='utf-8')
    database_path = destination / 'database/world.sqlite3'
    # Make a consistent SQLite copy, including any committed WAL contents.
    with sqlite3.connect(source / 'database/world.sqlite3') as original, sqlite3.connect(database_path) as output:
        original.backup(output)
    with sqlite3.connect(database_path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for card, value in entries:
            entry = next(n for n in value['blueprint']['nodes'] if n['key'] == value['entry_agent_key'])
            db.execute("UPDATE cards SET type='agent',plugin_id='open-agent-world.core',config_json=? WHERE id=?", (json.dumps(entry['config']), card['id']))
            db.execute("DELETE FROM state_scopes WHERE scope_kind='node_document' AND owner_id=?", (card['id'],))
        for scope, raw in db.execute("SELECT v.scope_id,v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id JOIN cards c ON c.id=s.owner_id WHERE s.scope_kind='node_document' AND v.key='document' AND c.type='oaw.barracks'").fetchall():
            value = json.loads(raw); value.pop('templates', None)
            db.execute("UPDATE state_values SET value_json=? WHERE scope_id=? AND key='document'", (json.dumps(value), scope))
        old_edges = db.execute("SELECT e.id,e.source_id,e.target_id FROM edges e JOIN cards c ON c.id=e.source_id WHERE e.relationship='oaw.barracks.summon' AND c.type='agent'").fetchall()
        db.executemany("DELETE FROM edges WHERE id=?", [(e[0],) for e in old_edges])
    from backend.config import Settings
    from backend.services import create_services
    from backend.world.models import CardCreate, CardPatch, EdgeCreate
    from backend.node_documents import read_document, write_document
    from backend.node_containers import parent_first
    from backend.legions.models import LegionTemplateNode
    services = create_services(Settings.for_data_root(destination))
    try:
        for card, value in entries:
            agent_id = card['id']
            if services.world.maybe_get_card(agent_id) is None:
                entry = next(n for n in value['blueprint']['nodes'] if n['key'] == value['entry_agent_key'])
                await services.create_card(CardCreate(id=agent_id, type='agent', name=value['name'], config=entry['config'], parent_id=card['parent_id']))
            parent = card.get('parent_id')
            if not parent:
                box = await services.create_card(CardCreate(type='oaw.barracks', name=value['name'] + ' Barracks', position={'x':card['x'],'y':card['y']}))
                parent = box.id
                await services.update_card(agent_id, CardPatch(parent_id=parent))
            await services.update_card(agent_id, CardPatch(config={'description': value.get('description','')}))
            nodes = [LegionTemplateNode.model_validate(n) for n in value['blueprint']['nodes']]
            keys = {n.key: (agent_id if n.key == value['entry_agent_key'] else str(uuid4())) for n in nodes if n.type != 'legion'}
            for node in parent_first(nodes, key=lambda n:n.key, parent=lambda n:n.parent_key):
                if node.type == 'legion' or node.key == value['entry_agent_key']:
                    continue
                nested = keys.get(node.parent_key) if node.parent_key != value['entry_agent_key'] else None
                created = await services._create_card(CardCreate(id=keys[node.key], type=node.type, name=node.name, config=node.config,
                    parent_id=nested, equipment=None if nested else {'owner_id':agent_id, 'relationship':None}),
                    template_payload=node.payload, template_payload_version=node.payload_version, template_node_ids=keys,
                    _skip_collection_seed=node.initial_document is not None)
                if node.initial_document is not None:
                    current = read_document(services, created.id)
                    write_document(services, created.id, spec.document.remap_references(node.initial_document, keys), current['revision'])
            for edge in value['blueprint']['edges']:
                if edge['source'] not in keys or edge['target'] not in keys:
                    continue
                target = services.world.get_card(keys[edge['target']])
                if keys[edge['source']] == agent_id and target.equipment and target.equipment.relationship == edge['relationship']:
                    continue
                await services.create_edge(EdgeCreate(source=keys[edge['source']], target=keys[edge['target']], relationship=edge['relationship'], direction=edge['direction']))
            for binding in value.get('bindings', []):
                external = parent if binding['external_id'] == '$library' else binding['external_id']
                internal = keys[binding['internal_key']]
                if binding['relationship'] == 'oaw.barracks.summon':
                    old_edges.append((None, internal, external))
                else:
                    await services.create_edge(EdgeCreate(source=internal if binding['internal_is_source'] else external,
                        target=external if binding['internal_is_source'] else internal, relationship=binding['relationship'], direction=binding['direction']))
        for _, owner, target in old_edges:
            adapter = await services.create_card(CardCreate(type='oaw.barracks.summoner', equipment={'owner_id':owner}))
            await services.create_edge(EdgeCreate(source=adapter.id, target=target, relationship='oaw.barracks.summon'))
        for record in services.summoning.records():
            ids = set(record['node_ids'])
            record['root_node_ids'] = [node.id for key in ids if (node := services.world.maybe_get_card(key))
                                      and node.parent_id not in ids and (not node.equipment or node.equipment.owner_id not in ids)]
            if 'template_id' in record:
                record['agent_id'] = record.pop('template_id')
            services.summoning.save(record)
        (destination / 'legacy-barracks-archive.json').write_text(json.dumps(entries, ensure_ascii=False), encoding='utf-8')
        services.world.assert_plugin_availability()
        marker.unlink()
    finally:
        services.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    asyncio.run(migrate(args.source, args.destination))
    print(f'Migrated data: {args.destination.resolve()}')
