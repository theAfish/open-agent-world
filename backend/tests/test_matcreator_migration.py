"""Real source SQLite layouts and host persistence, without agent/model calls."""
import asyncio
import copy
from contextlib import closing
from functools import partial
import json
from pathlib import Path
from pathlib import PurePosixPath
import sqlite3
import stat
import subprocess
import sys
import zipfile

import pytest

from backend.node_documents import read_document
from backend.errors import ConflictError
from plugins.matcreator.oaw_matcreator import migrate as migration
from plugins.matcreator.oaw_matcreator.migration import import_into, import_migration, knowledge_document, remap_text

SCRIPT = Path(migration.__file__)


@pytest.fixture
def source(tmp_path):
    home = tmp_path / "old-matcreator"
    adk = home / ".adk"
    adk.mkdir(parents=True)
    workspace = home / "workspace"
    workspace.mkdir()
    (workspace / "structure.cif").write_bytes(b"data_test\n")
    (workspace / ".env").write_text("DO_NOT_EXPORT=secret", encoding="utf-8")
    (home / "config.yaml").write_text("api_key: do-not-export", encoding="utf-8")
    skill = workspace / "skills" / "custom"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# My custom skill\nUse scripts/run.py", encoding="utf-8")
    (skill / "scripts" / "run.py").write_text("print('science')", encoding="utf-8")
    (skill / "example.bin").write_bytes(bytes(range(256)))
    with closing(sqlite3.connect(adk / "session.db")) as db, db:
        db.executescript("""CREATE TABLE sessions(app_name TEXT,user_id TEXT,id TEXT,state TEXT,create_time TEXT,update_time TEXT);
            CREATE TABLE events(id TEXT,app_name TEXT,user_id TEXT,session_id TEXT,timestamp REAL,event_data TEXT);""")
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", ("matcreator", "alice", "s1",
            json.dumps({"title": "Old research", "execution_approved": True}), "2026-08-01 10:00:00", "2026-08-01 10:03:00"))
        for index, author in enumerate(("user", "execution_agent", "thinking_agent")):
            event = {"id": f"e{index}", "author": author, "content": {"parts": [
                {"text": "private reasoning", "thought": True},
                {"text": str(workspace / "structure.cif") if index == 0 else author + " reply"}]}}
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (f"e{index}", "matcreator", "alice", "s1", 1785578400 + index, json.dumps(event)))
    (adk / "agent_graphs").mkdir()
    (adk / "agent_graphs" / "s1.json").write_text('{"nodes":{"step1":{"status":"running"}}}', encoding="utf-8")
    with closing(sqlite3.connect(adk / "know_do_graph.db")) as db, db:
        db.executescript("""CREATE TABLE entries(id TEXT,title TEXT,entry_type TEXT,content TEXT,tags TEXT,aliases TEXT,
            metadata_json TEXT,scripts_json TEXT,assets_json TEXT,created_at TEXT);
            CREATE TABLE edges(id TEXT,source_id TEXT,target_id TEXT,relation TEXT);""")
        for identifier, kind, metadata in [("a", "capability", {"trust_score": 0.8, "verification_status": "peer_reviewed"}),
                ("b", "memory", {}), ("disabled", "procedure", {"disabled": True})]:
            db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, identifier, kind,
                "Research content " + identifier, '["material"]', "[]", json.dumps(metadata),
                json.dumps([{"filename": "compute.py", "content": "print(42)"}]) if identifier == "a" else "[]", "[]", "2026-08-01 10:00:00"))
        db.executemany("INSERT INTO edges VALUES (?,?,?,?)", [("ab", "a", "b", "related_memory"), ("bad", "a", "b", "wikilink")])
    return home


def test_reader_and_preview_do_not_change_source(source):
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = migration.discover(source)
    assert [m["sender_kind"] for m in result["sessions"][0]["messages"]] == ["user", "agent"]
    assert result["sessions"][0]["title"] == "Old research"
    original = json.loads(b"".join(next(migration.history_archives(result["sessions"][0]))[1]))
    assert len(original["events"]) == result["sessions"][0]["legacy"]["event_count"] == 3
    assert original["agent_graph"]["nodes"]["step1"]["status"] == "running"
    assert migration.main(["local", "--source", str(source), "--data-root", str(source.parent / "unused"), "--dry-run"]) == 0
    assert not (source.parent / "unused").exists()
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_native_wsl_failure_reports_errors_written_to_stdout(monkeypatch):
    monkeypatch.setattr(migration.subprocess, 'run', lambda *_args, **_kwargs:
        subprocess.CompletedProcess([], 1, stdout='Wsl/Service/E_ACCESSDENIED', stderr=''))
    with pytest.raises(ValueError, match='Wsl/Service/E_ACCESSDENIED'):
        migration.wsl_call('Ubuntu', ['python3', '-B'])


