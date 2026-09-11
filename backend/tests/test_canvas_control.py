from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from backend.canvas_control import CanvasScope
from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.plugins.config_policy import agent_config_policy
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin


def control(client, **overrides):
    services = client.app.state.services
    grant = CanvasScope(bounds={"x": -100, "y": -100, "width": 4000, "height": 4000},
                        operations={"query", "create", "delete", "move", "resize", "configure", "rename",
                                    "reparent", "detach", "connect", "disconnect", "update_edge"},
                        node_types={"agent", "text", "image", "conversation", "sandbox", "legion", "test.policy"},
                        relationships={"read", "read_edit", "communicate"})
    grant = grant.model_copy(update=overrides)
    grants = {"automation": grant}
    return services.canvas_control("automation", grants.get), grants


def call(client, method, *args, **kwargs):
    async def invoke():
        return await method(*args, **kwargs)
    return client.portal.call(invoke)


def versions(client, facade):
    return call(client, facade.query)["versions"]


def test_principal_connection_authority_is_explicit_and_never_self_mutation(client):
    services = client.app.state.services
    principal = create_node(client, "agent", position={"x": -1000, "y": -1000})
    # Private equipment outside the spatial area must not be mistaken for an
    # ordinary endpoint effect, or become mutable through the principal grant.
    equipment = create_node(client, "text", position={"x": -1000, "y": -1000},
                            equipment={"owner_id": principal["id"], "relationship": "read"})
    chat = create_node(client, "conversation", position={"x": 100, "y": 100})
    _, grants = control(client, relationships=frozenset({"read", "participate"}),
                        principal_relationships=frozenset({"participate"}))
    grants[principal["id"]] = grants.pop("automation")
    facade = services.canvas_control(principal["id"], grants.get)
    view = call(client, facade.query)
    assert view["principal"]["id"] == principal["id"]
    assert principal["id"] not in {node["id"] for node in view["nodes"]}
    option = call(client, facade.connection_options, principal["id"], chat["id"])[0]
    assert option["permitted"] and option["relationship"] == "participate"
    edge = call(client, facade.connect_cards, principal["id"], chat["id"], "participate", view["versions"])
    assert not next(item for item in call(client, facade.query)["edges"] if item["id"] == edge["id"])["external"]
    assert call(client, facade.connection_options, principal["id"], chat["id"])[0]["existing_edge_id"] == edge["id"]
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, principal["id"], {"x": 200, "y": 200}, versions(client, facade))
    with pytest.raises(PermissionDeniedError):
        call(client, facade.delete_cards, [equipment["id"]], versions(client, facade))
    note = create_node(client, "text", position={"x": 100, "y": 400})
    assert not call(client, facade.connection_options, principal["id"], note["id"])[0]["permitted"]
    with pytest.raises(PermissionDeniedError):
        call(client, facade.connect_cards, principal["id"], note["id"], "read", versions(client, facade))
    observed = versions(client, facade)
    client.patch(f"/api/nodes/{principal['id']}", json={"name": "Changed controller"})
    with pytest.raises(RevisionConflictError):
        call(client, facade.disconnect_cards, edge["id"], observed)
    call(client, facade.disconnect_cards, edge["id"], versions(client, facade))
    grants[principal["id"]] = grants[principal["id"]].model_copy(update={"principal_relationships": frozenset()})
    with pytest.raises(PermissionDeniedError):
        call(client, facade.connect_cards, principal["id"], chat["id"], "participate", versions(client, facade))


def test_explicit_operations_use_services_and_preserve_resource_lifecycle(client):
    facade, _ = control(client)
    node = call(client, facade.create_card, {"type": "text", "position": {"x": 100, "y": 100}}, {})
    assert client.get(f"/api/resources/{node['id']}/text").status_code == 200
    moved = call(client, facade.move_card, node["id"], {"x": 300, "y": 400}, versions(client, facade))
    assert moved[0]["position"] == {"x": 300, "y": 400}
    assert moved[0]["revision"] == node["revision"] + 1
    call(client, facade.resize_card, node["id"], {"width": 350, "height": 250}, versions(client, facade))
    call(client, facade.delete_cards, [node["id"]], versions(client, facade))
    assert client.get(f"/api/nodes/{node['id']}").status_code == 404
    assert client.get(f"/api/resources/{node['id']}/text").status_code == 404


