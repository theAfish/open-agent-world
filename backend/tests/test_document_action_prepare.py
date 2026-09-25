"""External preparation must never hold the graph gate or bypass live checks."""
import asyncio
from dataclasses import replace

import pytest
from pydantic import BaseModel

from backend.errors import NotFoundError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document
from backend.tests.conftest import create_node
from backend.tests.test_tool_projection import connect
from open_agent_world.plugin_api import (
    CapabilityGrantDefinition, NodeDocumentAction, NodeDocumentDefinition,
    PluginDefinition, PluginDescriptor, RelationshipDefinition,
)


class PreparedDocument(BaseModel):
    content: str = "original"


def install_prepared_node(client, prepare):
    services = client.app.state.services
    actions = {"load": NodeDocumentAction(
        lambda value, arguments: {"content": arguments["content"]},
        capability_kind="example.prepared.write", prepare=prepare,
    )}

    async def dispatch(context, capability, arguments):
        return await context.node_document_action(capability, "load", arguments)

    def register(registration):
        original = services.plugins.node_type("compute-target")
        registration.register_node_type(replace(original, id="example.prepared", document=NodeDocumentDefinition(
            model=PreparedDocument, actions=actions,
        )))
        registration.register_capability_handler("example.prepared.write", dispatch)
        registration.register_relationship(RelationshipDefinition(
            id="example.prepared.write", label="Write", short_label="Write", description="Prepare a document",
            source_types=frozenset({"agent"}), target_types=frozenset({"example.prepared"}),
            capabilities=(CapabilityGrantDefinition("example.prepared.write", "prepare_document", "Prepare a document"),),
        ))

    services.plugins.install(PluginDefinition(
        PluginDescriptor(id="example.prepared", version="1", plugin_api_version="1.11"), register,
    ))
    return create_node(client, "example.prepared"), actions


def test_prepare_releases_gate_and_receives_detached_arguments(client):
    services = client.app.state.services
    observed = {}

    async def prepare(value, arguments):
        assert not services._node_mutation_lock.locked()
        # A different task can access the world while preparation is suspended.
        card = await asyncio.wait_for(asyncio.create_task(services.read_card(node["id"])), 1)
        assert card.id == node["id"]
        value["content"] = "only the preparation copy"
        arguments["nested"]["changed"] = True
        observed["before"] = read_document(services, node["id"])
        return {"content": "prepared"}

    node, _ = install_prepared_node(client, prepare)
    request = DocumentActionRequest(expected_revision=0, arguments={"nested": {"changed": False}})
    result = client.portal.call(lambda: invoke_document_action(services, node["id"], "load", request))
    assert observed["before"]["value"] == {"content": "original"}
    assert observed["before"]["revision"] == 0
    assert request.arguments == {"nested": {"changed": False}}
    assert result["value"] == {"content": "prepared"}
    assert result["revision"] == 1


def test_concurrent_document_edit_rejects_prepared_result(client):
    services = client.app.state.services

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def prepare(value, arguments):
            entered.set()
            await release.wait()
            return {"content": "stale download"}

        actions["load"] = replace(actions["load"], prepare=prepare)
        request = DocumentActionRequest(expected_revision=0)
        pending = asyncio.create_task(invoke_document_action(services, node["id"], "load", request))
        await asyncio.wait_for(entered.wait(), 1)
        newer = await invoke_document_action(services, node["id"], "replace", DocumentActionRequest(
            expected_revision=0, arguments={"content": "newer human edit"},
        ))
        # Neither caller mutation nor preparation can change the captured CAS.
        request.expected_revision = newer["revision"]
        release.set()
        with pytest.raises(RevisionConflictError):
            await pending
        assert read_document(services, node["id"]) == newer

    async def unused(value, arguments):
        return arguments

    node, actions = install_prepared_node(client, unused)
    client.portal.call(scenario)


@pytest.mark.parametrize("failure", ["validation", "unexpected", "cancel", "invalid_return"])
def test_failed_preparation_never_writes_document(client, failure):
    services = client.app.state.services
    handler_called = []

    async def prepare(value, arguments):
        value["content"] = "not stored"
        if failure == "validation":
            raise ValueError("Download was invalid")
        if failure == "unexpected":
            raise RuntimeError("Network failed")
        if failure == "cancel":
            raise asyncio.CancelledError()
        return []

    node, actions = install_prepared_node(client, prepare)
    actions["load"] = replace(actions["load"], handler=lambda value, arguments: handler_called.append(True))

    async def scenario():
        before = read_document(services, node["id"])
        error = {"validation": ResourceValidationError, "unexpected": RuntimeError,
                 "cancel": asyncio.CancelledError, "invalid_return": ResourceValidationError}[failure]
        with pytest.raises(error):
            await invoke_document_action(services, node["id"], "load", DocumentActionRequest(expected_revision=0))
        assert read_document(services, node["id"]) == before
        assert not handler_called

    client.portal.call(scenario)


@pytest.mark.parametrize("change", ["delete", "revoke", "handler", "not_editable"])
def test_live_changes_during_prepare_prevent_commit(client, monkeypatch, change):
    services = client.app.state.services

    async def unused(value, arguments):
        return arguments

    node, actions = install_prepared_node(client, unused)
    agent = create_node(client, "agent")
    edge = connect(client, agent, node, "example.prepared.write")
    capability = next(item for item in services.capabilities.derive(agent["id"]).capabilities
                      if item.target_id == node["id"])

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def prepare(value, arguments):
            entered.set()
            await release.wait()
            return {"content": "download"}

        actions["load"] = replace(actions["load"], prepare=prepare)
        pending = asyncio.create_task(invoke_document_action(services, node["id"], "load",
            DocumentActionRequest(expected_revision=0), capability=capability))
        await asyncio.wait_for(entered.wait(), 1)
        if change == "delete":
            await services.delete_card(node["id"])
            expected_error = NotFoundError
        elif change == "revoke":
            await services.delete_edge(edge["id"])
            expected_error = PermissionDeniedError
        elif change == "handler":
            actions["load"] = replace(actions["load"], handler=lambda value, arguments: value)
            expected_error = RevisionConflictError
        else:
            def busy(node_id, **kwargs):
                raise ResourceValidationError("Document is now executing")
            monkeypatch.setattr(services.node_execution, "assert_editable", busy)
            expected_error = ResourceValidationError
        release.set()
        with pytest.raises(expected_error):
            await pending
        if change == "delete":
            with pytest.raises(NotFoundError):
                services.world.get_card(node["id"])
        else:
            current = read_document(services, node["id"])
            assert current["revision"] == 0
            assert current["value"] == {"content": "original"}

    client.portal.call(scenario)


def test_prepare_is_not_called_before_revision_validation_or_inside_outer_gate(client):
    services = client.app.state.services
    calls = []

    async def prepare(value, arguments):
        calls.append(True)
        return arguments

    node, _ = install_prepared_node(client, prepare)

    async def scenario():
        for revision, error in [(None, ResourceValidationError), (1, RevisionConflictError)]:
            with pytest.raises(error):
                await invoke_document_action(services, node["id"], "load", DocumentActionRequest(expected_revision=revision))
        async with services._node_mutation():
            with pytest.raises(ResourceValidationError, match="outside a node mutation"):
                await invoke_document_action(services, node["id"], "load", DocumentActionRequest(expected_revision=0))
        assert not calls

    client.portal.call(scenario)


def test_read_only_preparation_is_not_allowed():
    async def prepare(value, arguments):
        return arguments

    with pytest.raises(ValueError, match="Read-only"):
        NodeDocumentAction(lambda value, arguments: value, read_only=True, prepare=prepare)
