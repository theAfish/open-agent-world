from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.conversations import ConversationSessionCreate
from backend.errors import NotFoundError, PermissionDeniedError
from backend.idempotency import IdempotencyConflictError, IdempotencyStore, InvalidIdempotencyKeyError
from backend.main import create_app
from backend.persistence import Database
from backend.request_context import ActorRef, RequestContext, TenantScope
from backend.tests.conftest import create_node


CONTEXT = RequestContext("request-1", ActorRef("local_host", "host"), TenantScope("org", "workspace", "world"), "local")


def authorize() -> None:
    pass


def execute(store, mutate, *, context=CONTEXT, payload=None, operation="test.create", key="retry-1", check=authorize):
    return store.execute(context, operation=operation, key=key, payload=payload or {"title": "Review"},
                         authorize=check, mutate=mutate)


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "replay.sqlite3")
    with database.locked() as connection:
        connection.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    yield IdempotencyStore(database)
    database.close()


def insert_effect(database, *, value="created"):
    with database.locked() as connection:
        row = connection.execute("INSERT INTO effects(value) VALUES (?) RETURNING id", (value,)).fetchone()
    return {"id": row["id"], "value": value}


def effect_count(database):
    with database.locked() as connection:
        return connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


def test_replay_is_stable_and_does_not_repeat_mutation(store):
    calls = []
    first = execute(store, lambda: insert_effect(store.database), check=lambda: calls.append("authorized"))
    replay = execute(store, lambda: pytest.fail("replay ran the mutation"),
                     context=replace(CONTEXT, request_id="request-2"), check=lambda: calls.append("authorized"))
    assert first.value == replay.value
    assert not first.replayed and replay.replayed
    assert calls == ["authorized", "authorized"]
    assert effect_count(store.database) == 1


def test_payload_hash_is_canonical_and_rejects_changed_payload(store):
    first = execute(store, lambda: insert_effect(store.database), payload={"b": [1, 2], "a": "Review"})
    replay = execute(store, lambda: pytest.fail("canonical replay mutated"), payload={"a": "Review", "b": [1, 2]})
    assert replay.value == first.value
    with pytest.raises(IdempotencyConflictError):
        execute(store, lambda: pytest.fail("conflict mutated"), payload={"a": "Changed", "b": [1, 2]})
    assert effect_count(store.database) == 1


@pytest.mark.parametrize("context,operation", [
    (replace(CONTEXT, actor=ActorRef("local_host", "other")), "test.create"),
    (replace(CONTEXT, actor=ActorRef("system", "host")), "test.create"),
    (replace(CONTEXT, tenant=TenantScope("other", "workspace", "world")), "test.create"),
    (replace(CONTEXT, tenant=TenantScope("org", "other", "world")), "test.create"),
    (replace(CONTEXT, tenant=TenantScope("org", "workspace", "other")), "test.create"),
    (CONTEXT, "test.other"),
])
def test_every_scope_dimension_isolates_keys(store, context, operation):
    first = execute(store, lambda: insert_effect(store.database))
    other = execute(store, lambda: insert_effect(store.database), context=context, operation=operation)
    assert first.value["id"] != other.value["id"]
    assert not other.replayed
    assert effect_count(store.database) == 2


@pytest.mark.parametrize("key", ["", "with space", "line\nbreak", "a" * 129, "非ASCII"])
def test_invalid_keys_do_not_mutate(store, key):
    with pytest.raises(InvalidIdempotencyKeyError):
        execute(store, lambda: pytest.fail("invalid key mutated"), key=key)


def test_authorization_is_rechecked_before_replay(store):
    execute(store, lambda: insert_effect(store.database))

    def deny():
        raise PermissionDeniedError("access revoked")

    with pytest.raises(PermissionDeniedError):
        execute(store, lambda: pytest.fail("denied mutation"), check=deny)


def test_failed_mutation_rolls_back_effect_and_key(store):
    def fail_after_write():
        insert_effect(store.database)
        raise RuntimeError("crash before replay record")

    with pytest.raises(RuntimeError, match="crash"):
        execute(store, fail_after_write)
    assert effect_count(store.database) == 0
    retry = execute(store, lambda: insert_effect(store.database))
    assert not retry.replayed
    assert effect_count(store.database) == 1


def test_failed_response_serialization_also_rolls_back(store):
    def invalid_response():
        insert_effect(store.database)
        return {"invalid": object()}

    with pytest.raises(TypeError):
        execute(store, invalid_response)
    assert effect_count(store.database) == 0
    assert not execute(store, lambda: insert_effect(store.database)).replayed


def test_async_callbacks_are_rejected_without_running(store):
    async def invalid_mutation():
        insert_effect(store.database)
        return {}

    with pytest.raises(TypeError, match="synchronous"):
        execute(store, invalid_mutation)
    assert effect_count(store.database) == 0


def test_store_refuses_to_leave_commit_to_an_outer_transaction(store):
    with store.database.transaction(immediate=True):
        with pytest.raises(RuntimeError, match="outer transaction"):
            execute(store, lambda: pytest.fail("nested mutation executed"))


def test_replay_survives_reopening_database(tmp_path):
    path = tmp_path / "restart.sqlite3"
    database = Database(path)
    with database.locked() as connection:
        connection.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    first = execute(IdempotencyStore(database), lambda: insert_effect(database))
    database.close()
    reopened = Database(path)
    try:
        replay = execute(IdempotencyStore(reopened), lambda: pytest.fail("restart repeated mutation"))
        assert replay.replayed and replay.value == first.value
        assert effect_count(reopened) == 1
    finally:
        reopened.close()