def test_query_projects_config_and_marks_external_and_equipment_edges(client):
    facade, _ = control(client)
    agent = create_node(client, "agent", config={"api_key": "private-value", "system_instruction": "Visible"})
    outside = create_node(client, "text", position={"x": 10000, "y": 0})
    equipped = create_node(client, "text", equipment={"owner_id": agent["id"], "relationship": "read"})
    client.post("/api/edges", json={"source": agent["id"], "target": outside["id"], "relationship": "read"})
    snapshot = call(client, facade.query)
    assert outside["id"] not in {node["id"] for node in snapshot["nodes"]}
    assert "private-value" not in str(snapshot)
    assert any(edge["external"] for edge in snapshot["edges"])
    assert any(edge["id"] == f"equipment:{equipped['id']}" and edge["derived"] for edge in snapshot["edges"])
    assert next(node for node in snapshot["nodes"] if node["id"] == agent["id"])["config"]["system_instruction"] == "Visible"


def test_authority_is_live_and_input_cannot_supply_scope_or_internal_fields(client):
    facade, grants = control(client)
    node = create_node(client, "conversation")
    observed = versions(client, facade)
    for patch in ({"status": "running"}, {"scope": {}}, {"equipment": None}, {"id": "other"}):
        with pytest.raises(ResourceValidationError):
            call(client, facade.update_card, node["id"], patch, observed)
    for field in ("status", "id", "equipment", "content"):
        with pytest.raises(ResourceValidationError):
            call(client, facade.create_card, {"type": "text", "position": {}, field: "injected"}, observed)
    del grants["automation"]
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, node["id"], {"x": 1, "y": 1}, observed)
    with pytest.raises(PermissionDeniedError):
        call(client, facade.query)


def test_rectangles_types_and_explicit_ids_intersect(client):
    node = create_node(client, "text", position={"x": 3800, "y": 0})  # Extends outside bounds.
    facade, _ = control(client)
    assert call(client, facade.query)["nodes"] == []
    with pytest.raises(PermissionDeniedError):
        call(client, facade.create_card, {"type": "text", "position": {"x": 3800, "y": 0}}, {})
    inside = create_node(client, "text")
    limited, _ = control(client, node_ids=frozenset({node["id"]}))
    with pytest.raises(PermissionDeniedError):
        call(client, limited.move_card, inside["id"], {}, versions(client, facade))
    observed = versions(client, facade)
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, inside["id"], {"x": 3900, "y": 0}, observed)
    with pytest.raises(ResourceValidationError):
        call(client, facade.move_card, inside["id"], {"x": float("nan"), "y": 0}, observed)


def test_revision_conflicts_and_restored_identity_are_rejected(client):
    facade, _ = control(client)
    node = create_node(client, "conversation")
    stale = versions(client, facade)
    call(client, facade.set_card_config, node["id"], {"description": "First actor"}, stale)
    with pytest.raises(RevisionConflictError):
        call(client, facade.set_card_config, node["id"], {"description": "Stale actor"}, stale)
    with pytest.raises(RevisionConflictError):
        call(client, facade.delete_cards, [node["id"]], stale)
    client.delete(f"/api/nodes/{node['id']}")
    create_node(client, "conversation", id=node["id"])
    with pytest.raises(RevisionConflictError):
        call(client, facade.move_card, node["id"], {"x": 1, "y": 2}, stale)


