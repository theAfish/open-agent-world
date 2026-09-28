"""Model-facing ADK contracts must retain the host's nested JSON schemas."""
import asyncio
from copy import deepcopy

from google.adk.models.lite_llm import _function_declaration_to_tool_param

from backend.agents.tools import _inline_schema_refs, build_scoped_adk_tools
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.tests.conftest import create_node
from backend.tests.test_agent_runtime import MutableCapabilityProvider
from backend.tests.test_library_intake import read


def atom_tools(client):
    agent = create_node(client, "agent")
    structure = create_node(client, "atomsculptor.structure", name="Example structure")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": structure["id"],
                                          "relationship": "atomsculptor.structure.modify"}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent["id"])
    tools = {tool.name: tool for tool in build_scoped_adk_tools(provider, agent["id"], definitions)}
    return agent, structure, edge, definitions, tools


def test_litellm_receives_complete_structure_contract_and_target_enum(client):
    _, structure, _, definitions, tools = atom_tools(client)
    original = next(item for item in definitions if item.name == "replace_atom_structure").input_schema
    before = deepcopy(original)
    declaration = tools["replace_atom_structure"]._get_declaration()
    wire = _function_declaration_to_tool_param(declaration)
    schema = wire["function"]["parameters"]
    assert schema["properties"]["target"]["enum"] == ["example_structure"]
    assert set(schema["required"]) == {"target", "structure", "expected_revision"}
    model = schema["properties"]["structure"]
    assert model["type"] == "object" and model["additionalProperties"] is False
    atoms = model["properties"]["atoms"]
    assert atoms["type"] == "array" and atoms["maxItems"] == 20000
    assert set(atoms["items"]["required"]) == {"id", "symbol", "x", "y", "z"}
    assert atoms["items"]["properties"]["id"]["type"] == "integer"
    assert atoms["items"]["properties"]["x"]["type"] == "number"
    assert "atoms" not in model["properties"]["layers"]["items"]["properties"]
    assert "$defs" not in schema and "$ref" not in str(schema)
    assert original == before
    declaration.parameters_json_schema["properties"]["target"]["enum"].append("forged")
    assert tools["replace_atom_structure"]._get_declaration().parameters_json_schema["properties"]["target"]["enum"] == ["example_structure"]


def test_adk_object_write_reaches_real_contract_and_still_checks_revision_and_authority(client):
    _, structure, edge, _, tools = atom_tools(client)
    invoke = lambda name, args: client.portal.call(lambda: tools[name].run_async(args=args, tool_context=None))
    inspected = invoke("inspect_atom_structure", {"target": structure["id"]})
    example = inspected["write_contract"]["minimal_structure_example"]
    assert example["atoms"][0]["symbol"] == "C"  # Generic schema help, no source-specific answer.
    document = {**example, "source_metadata": {"source": "synthetic contract test"}}
    arguments = {"target": structure["id"], "expected_revision": inspected["revision"], "structure": document}
    result = invoke("replace_atom_structure", arguments)
    assert "error" not in result
    current = read(client, structure["id"])
    assert current["value"]["atoms"][0]["symbol"] == "C"
    assert current["value"]["source_metadata"] == {"source": "synthetic contract test"}
    stale = invoke("replace_atom_structure", arguments)
    assert stale["ok"] is False and stale["error"]["code"] == "revision_conflict"
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    denied = invoke("replace_atom_structure", {**arguments, "expected_revision": current["revision"]})
    assert denied["error"]["code"] == "permission_denied"
    assert read(client, structure["id"]) == current


def test_legacy_definition_without_json_schema_retains_callable_behavior():
    provider = MutableCapabilityProvider()
    tool = build_scoped_adk_tools(provider, "agent-1", [provider.definition])[0]
    schema = _function_declaration_to_tool_param(tool._get_declaration())["function"]["parameters"]
    assert schema["properties"]["content"]["type"] == "string"
    assert asyncio.run(tool.run_async(args={"content": "notes"}, tool_context=None)) == {"content": "notes"}
    provider.allowed = False
    assert asyncio.run(tool.run_async(args={"content": "denied"}, tool_context=None))["error"]["code"] == "permission_denied"


def test_schema_expansion_preserves_recursive_definitions_and_pointer_escaping():
    recursive = {"$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
                 "$ref": "#/$defs/Node"}
    assert _inline_schema_refs(recursive) == recursive
    escaped = {"$defs": {"A/B~C": {"type": "object", "properties": {"value": {"type": "number"}}}},
               "$ref": "#/$defs/A~1B~0C"}
    assert _inline_schema_refs(escaped) == {"type": "object", "properties": {"value": {"type": "number"}}}
