"""Operation tools aggregate live targets without turning selectors into authority."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.agents.tools import build_scoped_tool_callables, build_scoped_tool_schemas
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import GraphValidationError, PermissionDeniedError, ResourceValidationError
from backend.main import create_app
from backend.plugins import CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, PluginDefinition, PluginDescriptor, RelationshipDefinition
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.test_plugin_registry import DatasetConfig
from backend.tests.test_skill_packages import edit
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def connect(client, agent, target, relationship):
    response = client.post("/api/edges", json={"source": agent["id"], "target": target["id"], "relationship": relationship})
    assert response.status_code == 201, response.text
    return response.json()


def tools(client, agent):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent["id"])
    return provider, {d.name: d for d in definitions}


def invoke(client, provider, agent, definition, **arguments):
    return client.portal.call(provider.invoke_tool, agent["id"], definition.capability_id, arguments)


def test_multiple_targets_have_one_semantic_tool_and_explicit_selectors(client):
    agent = create_node(client, "agent")
    a = create_node(client, "text", name="File A", content="alpha")
    b = create_node(client, "text", name="File B", content="beta")
    for target in (a, b):
        connect(client, agent, target, "read_edit")
    provider, definitions = tools(client, agent)
    assert set(definitions) == {"read_text", "edit_text"}
    reader = definitions["read_text"]
    assert reader.capability_id == "operation:read_text"
    assert reader.input_schema["properties"]["target"]["enum"] == ["file_a", "file_b"]
    assert reader.input_schema["required"] == ["target"]
    assert invoke(client, provider, agent, reader, target="file_a")["content"] == "alpha"
    assert invoke(client, provider, agent, reader, target="File B")["content"] == "beta"
    invoke(client, provider, agent, definitions["edit_text"], target="file_b", content="updated")
    assert invoke(client, provider, agent, reader, target=a["id"])["content"] == "alpha"
    assert invoke(client, provider, agent, reader, target=b["id"])["content"] == "updated"
    with pytest.raises(ResourceValidationError, match="selector"):
        invoke(client, provider, agent, reader)
    assert {c.tool_name for c in client.app.state.services.capabilities.derive(agent["id"]).capabilities} == set(definitions)


def test_duplicate_names_are_qualified_deterministically_and_never_pick_first(client):
    agent = create_node(client, "agent")
    a = create_node(client, "text", name="Notes", content="alpha")
    b = create_node(client, "text", name="Notes", content="beta")
    edges = [connect(client, agent, target, "read") for target in (a, b)]
    provider, definitions = tools(client, agent)
    reader = definitions["read_text"]
    aliases = reader.input_schema["properties"]["target"]["enum"]
    assert len(aliases) == 2 and all(alias.startswith("notes__") for alias in aliases)
    assert {invoke(client, provider, agent, reader, target=alias)["content"] for alias in aliases} == {"alpha", "beta"}
    with pytest.raises(ResourceValidationError, match="Ambiguous"):
        invoke(client, provider, agent, reader, target="Notes")
    for edge in edges:
        assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    for target in (b, a):
        connect(client, agent, target, "read")
    assert tools(client, agent)[1]["read_text"].input_schema["properties"]["target"]["enum"] == aliases
    assert client.patch(f"/api/nodes/{b['id']}", json={"name": "Archive"}).status_code == 200
    assert set(tools(client, agent)[1]["read_text"].input_schema["properties"]["target"]["enum"]) == {"notes", "archive"}
    for stale in aliases:
        with pytest.raises(PermissionDeniedError):
            invoke(client, provider, agent, reader, target=stale)


def test_exposed_callable_and_modified_schema_cannot_bypass_live_revocation(client):
    agent = create_node(client, "agent")
    note = create_node(client, "text", name="Private", content="secret")
    other = create_node(client, "text", name="Other", content="public")
    hidden = create_node(client, "text", name="Hidden", content="unavailable")
    edge = connect(client, agent, note, "read_edit")
    connect(client, agent, other, "read_edit")
    provider, definitions = tools(client, agent)
    callables = {f.__name__: f for f in build_scoped_tool_callables(provider, agent["id"], list(definitions.values()))}
    assert client.portal.call(lambda: callables["read_text"](target="private"))["content"] == "secret"
    assert client.patch(f"/api/edges/{edge['id']}", json={"relationship": "read"}).status_code == 200
    denied = client.portal.call(lambda: callables["edit_text"](target="private", content="overwrite"))
    assert denied["error"]["code"] == "permission_denied"
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    definitions["read_text"].input_schema["properties"]["target"]["enum"].append(hidden["id"])
    for selector in ("private", note["id"], hidden["id"]):
        denied = client.portal.call(lambda: callables["read_text"](target=selector))
        assert denied["error"]["code"] == "permission_denied"
    assert client.portal.call(lambda: callables["read_text"](target="other"))["content"] == "public"
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent["id"], f"text.read:{note['id']}", {})


@pytest.mark.parametrize("equipment,toolbox", [(False, False), (True, True)])
def test_composite_tool_has_independent_selectors_without_cartesian_names(runtime_client, equipment, toolbox):
    client, _, native = runtime_client
    agent, sandbox, skill, access, edges = setup_skill(client, equipment=equipment, toolbox=toolbox)
    assert client.patch(f"/api/nodes/{sandbox['id']}", json={"name": "Python"}).status_code == 200
    assert client.patch(f"/api/nodes/{skill['id']}", json={"name": "Phonon"}).status_code == 200
    second = create_node(client, "sandbox", name="Alternative")
    sandbox_edge = connect(client, agent, second, "execute")
    assert client.post(f"/api/sandboxes/{second['id']}/start").status_code == 200
    second_skill = create_node(client, "oaw.skills.skill", name="Converter")
    edit(client, second_skill, "replace", {"files": {"scripts/check.py": "print('second')"}})
    connect(client, agent, second_skill, "oaw.skills.skill.use")
    unrelated = create_node(client, "oaw.skills.skill", name="Unrelated")
    provider, definitions = tools(client, agent)
    tool = definitions["run_skill_script"]
    assert sum(name.startswith("run_skill_script") for name in definitions) == 1
    properties = tool.input_schema["properties"]
    assert set(properties["skill"]["enum"]) == {"phonon", "converter"}
    assert set(properties["sandbox"]["enum"]) == {"python", "alternative"}
    assert "skill_id" not in properties and "script" in properties
    for selected_skill, selected_sandbox in [("phonon", "python"), ("converter", "alternative")]:
        result = invoke(client, provider, agent, tool, skill=selected_skill, sandbox=selected_sandbox,
            script="scripts/check.py", interpreter=["python"], argv=[])
        assert result["exit_code"] == 0
    assert second["id"] in native.last_argv[1] and second_skill["id"] in native.last_argv[1]
    for selector in ("unrelated", unrelated["id"]):
        with pytest.raises(PermissionDeniedError):
            invoke(client, provider, agent, tool, skill=selector, sandbox="python", script="scripts/check.py")
    if equipment:
        assert client.patch(f"/api/nodes/{access['id']}", json={"equipment": None}).status_code == 200
    else:
        assert client.delete(f"/api/edges/{edges[1]['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, tool, skill="phonon", sandbox="python", script="scripts/check.py")
    assert client.delete(f"/api/edges/{sandbox_edge['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, tool, skill="converter", sandbox="alternative", script="scripts/check.py")


def test_sandbox_alone_does_not_expose_composite_tool(client):
    agent = create_node(client, "agent")
    sandbox = create_node(client, "sandbox")
    connect(client, agent, sandbox, "execute")
    assert set(tools(client, agent)[1]) == {"execute_command", "inspect_sandbox"}


def test_shared_plugin_operation_dispatches_the_selected_kind_and_preserves_schema(tmp_path):
    registry = load_plugin_registry()
    schema = {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
              "required": ["queries"], "additionalProperties": False}
    async def handler(context, capability, arguments):
        return {"kind": capability.kind, "target": capability.target_id, "arguments": arguments}
    def register(registration):
        for name in ("alpha", "beta"):
            kind = f"example.{name}.query"
            registration.register_node_type(replace(registry.node_type("text"), id=f"example.{name}",
                lifecycle=None, config_model=DatasetConfig, traits=frozenset(), default_status="available", statuses=frozenset({"available"})))
            registration.register_capability(CapabilityDefinition(kind, "query_data", "Query an authorized data source.", schema), handler)
            registration.register_relationship(RelationshipDefinition(id=f"example.{name}.use", label="Query", short_label="query",
                description="Query a data source", source_traits=frozenset({"core.agent"}), target_types=frozenset({f"example.{name}"}),
                capabilities=(CapabilityGrantDefinition(kind),)))
    registry.install(PluginDefinition(PluginDescriptor(id="example.sources", version="1", plugin_api_version="1.10"), register))
    settings = Settings.for_data_root(tmp_path / "world")
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        agent = create_node(client, "agent")
        for name in ("alpha", "beta"):
            target = create_node(client, f"example.{name}", name=name)
            connect(client, agent, target, f"example.{name}.use")
        provider, definitions = tools(client, agent)
        assert set(definitions) == {"query_data"}
        for name in ("alpha", "beta"):
            result = invoke(client, provider, agent, definitions["query_data"], target=name, queries=["hello"])
            assert result["kind"] == f"example.{name}.query" and result["arguments"] == {"queries": ["hello"]}
        built = build_scoped_tool_schemas(list(definitions.values()))[0]["function"]["parameters"]
        assert built["properties"]["queries"] == schema["properties"]["queries"]
        assert built["properties"]["target"]["enum"] == ["alpha", "beta"]
    def conflicting(registration):
        registration.register_capability(CapabilityDefinition("example.conflict", "query_data", "Different contract", {}), handler)
    with pytest.raises(ValueError, match="Conflicting contracts"):
        registry.install(PluginDefinition(PluginDescriptor(id="example.conflict", version="1", plugin_api_version="1.10"), conflicting))
    assert not registry.has_plugin("example.conflict")
    with pytest.raises(GraphValidationError):
        registry.capability_definition("example.conflict")


def test_legacy_plugin_grants_are_normalized_to_one_operation(client):
    agent = create_node(client, "agent")
    for name in ("Work A", "Work B"):
        board = create_node(client, "oaw.tasks", name=name)
        connect(client, agent, board, "oaw.tasks.edit")
    provider, definitions = tools(client, agent)
    assert "read_tasks" in definitions and "write_tasks" in definitions
    assert not any(name.startswith("read_tasks_") for name in definitions)
    for alias in ("work_a", "work_b"):
        assert invoke(client, provider, agent, definitions["read_tasks"], target=alias)["value"]["tasks"] == []


def test_plugin_composite_scopes_are_enforced_for_operation_and_legacy_ids(client):
    registry = client.app.state.services.plugins
    calls = []

    async def combine(context, capability, arguments):
        calls.append((capability.target_id, arguments))
        return arguments

    def register(registration):
        registration.register_capability(CapabilityDefinition(
            "example.combine", "combine_text", "Combine authorized texts.",
            selectors=(CapabilitySelector("source", "source_id", capability_kinds=frozenset({"text.read"})),),
            target_capabilities=frozenset({"text.edit"})), combine)
        registration.register_relationship(RelationshipDefinition(
            id="example.combine", label="Combine", short_label="combine", description="Combine texts",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"text"}),
            capabilities=(CapabilityGrantDefinition("example.combine"), CapabilityGrantDefinition("text.read"),
                          CapabilityGrantDefinition("text.edit"))))
        registration.register_relationship(RelationshipDefinition(
            id="example.combine_only", label="Combine only", short_label="combine", description="No write grant",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"text"}),
            capabilities=(CapabilityGrantDefinition("example.combine"), CapabilityGrantDefinition("text.read"))))

    registry.install(PluginDefinition(PluginDescriptor(id="example.combine", version="1", plugin_api_version="1.10"), register))
    agent = create_node(client, "agent")
    target = create_node(client, "text", name="Output")
    source = create_node(client, "text", name="Input")
    hidden = create_node(client, "text", name="Hidden")
    target_edge = connect(client, agent, target, "example.combine")
    source_edge = connect(client, agent, source, "read")
    provider, definitions = tools(client, agent)
    operation = definitions["combine_text"]
    assert invoke(client, provider, agent, operation, target="output", source="input") == {"source_id": source["id"]}
    legacy = f"example.combine:{target['id']}"
    assert client.portal.call(provider.invoke_tool, agent["id"], legacy, {"source_id": source["id"]}) == {"source_id": source["id"]}
    for selector in (source["id"], hidden["id"]):
        if selector == source["id"]:
            assert client.delete(f"/api/edges/{source_edge['id']}").status_code == 200
        with pytest.raises(PermissionDeniedError):
            invoke(client, provider, agent, operation, target="output", source=selector)
        with pytest.raises(PermissionDeniedError):
            client.portal.call(provider.invoke_tool, agent["id"], legacy, {"source_id": selector})
    assert len(calls) == 2
    assert client.patch(f"/api/edges/{target_edge['id']}", json={"relationship": "example.combine_only"}).status_code == 200
    assert "combine_text" not in tools(client, agent)[1]
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, operation, target="output", source="output")
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent["id"], legacy, {"source_id": target["id"]})
    assert len(calls) == 2


@pytest.mark.parametrize("selectors,required", [
    ((CapabilitySelector("source", "target"),), frozenset()),
    ((CapabilitySelector("source", "destination"), CapabilitySelector("destination", "dest_id")), frozenset()),
    ((CapabilitySelector("source", "source_id", include_members=True),), frozenset()),
    ((CapabilitySelector("source", "source_id", capability_kinds=frozenset({"missing.kind"})),), frozenset()),
    ((), frozenset({"missing.kind"})),
])
def test_invalid_composite_contracts_fail_registration_atomically(selectors, required):
    registry = load_plugin_registry()
    async def handler(context, capability, arguments):
        raise AssertionError("invalid operation must never be installed")
    def register(registration):
        registration.register_capability(CapabilityDefinition("example.invalid", "invalid_tool", "Invalid",
            selectors=selectors, target_capabilities=required), handler)
    with pytest.raises(ValueError):
        registry.install(PluginDefinition(PluginDescriptor(id="example.invalid", version="1", plugin_api_version="1.10"), register))
    assert not registry.has_plugin("example.invalid")