def test_edges_require_scope_relationship_permission_and_read_dependencies(client):
    facade, _ = control(client)
    agent, text = create_node(client, "agent"), create_node(client, "text")
    first = versions(client, facade)
    edge = call(client, facade.connect_cards, agent["id"], text["id"], "read", first)
    with pytest.raises(RevisionConflictError):
        call(client, facade.delete_cards, [text["id"]], first)  # New incident connection.
    observed = versions(client, facade)
    changed = call(client, facade.update_edge, edge["id"], {"relationship": "read_edit"}, observed)
    assert changed["revision"] == edge["revision"] + 1
    with pytest.raises(RevisionConflictError):
        call(client, facade.disconnect_cards, edge["id"], observed)
    restricted, _ = control(client, relationships=frozenset({"read"}))
    with pytest.raises(PermissionDeniedError):
        call(client, restricted.disconnect_cards, edge["id"], versions(client, restricted))
    call(client, facade.disconnect_cards, edge["id"], versions(client, facade))
    outside = create_node(client, "text", position={"x": 9000, "y": 0})
    client.post("/api/edges", json={"source": agent["id"], "target": outside["id"], "relationship": "read"})
    with pytest.raises(PermissionDeniedError):
        call(client, facade.delete_cards, [agent["id"]], versions(client, facade))
    assert client.get(f"/api/nodes/{agent['id']}").status_code == 200


def test_container_and_equipment_consequences_cannot_escape_scope(client):
    facade, _ = control(client)
    group = client.post("/api/nodes/restore", json={"id": "group", "type": "legion"}).json()
    member = create_node(client, "agent", parent_id=group["id"], position={"x": 300, "y": 300})
    observed = versions(client, facade)
    moved = call(client, facade.move_card, group["id"], {"x": 100, "y": 100}, observed)
    assert {n["id"] for n in moved} == {group["id"], member["id"]}
    assert next(n for n in moved if n["id"] == member["id"])["position"] == {"x": 400, "y": 400}
    current = versions(client, facade)
    create_node(client, "text", parent_id=group["id"], position={"x": 600, "y": 500})
    with pytest.raises(RevisionConflictError):
        call(client, facade.move_card, group["id"], {"x": 200, "y": 200}, current)
    create_node(client, "text", equipment={"owner_id": member["id"], "relationship": "read"}, position={"x": 9000, "y": 0})
    with pytest.raises(PermissionDeniedError):
        call(client, facade.delete_cards, [member["id"]], versions(client, facade))
    limited, _ = control(client, node_ids=frozenset({member["id"]}))
    with pytest.raises(PermissionDeniedError):
        call(client, limited.detach_card, member["id"], versions(client, limited))


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = Field(default="hello", json_schema_extra={"agentReadable": True, "agentWritable": True})
    readonly: str = Field(default="read", json_schema_extra={"agentReadable": True})
    key: str = Field(default="test-secret", json_schema_extra={"secret": True, "agentReadable": True, "agentWritable": True})
    internal: str = Field(default="fixed", json_schema_extra={"immutable": True, "agentWritable": True})
    privileged: str = Field(default="host", json_schema_extra={"privileged": True, "agentWritable": True})
    nested: dict = Field(default_factory=lambda: {"key": "nested-secret"}, json_schema_extra={"agentReadable": True, "agentWritable": True})
    password: SecretStr | None = Field(default=None, json_schema_extra={"agentReadable": True, "agentWritable": True})

    @model_validator(mode="after")
    def indirect(self):
        if self.title == "change-internal":
            self.internal = "changed"
        return self


def test_plugin_schema_policy_protects_reads_writes_creation_and_validator_effects(client):
    services = client.app.state.services
    template = services.plugins.node_type("conversation")
    install_test_plugin(services.plugins, "test.policy", lambda registration: registration.register_node_type(
        replace(template, id="test.policy", config_model=PolicyConfig, lifecycle=None, template_handler=None)))
    node = create_node(client, "test.policy", config={"unknown": "extra-secret"})
    facade, _ = control(client)
    view = call(client, facade.query)
    assert next(n for n in view["nodes"] if n["id"] == node["id"])["config"] == {"title": "hello", "readonly": "read"}
    for key in ("readonly", "key", "internal", "privileged", "unknown", "nested", "password"):
        with pytest.raises(PermissionDeniedError):
            call(client, facade.set_card_config, node["id"], {key: "submitted-secret"}, view["versions"])
        with pytest.raises(PermissionDeniedError):
            call(client, facade.create_card, {"type": "test.policy", "position": {}, "config": {key: "submitted-secret"}}, {})
    with pytest.raises(PermissionDeniedError):
        call(client, facade.set_card_config, node["id"], {"title": "change-internal"}, view["versions"])
    with pytest.raises(PermissionDeniedError):
        call(client, facade.create_card, {"type": "test.policy", "position": {}, "config": {"title": "change-internal"}}, {})
    changed = call(client, facade.set_card_config, node["id"], {"title": "updated"}, view["versions"])
    assert changed[0]["config"]["title"] == "updated"
    assert agent_config_policy(PolicyConfig)["key"]["agentWritable"] is False


