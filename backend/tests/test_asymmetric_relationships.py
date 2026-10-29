"""One stored edge grants independently validated permissions per direction."""
from dataclasses import replace
import pytest
from backend.errors import PermissionDeniedError
from backend.plugins import create_builtin_registry
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_canvas_control import call, control, versions
from open_agent_world.plugin_api import CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition


def relationships(registration):
    async def handle(context, capability, arguments):
        return {"kind": capability.kind, "target": capability.target_id}
    for name in ("forward", "reverse"):
        kind = f"test.asym.{name}"
        registration.register_capability(CapabilityDefinition(kind, f"asym_{name}", name), handle)
    registration.register_relationship(RelationshipDefinition(
        id="test.asym.reverse", label="Reverse", short_label="reverse", description="Reverse permission",
        source_types=frozenset({"agent"}), target_types=frozenset({"agent"}),
        capabilities=(CapabilityGrantDefinition("test.asym.reverse"),)))
    registration.register_relationship(RelationshipDefinition(
        id="test.asym.forward", label="Forward", short_label="forward", description="Forward permission",
        source_types=frozenset({"agent"}), target_types=frozenset({"agent"}),
        directions=frozenset({"forward", "bidirectional"}), reverse_relationship="test.asym.reverse",
        capabilities=(CapabilityGrantDefinition("test.asym.forward"),)))


def pair(client):
    install_test_plugin(client.app.state.services.plugins, "test.asym", relationships)
    return create_node(client, "agent"), create_node(client, "agent")


def kinds(client, node):
    response = client.get(f"/api/agents/{node['id']}/capabilities")
    assert response.status_code == 200, response.text
    return {item["kind"] for item in response.json()["capabilities"]}


def test_one_edge_grants_different_operations_and_updates_revoke_only_the_reverse(client):
    left, right = pair(client)
    response = client.post("/api/edges", json=dict(source=left["id"], target=right["id"],
                           relationship="test.asym.forward", direction="bidirectional"))
    assert response.status_code == 201, response.text
    edge = response.json()
    assert len(client.app.state.services.world.list_incident_edges([left["id"], right["id"]])) == 1
    assert kinds(client, left) == {"test.asym.forward"}
    assert kinds(client, right) == {"test.asym.reverse"}
    broker = client.app.state.services.capabilities
    broker.require_direct_grant(right["id"], left["id"], "test.asym.reverse")
    with pytest.raises(PermissionDeniedError):
        broker.require_direct_grant(right["id"], left["id"], "test.asym.forward")
    catalog = client.get("/api/catalog").json()["relationships"]
    assert next(r for r in catalog if r["id"] == "test.asym.forward")["reverse_relationship"] == "test.asym.reverse"
    assert client.patch(f"/api/edges/{edge['id']}", json={"direction": "forward"}).status_code == 200
    assert kinds(client, left) == {"test.asym.forward"} and kinds(client, right) == set()
    assert client.patch(f"/api/edges/{edge['id']}", json={"direction": "bidirectional"}).status_code == 200
    assert kinds(client, right) == {"test.asym.reverse"}
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    assert kinds(client, left) == kinds(client, right) == set()


def test_reverse_endpoint_validation_applies_on_create_and_patch_without_breaking_one_way(client):
    def register(registration):
        registration.register_relationship(RelationshipDefinition(
            id="test.invalid.forward", label="Work", short_label="work", description="Fixture",
            source_types=frozenset({"agent"}), target_types=frozenset({"text"}),
            directions=frozenset({"forward", "bidirectional"}), reverse_relationship="core.execution-source"))
    install_test_plugin(client.app.state.services.plugins, "test.invalid", register)
    agent, text = create_node(client, "agent"), create_node(client, "text")
    request = dict(source=text["id"], target=agent["id"], relationship="test.invalid.forward")
    assert client.post("/api/edges", json={**request, "direction": "bidirectional"}).status_code == 422
    response = client.post("/api/edges", json=request)
    assert response.status_code == 201
    assert response.json()["source"] == agent["id"]
    edge_id = response.json()["id"]
    assert client.patch(f"/api/edges/{edge_id}", json={"direction": "bidirectional"}).status_code == 422
    assert client.app.state.services.world.get_edge(edge_id).direction == "forward"