def test_multi_owner_selection_and_old_adk_columns(source):
    dbpath = source / ".adk" / "session.db"
    with closing(sqlite3.connect(dbpath)) as db, db:
        db.execute("INSERT INTO sessions SELECT app_name,'bob',id,'not-json',create_time,update_time FROM sessions")
        db.execute("INSERT INTO events VALUES ('other-owner','matcreator','bob','s1',1785578400,'not-json')")
    with pytest.raises(ValueError, match="--user-id"):
        migration.discover(source)
    assert len(migration.discover(source, user_id="alice")["sessions"]) == 1
    with closing(sqlite3.connect(dbpath)) as db, db:
        db.executescript("DROP TABLE events; CREATE TABLE events(id TEXT,app_name TEXT,user_id TEXT,session_id TEXT,timestamp REAL,author TEXT,content TEXT);")
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", ("old", "matcreator", "alice", "s1", 1785578400, "user", '{"parts":[{"text":"legacy ADK"}]}'))
    assert migration.discover(source, user_id="alice")["sessions"][0]["messages"][0]["content"] == "legacy ADK"


@pytest.mark.parametrize("modern", [True, False])
def test_standalone_planner_replies_exclude_tools_and_private_thoughts(source, modern):
    events = [
        ("user", {"role": "user", "parts": [{"text": "Question"}]}),
        ("MatCreator", {"role": "model", "parts": [
            {"text": "Private reasoning", "thought": True}, {"text": "Reply"}]}),
        ("MatCreator", {"role": "user", "parts": [{"text": "Tool response"}]}),
        ("execution_orchestrator", {"role": "model", "parts": [{"text": "Executor report"}]}),
        ("MatCreator", {"role": "model", "parts": [{"text": "Private", "thought": True}]}),
        ("MatCreator", {"role": "model", "parts": [{"functionCall": {"name": "run"}}]}),
        ("user", {"role": "model", "parts": [{"text": "Wrong role"}]}),
    ]
    with closing(sqlite3.connect(source / ".adk" / "session.db")) as db, db:
        db.execute("DELETE FROM events")
        if not modern:
            db.executescript("DROP TABLE events; CREATE TABLE events(id TEXT,app_name TEXT,user_id TEXT,session_id TEXT,timestamp REAL,author TEXT,content TEXT);")
        for index, (author, content) in enumerate(events):
            values = (f"old{index}", "matcreator", "alice", "s1", 1785578400 + index)
            if modern:
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (*values, json.dumps({"author": author, "content": content})))
            else:
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (*values, author, json.dumps(content)))
    session = migration.discover(source)["sessions"][0]
    assert [(message["sender_kind"], message["content"]) for message in session["messages"]] == [
        ("user", "Question"), ("agent", "Reply")]
    assert session["legacy"]["event_count"] == len(events)


def test_split_graph_json_memory_and_memory_md(source):
    with closing(sqlite3.connect(source / ".adk" / "skill_graph.db")) as db, db:
        db.executescript("CREATE TABLE kg_nodes(id TEXT,name TEXT,type TEXT,description TEXT,reference_count INT,confidence REAL);")
        db.execute("INSERT INTO kg_nodes VALUES ('legacy','Legacy skill','skill','description',3,0.9)")
    (source / ".adk" / "memory").mkdir()
    (source / ".adk" / "memory" / "s1.json").write_text('{"m1":{"content":"Collected memory"}}', encoding="utf-8")
    (source / "workspace" / "MEMORY.md").write_text("# Memory\n- Remember to converge calculations", encoding="utf-8")
    result = migration.discover(source)
    assert len(result["knowledge"]["entries"]) == 6
    assert next(e for e in result["knowledge"]["entries"] if e["raw"]["title"] == "Legacy skill")["raw"]["metadata"]["usage_count"] == 3


@pytest.mark.parametrize("compress", [False, True])
def test_package_is_portable_and_filters_secrets(source, tmp_path, compress):
    original = migration.discover(source)
    package = tmp_path / "research.zip"
    migration.export_package(original, package, compress=compress)
    with zipfile.ZipFile(package) as archive:
        manifest = migration.read_package(archive)
        assert "workspace/.env" not in archive.namelist() and "config.yaml" not in archive.namelist()
        assert archive.getinfo("workspace/structure.cif").compress_type == (zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED)
        source.rename(tmp_path / "unavailable-source")
        target = migration.extract_workspace(archive, manifest, tmp_path / "new-workspace")
        assert (target / "structure.cif").read_bytes() == b"data_test\n"
        assert (target / "skills" / "custom" / "example.bin").read_bytes() == bytes(range(256))
    with pytest.raises(ValueError, match="new output"):
        migration.export_package(original, package)
    assert zipfile.is_zipfile(package)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "workspace/C:bad", "workspace/a\\b", "workspace/CON.txt", "workspace/a."])
