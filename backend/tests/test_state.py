from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import backend.state as state_contract
from backend.agents import AgentConfig, AgentEvent, MockAgentRuntime
from backend.config import Settings
from backend.errors import NotFoundError, PermissionDeniedError, RevisionConflictError
from backend.events import EventType
from backend.persistence.database import Database
from backend.plugins import create_builtin_registry
from backend.runs import InvocationContext, RuntimeInput
from backend.services import create_services
from backend.state import (
    MergePolicy,
    StateContext,
    StateFieldDefinition,
    StateSchema,
    StateScopeRef,
    StateStore,
)
from backend.tests.plugin_support import install_test_plugin
from backend.world.models import CardCreate


@pytest.fixture
def state_store(tmp_path: Path) -> StateStore:
    database = Database(tmp_path / "state.sqlite3")
    store = StateStore(database, create_builtin_registry())
    yield store
    database.close()


def test_scope_identity_explicit_writes_resolution_and_snapshot(
    state_store: StateStore,
) -> None:
    world = state_store.ensure_scope(StateScopeRef("world", "default"))
    assert state_store.ensure_scope("world", "default").scope_id == world.scope_id

    agent = state_store.ensure_scope("agent", "agent-1")
    session = state_store.ensure_scope("session", "session-1")
    run = state_store.ensure_scope("run", "run-1")
    context = StateContext([world, agent, session, run])

    state_store.set(world, "workspace", {"owner": "world"})
    state_store.set(agent, "workspace", {"owner": "agent"})
    state_store.set(session, "workspace", {"owner": "session"})
    resolved = state_store.resolve(context, "workspace")
    assert resolved.value == {"owner": "session"}
    assert resolved.source_scope.scope_id == session.scope_id
    assert resolved.revision == 1

    state_store.set(run, "workspace", {"owner": "run"})
    assert state_store.resolve(context, "workspace").source_scope.scope_id == run.scope_id
    assert state_store.get(session, "workspace") == {"owner": "session"}

    snapshot = state_store.snapshot(context)
    assert snapshot.scope_stack[-1].scope_id == run.scope_id
    assert snapshot.values["workspace"].value == {"owner": "run"}
    assert snapshot.values["workspace"].source_scope.scope_id == run.scope_id
    assert snapshot.values["workspace"].revision == 1


def test_builtin_run_schema_is_generic_and_all_state_is_durable() -> None:
    registry = create_builtin_registry()
    run_fields = registry.state_schema("core.run").fields
    assert "completed" not in run_fields
    assert "execution_graph" not in run_fields
    assert not hasattr(state_contract, "StateDurability")
    assert not hasattr(next(iter(run_fields.values())), "durability")


def test_merge_policies_revision_conflicts_and_permissions(tmp_path: Path) -> None:
    registry = create_builtin_registry()
    schema = StateSchema(id="example.runtime", fields={
        "replace": StateFieldDefinition(value_type=dict[str, int]),
        "mapping": StateFieldDefinition(
            value_type=dict[str, int], merge_policy=MergePolicy.MERGE_DICT
        ),
        "items": StateFieldDefinition(
            value_type=list[str], merge_policy=MergePolicy.APPEND
        ),
        "unique": StateFieldDefinition(
            value_type=list[str], merge_policy=MergePolicy.APPEND_UNIQUE
        ),
        "restricted": StateFieldDefinition(
            value_type=str, write_permissions=frozenset({"example.write"})
        ),
    })
    install_test_plugin(
        registry,
        "example.runtime-plugin",
        lambda registration: registration.register_state_schema(schema),
    )
    database = Database(tmp_path / "merges.sqlite3")
    store = StateStore(database, registry)
    try:
        scope = store.ensure_scope("plugin", "example-1", schema_id="example.runtime")
        first = store.set(scope, "replace", {"one": 1}, expected_revision=0)
        second = store.set(
            scope, "replace", {"two": 2}, expected_revision=first.revision
        )
        assert second.value == {"two": 2}
        with pytest.raises(RevisionConflictError):
            store.set(scope, "replace", {}, expected_revision=first.revision)

        store.patch(scope, "mapping", {"one": 1})
        assert store.patch(scope, "mapping", {"two": 2}).value == {
            "one": 1,
            "two": 2,
        }
        store.patch(scope, "items", "one")
        assert store.patch(scope, "items", ["two"]).value == ["one", "two"]
        store.patch(scope, "unique", ["one", "one"])
        assert store.patch(scope, "unique", ["one", "two"]).value == ["one", "two"]

        api = store.api(StateContext([scope]))
        with pytest.raises(PermissionDeniedError):
            api.set(scope, "restricted", "no")
        permitted = store.api(
            StateContext([scope]), permissions={"example.write"}
        )
        assert permitted.set(scope, "restricted", "yes").value == "yes"
    finally:
        database.close()