def test_competing_connections_create_one_effect(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    databases = [Database(path), Database(path)]
    stores = [IdempotencyStore(database) for database in databases]
    with databases[0].locked() as connection:
        connection.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    barrier = threading.Barrier(2)

    def attempt(index):
        def mutation():
            value = insert_effect(databases[index])
            time.sleep(0.05)  # Hold the writer lock while the other connection competes.
            return value
        barrier.wait(timeout=5)
        return execute(stores[index], mutation)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        assert results[0].value == results[1].value
        assert sorted(result.replayed for result in results) == [False, True]
        assert effect_count(databases[0]) == 1
    finally:
        for database in databases:
            database.close()


def test_session_api_replays_and_conflicts(client: TestClient, monkeypatch):
    conversation = create_node(client, "conversation")
    services = client.app.state.services
    original_publish = services.events.publish
    events = []

    async def publish(*args, **kwargs):
        events.append(args[0])
        await original_publish(*args, **kwargs)

    monkeypatch.setattr(services.events, "publish", publish)
    url = f"/api/conversations/{conversation['id']}/sessions"
    headers = {"Idempotency-Key": "session-create"}
    first = client.post(url, json={"title": "Review"}, headers=headers)
    replay = client.post(url, json={"title": "Review"}, headers=headers)
    conflict = client.post(url, json={"title": "Changed"}, headers=headers)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(events) == 1
    sessions = services.conversations.list_sessions(conversation["id"])
    assert len([session for session in sessions if session.title == "Review"]) == 1
    unkeyed = client.post(url, json={"title": "Review"})
    assert unkeyed.status_code == 201 and unkeyed.json()["id"] != first.json()["id"]


def test_session_api_replay_survives_application_restart(data_root: Path):
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        conversation = create_node(client, "conversation")
        url = f"/api/conversations/{conversation['id']}/sessions"
        first = client.post(url, json={"title": "Durable"}, headers={"Idempotency-Key": "restart"})
        assert first.status_code == 201
    with TestClient(create_app(settings)) as client:
        replay = client.post(url, json={"title": "Durable"}, headers={"Idempotency-Key": "restart"})
        assert replay.status_code == 201 and replay.json() == first.json()
        sessions = client.app.state.services.conversations.list_sessions(conversation["id"])
        assert len([session for session in sessions if session.title == "Durable"]) == 1


def test_session_api_rechecks_revoked_participant_permission(client: TestClient):
    conversation = create_node(client, "conversation")
    agent = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": conversation["id"],
                                          "relationship": "participate"})
    assert edge.status_code == 201
    url = f"/api/conversations/{conversation['id']}/sessions"
    body = {"title": "Restricted", "participant_ids": [agent["id"]]}
    first = client.post(url, json=body, headers={"Idempotency-Key": "revoked"})
    assert first.status_code == 201
    assert client.delete(f"/api/edges/{edge.json()['id']}").status_code == 200
    replay = client.post(url, json=body, headers={"Idempotency-Key": "revoked"})
    assert replay.status_code == 403


def test_event_failure_after_commit_does_not_duplicate_session_on_retry(client: TestClient, monkeypatch):
    conversation = create_node(client, "conversation")
    services = client.app.state.services
    attempts = []

    async def fail_publish(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("event failure after commit")

    monkeypatch.setattr(services.events, "publish", fail_publish)
    url = f"/api/conversations/{conversation['id']}/sessions"
    with pytest.raises(RuntimeError, match="event failure after commit"):
        client.post(url, json={"title": "Committed"}, headers={"Idempotency-Key": "event-failure"})
    replay = client.post(url, json={"title": "Committed"}, headers={"Idempotency-Key": "event-failure"})
    assert replay.status_code == 201
    assert len(attempts) == 1
    sessions = services.conversations.list_sessions(conversation["id"])
    assert len([session for session in sessions if session.title == "Committed"]) == 1


def test_session_api_rejects_invalid_key(client: TestClient):
    conversation = create_node(client, "conversation")
    response = client.post(f"/api/conversations/{conversation['id']}/sessions",
                           json={"title": "Invalid"}, headers={"Idempotency-Key": "contains spaces"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "foreign-scope"])
async def test_session_service_rejects_foreign_tenant_before_resource_lookup(client: TestClient, monkeypatch, key):
    conversation = create_node(client, "conversation")
    services = client.app.state.services

    def unexpected_lookup(*args, **kwargs):
        pytest.fail("looked up a conversation before rejecting the unsupported tenant")

    monkeypatch.setattr(type(services), "_require_card_type", unexpected_lookup)
    with pytest.raises(NotFoundError):
        await services.create_conversation_session(
            conversation["id"], ConversationSessionCreate(title="Foreign"),
            idempotency_key=key, request_context=CONTEXT,
        )


def test_session_and_scope_rollback_together(client: TestClient, monkeypatch):
    conversation = create_node(client, "conversation")
    services = client.app.state.services
    original = services.state.ensure_scope

    def fail(*args, **kwargs):
        raise RuntimeError("scope creation failed")

    monkeypatch.setattr(services.state, "ensure_scope", fail)
    url = f"/api/conversations/{conversation['id']}/sessions"
    with pytest.raises(RuntimeError, match="scope creation failed"):
        client.post(url, json={"title": "Atomic"}, headers={"Idempotency-Key": "atomic"})
    assert not any(session.title == "Atomic" for session in services.conversations.list_sessions(conversation["id"]))
    monkeypatch.setattr(services.state, "ensure_scope", original)
    retry = client.post(url, json={"title": "Atomic"}, headers={"Idempotency-Key": "atomic"})
    assert retry.status_code == 201