def test_package_rejects_unsafe_paths(tmp_path, name):
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(name, b"bad")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError):
        migration.read_package(archive)
    assert not (tmp_path / "escape").exists()


def test_package_rejects_links_and_undeclared_files(source, tmp_path):
    package = tmp_path / "link.zip"
    info = zipfile.ZipInfo("workspace/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(info, "../../escape")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="links"):
        migration.read_package(archive)
    package.unlink()
    migration.export_package(migration.discover(source), package)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("extra", "undeclared")
    with zipfile.ZipFile(package) as archive, pytest.raises(ValueError, match="undeclared"):
        migration.read_package(archive)


def test_local_import_history_resources_and_idempotency(client, source):
    services = client.app.state.services
    manifest = migration.discover(source)
    before = (source / "workspace" / "structure.cif").stat()
    def forbid_history_copy(_source):
        raise AssertionError("Local migration must not read or copy raw history")
    result = client.portal.call(partial(import_into, services, manifest, source / "workspace", history_reader=forbid_history_copy))
    ids = result["node_ids"]
    assert result["sessions_added"] == 1 and result["messages_added"] == 2
    assert result["knowledge_added"] == 3 and result["skills_added"] == 2
    graph = read_document(services, ids["knowledge"])["value"]
    imported = [e for e in graph["entries"] if e["provenance"].get("migration_source") == manifest["source_id"]]
    assert {e["title"] for e in imported} == {"a", "b", "custom"}
    assert next(e for e in imported if e["title"] == "a")["verification"] == "reviewed"
    assert any(s["files"].get("scripts/compute.py") == "print(42)" for s in graph["skills"])
    sandbox = services.world.get_card(ids["sandbox"])
    assert sandbox.config["workspace_path"] == str(source / "workspace") and sandbox.config["workspace_scope"] == "shared"
    assert not services.run_manager.list_runs()
    session = next(s for s in services.conversations.list_sessions(ids["conversation"]) if s.title == "Old research")
    messages = services.conversations.context_messages(ids["conversation"], session.id)
    assert [m.sender_kind for m in messages] == ["user", "agent", "system"]
    assert messages[0].created_at.year == 2026 and session.created_at.month == 8
    attachment = messages[-1].attachments[0]
    content = services.resources.artifacts.path(attachment.version_id) / attachment.path
    reference = json.loads(content.read_text(encoding="utf-8"))
    assert reference["format"] == "oaw.matcreator-history-reference"
    assert reference["event_count_at_import"] == 3
    assert Path(reference["database"]) == source / ".adk" / "session.db"
    assert content.stat().st_size < 4096
    assert result["history_parts_added"] == 0 and result["history_references_added"] == 1
    download = client.get(f"/api/conversations/{ids['conversation']}/sessions/{session.id}/attachments/{attachment.version_id}", params={"path": attachment.path})
    assert download.status_code == 200 and Path(download.json()["agent_graph"]) == source / ".adk" / "agent_graphs" / "s1.json"
    again = client.portal.call(import_into, services, migration.discover(source), source / "workspace")
    assert again["sessions_added"] == again["messages_added"] == again["knowledge_added"] == again["skills_added"] == 0
    assert again["conflicts"] == []
    assert again["node_ids"] == ids
    assert before.st_mtime_ns == (source / "workspace" / "structure.cif").stat().st_mtime_ns


@pytest.mark.parametrize("unreadable", [False, True])
def test_destination_settings_are_preserved_and_unusable_bindings_reported(client, source, tmp_path, unreadable):
    from cryptography.fernet import Fernet
    services = client.app.state.services
    resource = tmp_path / "current-models"
    resource.mkdir()
    variables = {"ACCESS_KEY": {"secret_ref": "current-access"}, "MODEL": {"folder_ref": "current-model"}, "REGION": "current-region"}
    saved = client.put('/api/settings/sandbox', json={"environment_variables": variables,
        "secrets": {"current-access": "current-oaw-secret"}, "folders": {"current-model": {"path": str(resource)}}})
    assert saved.status_code == 200, saved.text
    services.llm_settings.save(base_url="https://current.example.test/v1", api_key="current-model-key")
    if unreadable:
        token = Fernet(Fernet.generate_key()).encrypt(b"old-host-private-value").decode()
        with services.database.transaction() as db:
            db.execute("UPDATE application_settings SET value_json=? WHERE key LIKE 'global_execution_%'", (json.dumps(token),))
    def current_settings():
        with services.database.locked() as db:
            return [tuple(row) for row in db.execute("SELECT key,value_json FROM application_settings WHERE key IN ('sandbox','llm_connection','model_connections') OR key LIKE 'global_execution_%' ORDER BY key")]
    before = current_settings()
    key = services.llm_settings.key_path.read_bytes()
    manifest = migration.discover(source)
    manifest["environment_variables"] = {"ACCESS_KEY": "legacy-secret", "REGION": "legacy-region"}
    result = client.portal.call(import_into, services, manifest, source / 'workspace')
    assert current_settings() == before and services.llm_settings.key_path.read_bytes() == key
    settings = result['destination_settings']
    assert settings['policy'] == 'preserve_oaw' and settings['environment_ready'] is not unreadable
    assert set(settings['environment_variables']) == set(variables)
    assert {item['name'] for item in settings['issues']} == ({'ACCESS_KEY', 'MODEL'} if unreadable else set())
    assert bool([warning for warning in result['warnings'] if 'Execution setup requires attention' in warning]) is unreadable
    assert result['status'] == 'complete' and result['messages_added'] == 2
    serialized = json.dumps(result)
    assert all(secret not in serialized for secret in ('legacy-secret', 'current-oaw-secret', 'current-model-key', 'old-host-private-value'))


def test_conflicts_preserve_edits_and_append_new_history(client, source):
    services = client.app.state.services
    manifest = migration.discover(source)
    result = client.portal.call(import_into, services, manifest, source / "workspace")
    group = services.world.get_card(result['node_ids']['group'])
    edited = client.patch(f'/api/nodes/{group.id}', json={'config': {**group.config, 'description': 'Personal research notes'}})
    assert edited.status_code == 200
    changed = copy.deepcopy(manifest)
    changed["sessions"][0]["messages"][0]["content"] = "changed source"
    changed["sessions"][0]["messages"].append({"id": "new", "sender_kind": "user", "content": "New history", "created_at": manifest["created_at"]})
    changed["knowledge"]["entries"][0]["raw"]["content"] = "changed knowledge"
    repeated = client.portal.call(import_into, services, changed, source / "workspace")
    assert repeated["messages_added"] == 1
    assert {item["kind"] for item in repeated["conflicts"]} == {"message", "entries", "skills"}
    graph = read_document(services, result["node_ids"]["knowledge"])["value"]
    assert next(e for e in graph["entries"] if e["title"] == "a")["content"] == "Research content a"
    assert services.world.get_card(group.id).config['description'] == 'Personal research notes'


def test_backfill_missing_planner_replies_preserves_order_and_destination_messages(client, source, monkeypatch):
    from plugins.matcreator.oaw_matcreator import migration as importer
    services = client.app.state.services
    manifest = migration.discover(source)
    history = manifest["sessions"][0]["messages"]
    history.extend([
        {"id": "next-user", "sender_kind": "user", "content": "Second question", "created_at": "2026-08-01T10:01:00+00:00"},
        {"id": "next-reply", "sender_kind": "agent", "content": "Second reply", "created_at": "2026-08-01T10:02:00+00:00"},
    ])
    old = copy.deepcopy(manifest)
    old["sessions"][0]["messages"] = [message for message in history if message["sender_kind"] == "user"]
    first = client.portal.call(import_into, services, old, source / "workspace")
    ids = first["node_ids"]
    session = next(s for s in services.conversations.list_sessions(ids["conversation"]) if s.title == "Old research")
    local = services.conversations.add_message(ids["conversation"], session.id,
        sender_kind="user", sender_id=None, sender_name="You", content="New conversation in OAW")
    with services.database.transaction() as db:
        original_id = migration.identity(manifest["source_id"], manifest["sessions"][0]["key"], history[0]["id"])
        db.execute("UPDATE conversation_messages SET content='My edit' WHERE id=?", (original_id,))
    before = services.conversations.context_messages(ids["conversation"], session.id)
    order_history = importer._order_history
    def fail_after_reordering(*args):
        order_history(*args)
        raise RuntimeError("Interrupted reply backfill")
    with monkeypatch.context() as patch:
        patch.setattr(importer, "_order_history", fail_after_reordering)
        with pytest.raises(RuntimeError, match="Interrupted reply backfill"):
            client.portal.call(import_into, services, manifest, source / "workspace")
    assert services.conversations.context_messages(ids["conversation"], session.id) == before
    repaired = client.portal.call(import_into, services, manifest, source / "workspace")
    assert repaired["node_ids"] == ids and repaired["sessions_added"] == 0 and repaired["messages_added"] == 2
    assert repaired["conflicts"] == [{"kind": "message", "id": original_id}]
    messages = services.conversations.context_messages(ids["conversation"], session.id)
    assert [message.sender_kind for message in messages[:4]] == ["user", "agent", "user", "agent"]
    assert [message.content for message in messages[:4]] == ["My edit", history[1]["content"], "Second question", "Second reply"]
    assert next(message for message in messages if message.id == local.id).content == local.content
    assert [message.id for message in messages[4:] if message.sender_kind != "system"] == [local.id]
    assert all(message.sender_id == ids["agent"] for message in messages if message.sender_kind == "agent")
    assert [message.sequence for message in messages] == list(range(1, len(messages) + 1))
    again = client.portal.call(import_into, services, manifest, source / "workspace")
    assert again["messages_added"] == again["sessions_added"] == 0
    assert services.conversations.context_messages(ids["conversation"], session.id) == messages
    assert not services.run_manager.list_runs()


def test_import_result_is_discoverable_and_discarding_its_entry_keeps_data(client, source):
    from backend.card_library import LibraryEdit
    services = client.app.state.services
    obstacle = client.post('/api/nodes', json={'type': 'text', 'name': 'Existing work', 'position': {'x': 12000, 'y': 300}})
    assert obstacle.status_code == 201
    result = client.portal.call(import_into, services, migration.discover(source), source / 'workspace')
    group = services.world.get_card(result['node_ids']['group'])
    assert group.position.x > 12000 + obstacle.json()['size']['width']
    assert result['status'] == 'complete' and result['totals']['sessions'] == 1 and result['totals']['messages'] == 2
    state = services.card_library.read()
    deck = next(deck for deck in state.decks if deck.id == result['location']['deck_id'])
    entry = next(entry for entry in deck.entries if entry.kind == 'legion_instance')
    assert entry.id == group.id
    metadata = services.card_library.snapshot()['legion_instances'][group.id]
    assert '1 sessions, 2 messages' in metadata['description']
    services.card_library.edit(LibraryEdit(action='update_deck', id=deck.id, expected_revision=state.revision,
        entries=[item for item in deck.entries if item != entry]))
    services.card_library.reconcile()
    assert group.id not in services.card_library.snapshot()['legion_instances']
    assert services.world.get_card(group.id).name == 'MatCreator imported research'
    assert any(s.title == 'Old research' for s in services.conversations.list_sessions(result['node_ids']['conversation']))
    assert (source / 'workspace' / 'structure.cif').exists()


def test_import_reuses_a_user_created_legions_deck(client, source):
    from backend.card_library import LibraryEdit
    services = client.app.state.services
    library = services.card_library
    library.edit(LibraryEdit(action='delete_deck', id='saved-legions', expected_revision=library.read().revision))
    state = library.edit(LibraryEdit(action='create_deck', name='Legions', expected_revision=library.read().revision))
    target = state.active_deck_id
    result = client.portal.call(import_into, services, migration.discover(source), source / 'workspace')
    assert result['location']['deck_id'] == target
    assert sum(deck.name == 'Legions' for deck in library.read().decks) == 1


@pytest.mark.parametrize('fail_first_retry', [False, True])
def test_reimport_after_deleting_imported_legion_and_members(client, source, monkeypatch, fail_first_retry):
    services = client.app.state.services
    unrelated = client.post('/api/nodes', json={'type': 'text', 'name': 'Existing unrelated work'})
    assert unrelated.status_code == 201
    skill = source / 'skills' / 'global'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('# Global collected skill', encoding='utf-8')
    manifest = migration.discover(source)
    external = {s['id']: {'root': s['source_root'], 'path': s['source_path']} for s in manifest['skills']
        if not Path(s['source_path']).is_relative_to(Path(manifest['workspace']))}
    run = partial(import_into, services, manifest, source / 'workspace', external_skills=external)
    first = client.portal.call(run)
    old_group = first['node_ids']['group']
    old_nodes = [old_group, *[node.id for node in services.world.descendants(old_group)]]
    response = client.post('/api/nodes/batch-delete', json={'node_ids': old_nodes})
    assert response.status_code == 200, response.text
    receipt_key = 'migration.matcreator.' + manifest['source_id']
    with services.database.locked() as db:
        old_receipt = db.execute('SELECT value_json FROM application_settings WHERE key=?', (receipt_key,)).fetchone()[0]
    if fail_first_retry:
        def fail(*_args, **_kwargs):
            raise RuntimeError('retry interrupted')
        with monkeypatch.context() as patch:
            patch.setattr(services.conversations, 'add_message', fail)
            with pytest.raises(RuntimeError, match='retry interrupted'):
                client.portal.call(run)
        with services.database.locked() as db:
            assert db.execute('SELECT value_json FROM application_settings WHERE key=?', (receipt_key,)).fetchone()[0] == old_receipt
    result = client.portal.call(run)
    assert result['recreated_deleted_import'] is True
    assert result['node_ids']['group'] != old_group
    assert result['sessions_added'] == 1 and result['messages_added'] == 2
    assert result['history_references_added'] == 1 and result['history_parts_added'] == 0
    library = services.card_library.snapshot()
    references = [entry for deck in library['decks'] for entry in deck['entries'] if entry['kind'] == 'legion_instance']
    assert references == [{'kind': 'legion_instance', 'id': result['node_ids']['group']}]
    with services.database.locked() as db:
        receipt = json.loads(db.execute('SELECT value_json FROM application_settings WHERE key=?', (receipt_key,)).fetchone()[0])
    assert receipt['external_sandboxes'] and all(services.world.get_card(ident) for ident in receipt['external_sandboxes'].values())
    assert (skill / 'SKILL.md').read_text(encoding='utf-8') == '# Global collected skill'
    assert (source / 'workspace' / 'structure.cif').read_bytes() == b'data_test\n'
    assert services.world.get_card(unrelated.json()['id']).name == 'Existing unrelated work'
    again = client.portal.call(run)
    assert again['sessions_added'] == again['messages_added'] == 0


def test_reimport_preserves_cards_when_only_the_imported_group_was_removed(client, source):
    services = client.app.state.services
    manifest = migration.discover(source)
    first = client.portal.call(import_into, services, manifest, source / 'workspace')
    group = first['node_ids']['group']
    for member in services.world.list_members(group):
        detached = client.patch(f'/api/nodes/{member.id}', json={'parent_id': None})
        assert detached.status_code == 200, detached.text
    removed = client.delete(f'/api/nodes/{group}')
    assert removed.status_code == 200, removed.text
    before = {node.id for node in services.world.list_cards()}
    with pytest.raises(ConflictError, match='some of its cards still exist'):
        client.portal.call(import_into, services, manifest, source / 'workspace')
    assert {node.id for node in services.world.list_cards()} == before
    assert any(s.title == 'Old research' for s in services.conversations.list_sessions(first['node_ids']['conversation']))


@pytest.mark.parametrize("phase", ["agent_message", "source_attachment"])
def test_failure_rolls_back_records(client, source, monkeypatch, phase):
    services = client.app.state.services
    before = {node.id for node in services.world.list_cards()}
    original = services.conversations.add_message
    before_artifacts = set(services.resources.artifacts.root.iterdir())
    def fail(*args, **kwargs):
        if ((phase == "agent_message" and kwargs.get("sender_kind") == "agent") or
                (phase == "source_attachment" and kwargs.get("content", "").startswith("Original knowledge records"))):
            raise RuntimeError("injected persistence failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(services.conversations, "add_message", fail)
    with pytest.raises(RuntimeError, match="injected"):
        client.portal.call(import_into, services, migration.discover(source), source / "workspace")
    assert {node.id for node in services.world.list_cards()} == before
    assert set(services.resources.artifacts.root.iterdir()) == before_artifacts
    with services.database.locked() as db:
        assert db.execute("SELECT count(*) FROM application_settings WHERE key LIKE 'migration.matcreator.%'").fetchone()[0] == 0


def test_offline_import_refuses_running_profile(client, source):
    root = client.app.state.services.settings.data_root
    with pytest.raises(Exception, match="in use"):
        asyncio.run(import_migration(migration.discover(source), root, workspace=source / "workspace"))
    assert not (root / "backups" / "matcreator").exists()


def test_offline_package_roundtrip_and_database_backup(source, tmp_path):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    root = tmp_path / "destination-profile"
    settings = Settings.for_data_root(root)
    with TestClient(create_app(settings)):
        pass
    package = tmp_path / "portable.zip"
    # A copied exporter must work without site-packages or the OAW interpreter path.
    copied_script = tmp_path / "migrate.py"
    copied_script.write_bytes(SCRIPT.read_bytes())
    exported = subprocess.run([sys.executable, "-I", "-S", str(copied_script), "export", "--source", str(source),
        "--output", str(package)], capture_output=True, text=True)
    assert exported.returncode == 0, exported.stderr
    source.rename(tmp_path / "source-offline")
    destination = tmp_path / "received-files"
    arguments = ["import", "--package", str(package), "--files-to", str(destination), "--data-root", str(root), "--read-only"]
    # A failed target selection leaves complete files that the same package can reuse.
    assert migration.main([*arguments, "--group-id", "missing-group"]) == 1
    assert (destination / "structure.cif").is_file()
    imported = subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=tmp_path, capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr
    backups = list((root / "backups" / "matcreator").glob("*.sqlite3"))
    assert len(backups) == 2
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT count(*) FROM application_settings WHERE key LIKE 'migration.matcreator.%'").fetchone()[0] == 0
    assert migration.main(arguments) == 0
    with TestClient(create_app(settings)) as reopened:
        services = reopened.app.state.services
        with services.database.locked() as db:
            receipt = json.loads(db.execute("SELECT value_json FROM application_settings WHERE key LIKE 'migration.matcreator.%'").fetchone()[0])
        ids = receipt["node_ids"]
        sandbox = services.world.get_card(ids["sandbox"])
        assert sandbox.config["workspace_path"] == str(destination)
        assert sandbox.config["workspace_access"] == "read_only"
        session = next(s for s in services.conversations.list_sessions(ids["conversation"]) if s.title == "Old research")
        messages = services.conversations.context_messages(ids["conversation"], session.id)
        assert messages[0].content == str(destination / "structure.cif")
        assert (destination / "structure.cif").read_bytes() == b"data_test\n"
        assert len(messages) == 3
        assert not services.run_manager.list_runs()


def test_external_collected_skills_bind_locally_and_package_independently(client, source, tmp_path):
    root = source / "skills"
    skill = root / "collected"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Collected skill\nRead model.dat", encoding="utf-8")
    with (skill / "model.dat").open("wb") as stream:
        stream.truncate(2 * 1024 * 1024)
    manifest = migration.discover(source)
    external = {s["id"]: {"root": s["source_root"], "path": s["source_path"]} for s in manifest["skills"]
        if not Path(s["source_path"]).is_relative_to(source / "workspace")}
    services = client.app.state.services
    result = client.portal.call(partial(import_into, services, manifest, source / "workspace", external_skills=external))
    bound = [n for n in services.world.descendants(result["node_ids"]["group"]) if n.type == "sandbox" and n.config.get("workspace_path") == str(root)]
    assert len(bound) == 1 and bound[0].config["workspace_access"] == "read_only"
    assert any(e.target == bound[0].id and e.source == result["node_ids"]["agent"] for e in services.world.list_edges())
    again = client.portal.call(partial(import_into, services, manifest, source / "workspace", external_skills=external))
    assert again["knowledge_added"] == 0
    package = tmp_path / "collected.zip"
    exported = migration.export_package(manifest, package)
    virtual = next(s["path"] for s in exported["skills"] if s["name"] == "collected")
    assert any(f["path"] == virtual + "/model.dat" for f in exported["files"])
    with zipfile.ZipFile(package) as archive:
        restored = migration.extract_workspace(archive, migration.read_package(archive), tmp_path / "portable-collected")
    graph, warnings = knowledge_document(exported, restored)
    assert any(s["name"] == "collected" for s in graph["skills"])
    assert (restored / virtual / "model.dat").stat().st_size == 2 * 1024 * 1024
    assert any("model.dat" in warning for warning in warnings)


def test_container_alias_mapping_and_missing_artifact_preview(source):
    with closing(sqlite3.connect(source / ".adk" / "session.db")) as db, db:
        event = {"id": "artifact", "author": "execution_agent", "content": {"parts": [
            {"function_response": {"response": {"artifact_path": "/root/.matcreator/workspace/missing.cif"}}}]}}
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", ("artifact", "matcreator", "alice", "s1", 1785578500, json.dumps(event)))
    manifest = migration.discover(source, old_workspaces=["/root/.matcreator/workspace"])
    assert any("Missing artifact reference" in warning for warning in manifest["warnings"])
    assert remap_text(r"Read C:\research\project\structure.cif", r"C:\research", "/srv/project") == "Read /srv/project/project/structure.cif"
    assert migration.discover(source)["source_id"] == migration.discover(source, user_id="alice")["source_id"]


def test_large_history_is_partitioned_and_portable(client, source, tmp_path, monkeypatch):
    with closing(sqlite3.connect(source / ".adk" / "session.db")) as db, db:
        for index in range(8):
            event = {"id": f"large-{index}", "author": "execution_agent", "actions": {"state_delta": {"large": "x" * 1500}}}
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (event["id"], "matcreator", "alice", "s1", 1785578500 + index, json.dumps(event)))
    monkeypatch.setattr(migration, "HISTORY_CHUNK_BYTES", 4096)
    manifest = migration.discover(source)
    assert "state_delta" not in json.dumps(manifest)
    parts = list(migration.history_archives(manifest["sessions"][0]))
    assert len(parts) >= 3
    originals = [json.loads(b"".join(chunks)) for _, chunks in parts]
    events = [event for part in originals for event in part["events"]]
    assert len(events) == sum(part["event_count"] for part in originals) == 11
    assert [event["id"] for event in events] == ["e0", "e1", "e2", *[f"large-{i}" for i in range(8)]]
    services = client.app.state.services
    result = client.portal.call(partial(import_into, services, manifest, source / "workspace", copy_history=True))
    assert result["history_parts_added"] == len(parts)
    session = next(s for s in services.conversations.list_sessions(result["node_ids"]["conversation"]) if s.title == "Old research")
    messages = services.conversations.context_messages(result["node_ids"]["conversation"], session.id)
    saved = [json.loads((services.resources.artifacts.path(a.version_id) / a.path).read_bytes())
        for message in messages for a in message.attachments]
    assert [event for part in saved for event in part["events"]] == events
    package = tmp_path / "large-history.zip"
    migration.export_package(manifest, package)
    source.rename(tmp_path / "old-unavailable")
    with zipfile.ZipFile(package) as archive:
        received = migration.read_package(archive)
        # Packaged history never reads machine paths supplied in its manifest.
        received["sessions"][0]["legacy"]["database"] = "not-a-source-database"
        portable = [json.loads(b"".join(chunks)) for _, chunks in migration.packaged_history_archives(archive, received["sessions"][0])]
        assert [event for part in portable for event in part["events"]] == events


def test_changing_source_history_rolls_back_partial_archive_import(client, source, monkeypatch):
    services = client.app.state.services
    before = {node.id for node in services.world.list_cards()}
    before_artifacts = set(services.resources.artifacts.root.iterdir())
    manifest = migration.discover(source)
    with closing(sqlite3.connect(source / ".adk" / "session.db")) as db, db:
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", ("late", "matcreator", "alice", "s1", 1785578500,
            json.dumps({"id": "late", "author": "execution_agent"})))
    monkeypatch.setattr(migration, "HISTORY_CHUNK_BYTES", 512)
    with pytest.raises(ValueError, match="Source history changed"):
        client.portal.call(partial(import_into, services, manifest, source / "workspace", copy_history=True))
    assert {node.id for node in services.world.list_cards()} == before
    assert set(services.resources.artifacts.root.iterdir()) == before_artifacts