def test_plugin_schema_registration_and_persistence_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "persistent.sqlite3"
    registry = create_builtin_registry()
    schema = StateSchema(id="acme.research", fields={
        "findings": StateFieldDefinition(value_type=list[str])
    })
    install_test_plugin(
        registry,
        "acme.research-plugin",
        lambda registration: registration.register_state_schema(schema),
    )
    assert registry.state_schema("acme.research") is schema

    database = Database(path)
    store = StateStore(database, registry)
    scope = store.ensure_scope("plugin.research", "case-7", schema_id="acme.research")
    store.set(scope, "findings", ["durable"])
    scope_id = scope.scope_id
    database.close()

    restarted_registry = create_builtin_registry()
    install_test_plugin(
        restarted_registry,
        "acme.research-plugin",
        lambda registration: registration.register_state_schema(schema),
    )
    restarted_database = Database(path)
    try:
        restarted = StateStore(restarted_database, restarted_registry)
        recovered = restarted.get_scope("plugin.research", "case-7")
        assert recovered.scope_id == scope_id
        assert restarted.get(recovered, "findings") == ["durable"]
    finally:
        restarted_database.close()


def test_context_derivation_projects_only_selected_parent_scopes(
    state_store: StateStore,
) -> None:
    world = state_store.ensure_scope("world", "default")
    parent_agent = state_store.ensure_scope("agent", "parent")
    parent_run = state_store.ensure_scope("run", "parent-run")
    child_agent = state_store.ensure_scope("agent", "child")
    child_run = state_store.ensure_scope("run", "child-run")

    parent = StateContext([world, parent_agent, parent_run])
    child = parent.derive(
        inherited_scopes=[world],
        additional_scopes=[child_agent, child_run],
    )
    assert [scope.identity for scope in child.scope_stack] == [
        ("world", "default"),
        ("agent", "child"),
        ("run", "child-run"),
    ]


class _CapturingRuntime(MockAgentRuntime):
    def __init__(self, capability_provider: object) -> None:
        super().__init__(capability_provider)  # type: ignore[arg-type]
        self.contexts: list[InvocationContext] = []

    async def execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
    ) -> AsyncIterator[AgentEvent]:
        self.contexts.append(context)
        async for event in super().execute(config, context, runtime_input):
            yield event


@pytest.mark.asyncio
async def test_run_scopes_are_automatic_independent_and_typed(tmp_path: Path) -> None:
    registry = create_builtin_registry()
    instances: list[_CapturingRuntime] = []

    def factory(capability_provider: object, **options: object) -> _CapturingRuntime:
        del options
        runtime = _CapturingRuntime(capability_provider)
        instances.append(runtime)
        return runtime

    install_test_plugin(
        registry,
        "test.capture-runtime",
        lambda registration: registration.register_runtime_provider(
            "test.capture", factory  # type: ignore[arg-type]
        ),
    )
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        default_runtime_provider_id="test.capture",
    )
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        agent_scope = services.state.get_scope("agent", agent.id)
        services.state.set(agent_scope, "memory", {"shared": True})

        first = await services.run_manager.start_run(  # type: ignore[union-attr]
            agent.id, "first", context_id="conversation-session-1"
        )
        await services.run_manager.wait_terminal(first.run_id)  # type: ignore[union-attr]
        second = await services.run_manager.start_run(  # type: ignore[union-attr]
            agent.id, "second", context_id="conversation-session-1"
        )
        await services.run_manager.wait_terminal(second.run_id)  # type: ignore[union-attr]

        first_scope = services.state.get_scope("run", first.run_id)
        second_scope = services.state.get_scope("run", second.run_id)
        assert first_scope.scope_id != second_scope.scope_id
        assert services.state.get(first_scope, "input") == "first"
        assert services.state.get(second_scope, "input") == "second"
        async with services.events.subscribe() as queue:
            services.state.set(
                first_scope,
                "progress",
                0.5,
                actor_id=agent.id,
                run_id=first.run_id,
            )
            mutation_event = queue.get_nowait()
        assert mutation_event.type is EventType.STATE_CREATED
        assert mutation_event.payload == {
            "scope_id": first_scope.scope_id,
            "scope_kind": "run",
            "owner_id": first.run_id,
            "key": "progress",
            "revision": 1,
            "actor_id": agent.id,
            "run_id": first.run_id,
        }
        assert services.state.get(first_scope, "progress") == 0.5
        with pytest.raises(NotFoundError):
            services.state.get(second_scope, "progress")
        assert services.state.get(agent_scope, "memory") == {"shared": True}

        assert len(instances) == 1
        assert len(instances[0].contexts) == 2
        for invocation in instances[0].contexts:
            assert isinstance(invocation.state_context, StateContext)
            assert [scope.scope_kind for scope in invocation.state_context.scope_stack] == [
                "world",
                "agent",
                "session",
                "run",
            ]
            assert invocation.state_context.local_scope.owner_id == invocation.run_id

        session_scope = services.state.get_scope("session", "conversation-session-1")
        services.state.set(session_scope, "workspace", "shared session")
        context = instances[0].contexts[-1].state_context
        assert context is not None
        assert services.state.resolve(context, "workspace").value == "shared session"
        services.state.set(context.local_scope, "workspace", "run-local")
        assert services.state.resolve(context, "workspace").value == "run-local"
        assert services.state.get(session_scope, "workspace") == "shared session"

        await services.delete_card(agent.id)
        with pytest.raises(NotFoundError):
            services.state.get_scope("agent", agent.id)
        assert services.state.get_scope("run", first.run_id).scope_id == first_scope.scope_id
    finally:
        services.close()
