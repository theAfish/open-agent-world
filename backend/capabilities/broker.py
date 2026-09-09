from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.capabilities.models import Capability, CapabilitySet
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins import PluginRegistry
from backend.resources.manager import ManagedResourceStore
from backend.resources.models import ResourceRecord, TextDocument, TextEdit
from backend.world.models import Card, CardType, EdgeDirection, Relationship
from backend.world.store import WorldStore


class CapabilityBroker:
    """Derives and checks permissions directly against the authoritative graph.

    There is deliberately no capability cache. Every privileged operation below
    asks the world store for the current edge, so an update or deletion takes
    effect immediately.
    """

    def __init__(
        self,
        world: WorldStore,
        resources: ManagedResourceStore,
        plugins: PluginRegistry,
    ) -> None:
        self.world = world
        self.resources = resources
        self.plugins = plugins

    def derive(self, agent_id: str) -> CapabilitySet:
        agent = self._require_agent(agent_id)
        capabilities: list[Capability] = []
        directed_edges = [(edge, edge.target) for edge in self.world.connections_from(agent_id)]
        directed_edges.extend(
            (edge, edge.source)
            for edge in self.world.connections_to(agent_id)
            if edge.direction == EdgeDirection.BIDIRECTIONAL
        )
        capability_ids: set[str] = set()
        visited: set[tuple[str, str]] = set()
        for edge, target_id in directed_edges:
            if (edge.relationship, target_id) in visited:
                continue
            visited.add((edge.relationship, target_id))
            target = self.world.get_card(target_id)
            relationship = self.plugins.relationship(edge.relationship)
            if relationship.generated:
                continue
            # Participation explicitly shares only attached meeting-note resources,
            # not arbitrary capabilities belonging to other participants.
            if edge.relationship == "participate" and self.plugins.has_trait(target.type, "core.conversation"):
                directed_edges.extend(
                    (child, child.target) for child in self.world.connections_from(target_id)
                    if child.relationship == "conversation_notes"
                )
            if not relationship.capabilities:
                directed_edges.extend((child, child.target) for child in self.world.connections_from(target_id))
            for grant in relationship.capabilities:
                operation = self.plugins.capability_definition(grant.kind)
                capability_id = f"{grant.kind}:{target.id}"
                if capability_id in capability_ids:
                    continue
                capability_ids.add(capability_id)
                capabilities.append(
                    Capability(
                        id=capability_id,
                        tool_name=operation.tool_name,
                        kind=grant.kind,
                        agent_id=agent.id,
                        target_id=target.id,
                        target_type=target.type,
                        target_name=target.name,
                        source_node_id=edge.source if edge.target == target_id else edge.target,
                        description=operation.description,
                        input_schema=dict(operation.input_schema),
                    )
                )
        if agent.parent_id and self.world.get_card(agent.parent_id).type == "legion":
            group = self.world.get_card(agent.parent_id)
            for operation in ("read", "patch"):
                if operation == "patch" and group.config.get("shared_state_access") != "read_write":
                    continue
                kind = f"legion.state.{operation}"
                definition = self.plugins.capability_definition(kind)
                capabilities.append(Capability(
                    id=f"{kind}:{group.id}", tool_name=definition.tool_name, kind=kind,
                    agent_id=agent.id, target_id=group.id, target_type=group.type, target_name=group.name,
                    description=definition.description, input_schema=dict(definition.input_schema),
                ))
        return CapabilitySet(agent_id=agent.id, capabilities=capabilities)

    def require_agent_communicate(self, agent_id: str, target_agent_id: str) -> None:
        self._require_agent(agent_id)
        self._require_agent(target_agent_id)
        self.capability_for_id(agent_id, f"agent.communicate:{target_agent_id}")

    def require_text_read(self, agent_id: str, resource_id: str) -> None:
        self.capability_for_id(agent_id, f"text.read:{resource_id}")

    def require_text_edit(self, agent_id: str, resource_id: str) -> None:
        self.capability_for_id(agent_id, f"text.edit:{resource_id}")

    def require_image_view(self, agent_id: str, resource_id: str) -> None:
        self.capability_for_id(agent_id, f"image.view:{resource_id}")

    def require_sandbox_execute(self, agent_id: str, sandbox_id: str) -> None:
        self.capability_for_id(agent_id, f"sandbox.execute:{sandbox_id}")

    def require_sandbox_resource(
        self, sandbox_id: str, resource_id: str, *, write: bool = False
    ) -> None:
        self._require_type(sandbox_id, CardType.SANDBOX)
        resource = self.world.get_card(resource_id)
        if resource.type not in {CardType.TEXT, CardType.IMAGE}:
            raise ResourceValidationError(f"card {resource_id!r} is not a resource")
        edge = self.world.find_edge(resource_id, sandbox_id)
        allowed = {Relationship.MOUNT_READ_ONLY, Relationship.MOUNT_READ_WRITE}
        if write:
            allowed = {Relationship.MOUNT_READ_WRITE}
        if edge is None or edge.relationship not in allowed:
            action = "write" if write else "read"
            raise PermissionDeniedError(
                f"sandbox {sandbox_id!r} has no {action} access to resource {resource_id!r}"
            )
        if write and resource.type == CardType.IMAGE:
            raise PermissionDeniedError("image resources are always read-only")

    def read_text(self, agent_id: str, resource_id: str) -> TextDocument:
        self.require_text_read(agent_id, resource_id)
        return self.resources.read_text(resource_id)

    def replace_text(
        self,
        agent_id: str,
        resource_id: str,
        content: str,
        *,
        expected_revision: int | None = None,
    ) -> TextDocument:
        self.require_text_edit(agent_id, resource_id)
        return self.resources.replace_text(
            resource_id,
            content,
            expected_revision=expected_revision,
            actor_id=agent_id,
        )

    def patch_text(
        self,
        agent_id: str,
        resource_id: str,
        edits: list[TextEdit],
        *,
        expected_revision: int | None = None,
    ) -> TextDocument:
        self.require_text_edit(agent_id, resource_id)
        return self.resources.patch_text(
            resource_id,
            edits,
            expected_revision=expected_revision,
            actor_id=agent_id,
        )

    def view_image(self, agent_id: str, resource_id: str) -> tuple[ResourceRecord, Path]:
        self.require_image_view(agent_id, resource_id)
        return self.resources.read_bytes(resource_id)

    def capability_for_id(self, agent_id: str, capability_id: str) -> Capability:
        # A capability id identifies scope for dispatch, but it is never treated
        # as an authorization token. Derivation re-reads the graph here.
        for capability in self.derive(agent_id).capabilities:
            if capability.id == capability_id:
                return capability
        raise PermissionDeniedError(
            f"capability {capability_id!r} is not currently available to agent {agent_id!r}"
        )

    def _require_agent(self, card_id: str) -> Card:
        card = self.world.get_card(card_id)
        if not self.plugins.has_trait(card.type, "core.agent"):
            raise ResourceValidationError(f"card {card_id!r} is not an Agent")
        return card

    def _require_type(self, card_id: str, expected: str) -> Card:
        card = self.world.get_card(card_id)
        if card.type != expected:
            raise ResourceValidationError(
                f"card {card_id!r} is {card.type!r}, expected {expected!r}"
            )
        return card