def test_space_budget_refuses_export_before_writing(source, tmp_path, monkeypatch):
    manifest = migration.discover(source)
    package = tmp_path / "too-little-space.zip"
    usage = migration.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(migration.shutil, "disk_usage", lambda path: usage._replace(free=migration.DISK_RESERVE_BYTES))
    with pytest.raises(ValueError, match="Insufficient disk space"):
        migration.export_package(manifest, package)
    assert not package.exists()
    assert (source / "workspace" / "structure.cif").read_bytes() == b"data_test\n"


def test_export_skips_download_markers_and_rejects_case_collisions(source, tmp_path, monkeypatch):
    workspace = source / "workspace"
    monkeypatch.setattr(migration.os, "walk", lambda root, followlinks=False: iter([(str(workspace), [], ["structure.cif", "structure.cif:Zone.Identifier"])]))
    files, warnings = migration.inventory(workspace)
    assert [item["path"] for item in files] == ["structure.cif"]
    assert any("Zone.Identifier" in warning for warning in warnings)
    manifest = migration.discover(source, skills_dirs=[])
    manifest["skills"] = []
    manifest["files"] = [*files, {**files[0], "path": "STRUCTURE.cif"}]
    monkeypatch.setattr(migration, "package_inventory", lambda *args: manifest)
    package = tmp_path / "case-collision.zip"
    with pytest.raises(ValueError, match="collide on Windows"):
        migration.export_package(manifest, package)
    assert not package.exists()