def test_desktop_revision_checks_are_optional_but_enforced_when_supplied(client):
    agent, text = create_node(client, "agent"), create_node(client, "text")
    url = f"/api/nodes/{text['id']}"
    assert client.patch(url, json={"name": "new", "expected_revision": 1}).status_code == 200
    assert client.patch(url, json={"name": "stale", "expected_revision": 1}).status_code == 409
    assert client.delete(url, params={"expected_revision": 1}).status_code == 409
    assert client.patch(url, json={"name": "legacy"}).status_code == 200
    response = client.post("/api/nodes/batch-update", json={"updates": [
        {"node_id": agent["id"], "patch": {"name": "not committed", "expected_revision": agent["revision"]}},
        {"node_id": text["id"], "patch": {"name": "stale", "expected_revision": 1}}]})
    assert response.status_code == 409
    assert client.get(f"/api/nodes/{agent['id']}").json()["name"] == agent["name"]
    edge = client.post("/api/edges", json={"source": agent["id"], "target": text["id"], "relationship": "read"}).json()
    url = f"/api/edges/{edge['id']}"
    assert client.patch(url, json={"relationship": "read_edit", "expected_revision": 1}).status_code == 200
    assert client.patch(url, json={"relationship": "read", "expected_revision": 1}).status_code == 409
    assert client.delete(url, params={"expected_revision": 1}).status_code == 409
    assert client.delete(url, params={"expected_revision": 2}).status_code == 200


def test_host_authorizer_can_reuse_live_capabilities_and_revoke_cached_facade(client):
    services = client.app.state.services
    actor = create_node(client, "agent")
    gate = create_node(client, "text")
    edge = client.post("/api/edges", json={"source": actor["id"], "target": gate["id"], "relationship": "read"}).json()
    _, grants = control(client)
    def authorize(actor_id):
        # Host-selected gate and mapping. The caller cannot choose either.
        services.capabilities.capability_for_id(actor_id, f"text.read:{gate['id']}")
        return grants["automation"]
    facade = services.canvas_control(actor["id"], authorize)
    observed = versions(client, facade)
    client.delete(f"/api/edges/{edge['id']}")
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, gate["id"], {"x": 200, "y": 200}, observed)


def test_delete_rechecks_revision_after_lifecycle_work_and_compensates(client):
    from backend.plugins import NodeLifecycleHandler, NodeLifecycleTransaction
    from backend.world.models import CardPatch
    services = client.app.state.services
    rolled_back = []

    class Behavior(NodeLifecycleHandler):
        async def prepare_delete(self, context, node):
            class Transaction(NodeLifecycleTransaction):
                async def commit(self):
                    # Operational status can change without taking the editor barrier.
                    services.world.update_card(node.id, CardPatch(name="Newer state"))

                async def rollback(self, error):
                    rolled_back.append(type(error))
            return Transaction()

    template = services.plugins.node_type("conversation")
    install_test_plugin(services.plugins, "test.policy", lambda registration: registration.register_node_type(
        replace(template, id="test.policy", lifecycle=Behavior(), template_handler=None)))
    node = create_node(client, "test.policy")
    facade, _ = control(client)
    with pytest.raises(RevisionConflictError):
        call(client, facade.delete_cards, [node["id"]], versions(client, facade))
    assert rolled_back == [RevisionConflictError]
    assert client.get(f"/api/nodes/{node['id']}").json()["name"] == "Newer state"
    assert not services._has_pending_node_deletion(node["id"])


def test_authorization_runs_after_waiting_for_mutation_lock(client):
    import asyncio
    facade, grants = control(client)
    node = create_node(client, "text")
    observed = versions(client, facade)
    async def check():
        async with client.app.state.services._node_mutation():
            queued = asyncio.create_task(facade.move_card(node["id"], {"x": 200, "y": 200}, observed))
            await asyncio.sleep(0)
            grants.clear()
        with pytest.raises(PermissionDeniedError):
            await queued
    client.portal.call(check)