def test_disabled_reverse_provider_revokes_reverse_access_without_copying_forward_grants(client):
    registry = client.app.state.services.plugins
    def reverse(registration):
        registration.register_relationship(RelationshipDefinition(
            id="test.external.reverse", label="External reverse", short_label="rev", description="Fixture",
            source_types=frozenset({"agent"}), target_types=frozenset({"agent"}),
            capabilities=(CapabilityGrantDefinition("agent.communicate"),)))
    install_test_plugin(registry, "test.external", reverse)
    def forward(registration):
        relationships(registration)
        registration.relationships['test.asym.forward'] = replace(registration.relationships['test.asym.forward'],
                                                                reverse_relationship='test.external.reverse')
    install_test_plugin(registry, "test.asym", forward)
    left, right = create_node(client, "agent"), create_node(client, "agent")
    response = client.post('/api/edges', json=dict(source=left['id'], target=right['id'],
                           relationship='test.asym.forward', direction='bidirectional'))
    assert response.status_code == 201, response.text
    assert kinds(client, right) == {'agent.communicate'}
    registry.set_enabled('test.external', False)
    assert kinds(client, left) == {'test.asym.forward'} and kinds(client, right) == set()
    registry.set_enabled('test.external', True)
    assert kinds(client, right) == {'agent.communicate'}


@pytest.mark.parametrize("case", ["unknown", "one-way", "nested", "generated"])
def test_invalid_reverse_declarations_are_rejected_atomically(case):
    registry = create_builtin_registry()
    reverse = RelationshipDefinition(id="test.invalid.reverse", label="Reverse", short_label="rev", description="Fixture")
    forward = RelationshipDefinition(id="test.invalid.forward", label="Forward", short_label="fwd", description="Fixture",
                                     directions=frozenset({"forward", "bidirectional"}), reverse_relationship=reverse.id)
    if case == "unknown":
        forward = replace(forward, reverse_relationship="test.invalid.missing")
    elif case == "one-way":
        forward = replace(forward, directions=frozenset({"forward"}))
    elif case == "nested":
        reverse = replace(reverse, reverse_relationship=forward.id, directions=forward.directions)
    else:
        reverse = replace(reverse, generated=True)
    def register(registration):
        registration.register_relationship(forward)
        registration.register_relationship(reverse)
    with pytest.raises(ValueError, match="reverse_relationship"):
        install_test_plugin(registry, "test.invalid", register)
    assert not any(r.id.startswith("test.invalid") for r in registry.catalog().relationships)


def test_canvas_controller_must_be_authorized_for_both_directional_relationships(client):
    left, right = pair(client)
    facade, grants = control(client, relationships=frozenset({"test.asym.forward"}))
    with pytest.raises(PermissionDeniedError):
        call(client, facade.connect_cards, left["id"], right["id"], "test.asym.forward", versions(client, facade), "bidirectional")
    assert kinds(client, left) == kinds(client, right) == set()
    grants["automation"] = grants["automation"].model_copy(update={"relationships": frozenset({"test.asym.forward", "test.asym.reverse"})})
    edge = call(client, facade.connect_cards, left["id"], right["id"], "test.asym.forward", versions(client, facade), "bidirectional")
    assert kinds(client, left) == {"test.asym.forward"} and kinds(client, right) == {"test.asym.reverse"}
    call(client, facade.disconnect_cards, edge["id"], versions(client, facade))
    assert kinds(client, left) == kinds(client, right) == set()


def test_canvas_reviewer_receives_both_contracts_with_their_own_endpoint_order(client):
    left, right = pair(client)
    _, grants = control(client, relationships=frozenset({"test.asym.forward", "test.asym.reverse"}))
    effects = []
    facade = client.app.state.services.canvas_control("automation", grants.get, review=effects.append)
    call(client, facade.connect_cards, left["id"], right["id"], "test.asym.forward", versions(client, facade), "bidirectional")
    assert [(e.source, e.target, e.relationship) for e in effects[-1]['added_edges']] == [
        (left['id'], right['id'], 'test.asym.forward'), (right['id'], left['id'], 'test.asym.reverse')]