def test_space_budget_refuses_import_before_backup(client, source, monkeypatch):
    root = client.app.state.services.settings.data_root
    usage = migration.shutil.disk_usage(root)
    monkeypatch.setattr(migration.shutil, "disk_usage", lambda path: usage._replace(free=migration.DISK_RESERVE_BYTES))
    with pytest.raises(ValueError, match="Insufficient disk space"):
        asyncio.run(import_migration(migration.discover(source), root, workspace=source / "workspace"))
    assert not (root / "backups" / "matcreator").exists()


def test_wsl_metadata_transport_uses_structured_arguments_and_original_locations(source, monkeypatch):
    manifest = migration.discover(source)
    native = PurePosixPath("/home/user name/.matcreator")
    manifest["source_home"] = str(native)
    manifest["workspace"] = str(native / "workspace")
    manifest["workspace_aliases"] = [manifest["workspace"]]
    for skill in manifest["skills"]:
        skill["source_root"] = str(native / Path(skill["source_root"]).relative_to(source).as_posix())
        skill["source_path"] = str(native / Path(skill["source_path"]).relative_to(source).as_posix())
    for session in manifest["sessions"]:
        session["legacy"]["database"] = str(native / ".adk/session.db")
        session["legacy"]["agent_graph_path"] = str(native / ".adk/agent_graphs/s1.json")
    calls = []
    def native_call(distribution, arguments):
        calls.append((distribution, arguments))
        output = "/mnt/d/pack/migrate.py\n" if arguments[0] == "wslpath" else json.dumps(manifest)
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")
    monkeypatch.setattr(migration, "wsl_call", native_call)
    result = migration.discover_wsl(r"\\wsl.localhost\Ubuntu\home\user name\.matcreator")
    assert result["source_id"] == manifest["source_id"]
    assert result["workspace"] == r"\\wsl.localhost\Ubuntu\home\user name\.matcreator\workspace"
    assert result["sessions"][0]["legacy"]["database"] == r"\\wsl.localhost\Ubuntu\home\user name\.matcreator\.adk\session.db"
    assert result["native_source"]["distribution"] == "Ubuntu"
    options = json.loads(calls[-1][1][-1])
    assert options["source"] == str(native)
    assert calls[-1][1][:2] == ["python3", "-B"] and "--read-source" in calls[-1][1]
    with pytest.raises(ValueError, match="same WSL distribution"):
        migration.wsl_argument("Ubuntu", r"\\wsl.localhost\Other\home\user\.matcreator")