def test_indirect_resource_grants_cannot_cross_scope(client):
    from backend.plugins import RelationshipDefinition
    services = client.app.state.services
    install_test_plugin(services.plugins, "test.forwarding", lambda registration: registration.register_relationship(
        RelationshipDefinition(id="test.forward", label="Forward", short_label="Forward", description="Indirect resources",
                               source_types=frozenset({"agent"}), target_types=frozenset({"conversation"})) ))
    actor = create_node(client, "agent")
    bridge = create_node(client, "conversation")
    outside = create_node(client, "text", position={"x": 9000, "y": 0})
    client.post("/api/edges", json={"source": bridge["id"], "target": outside["id"], "relationship": "conversation_notes"})
    facade, _ = control(client, relationships=frozenset({"test.forward", "conversation_notes"}))
    with pytest.raises(PermissionDeniedError):
        call(client, facade.connect_cards, actor["id"], bridge["id"], "test.forward", versions(client, facade))


def test_connection_changes_check_upstream_actors_and_observed_grants(client):
    actor = create_node(client, "agent", position={"x": 9000, "y": 0})
    bridge = create_node(client, "conversation")
    text = create_node(client, "text")
    participation = client.post("/api/edges", json={"source": actor["id"], "target": bridge["id"], "relationship": "participate"}).json()
    facade, _ = control(client, relationships=frozenset({"participate", "conversation_notes"}))
    with pytest.raises(PermissionDeniedError):
        call(client, facade.connect_cards, bridge["id"], text["id"], "conversation_notes", versions(client, facade))
    client.patch(f"/api/nodes/{actor['id']}", json={"position": {"x": 10, "y": 10}})
    observed = versions(client, facade)
    del observed["edges"][participation["id"]]
    with pytest.raises(RevisionConflictError):
        call(client, facade.connect_cards, bridge["id"], text["id"], "conversation_notes", observed)
    call(client, facade.connect_cards, bridge["id"], text["id"], "conversation_notes", versions(client, facade))
    assert any(c.target_id == text["id"] for c in client.app.state.services.capabilities.derive(actor["id"]).capabilities)


def test_membership_changes_check_shared_container_connections(client):
    from backend.plugins import RelationshipDefinition
    services = client.app.state.services
    install_test_plugin(services.plugins, "test.membership", lambda registration: registration.register_relationship(
        RelationshipDefinition(id="test.members", label="Members", short_label="Members", description="Shared members",
                               source_types=frozenset({"agent"}), target_types=frozenset({"legion"})) ))
    outside = create_node(client, "agent", position={"x": 9000, "y": 0})
    group = client.post("/api/nodes/restore", json={"id": "shared-group", "type": "legion"}).json()
    member = create_node(client, "agent", parent_id=group["id"])
    client.post("/api/edges", json={"source": outside["id"], "target": group["id"], "relationship": "test.members"})
    facade, _ = control(client, relationships=frozenset({"test.members"}))
    for method, args in ((facade.detach_card, (member["id"],)), (facade.delete_cards, ([member["id"]],)),
                         (facade.create_card, ({"type": "agent", "position": {}, "parent_id": group["id"]},))):
        with pytest.raises(PermissionDeniedError):
            call(client, method, *args, versions(client, facade))
    assert client.get(f"/api/nodes/{member['id']}").json()["parent_id"] == group["id"]


def test_denied_writes_publish_nothing_and_success_uses_existing_world_events(client):
    services = client.app.state.services
    facade, _ = control(client)
    node = create_node(client, "conversation")
    observed = versions(client, facade)
    async def check():
        async with services.events.subscribe() as queue:
            with pytest.raises(PermissionDeniedError):
                await facade.set_card_config(node["id"], {"api_key": "do-not-echo"}, observed)
            assert queue.empty()
            await facade.set_card_config(node["id"], {"description": "Allowed"}, observed)
            event = queue.get_nowait()
            assert event.type == "card_updated"
            assert event.payload["node"]["config"]["description"] == "Allowed"
    client.portal.call(check)
