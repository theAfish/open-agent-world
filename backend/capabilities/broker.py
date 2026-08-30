from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.capabilities.models import Capability, CapabilityKind, CapabilitySet
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.resources.manager import ManagedResourceStore
from backend.resources.models import ResourceRecord, TextDocument, TextEdit
from backend.world.models import Card, CardType, Relationship
from backend.world.store import WorldStore


def _tool_suffix(card: Card) -> str:
    readable = re.sub(r"[^a-z0-9]+", "_", card.name.lower()).strip("_")[:24]
    identifier = re.sub(r"[^a-zA-Z0-9]+", "", card.id)[:12].lower()
    return "_".join(part for part in (readable, identifier) if part) or "resource"


class CapabilityBroker:
    """Derives and checks permissions directly against the authoritative graph.

    There is deliberately no capability cache. Every privileged operation below
    asks the world store for the current edge, so an update or deletion takes
    effect immediately.
    """

    def __init__(self, world: WorldStore, resources: ManagedResourceStore) -> None:
        self.world = world
        self.resources = resources

    def derive(self, agent_id: str) -> CapabilitySet:
        agent = self._require_type(agent_id, CardType.AGENT)
        capabilities: list[Capability] = []
        for edge in self.world.list_edges_from(agent_id):
            target = self.world.get_card(edge.target)
            suffix = _tool_suffix(target)
            if target.type is CardType.TEXT and edge.relationship in {
                Relationship.READ,
                Relationship.READ_EDIT,
            }:
                capabilities.append(
                    Capability(
                        id=f"{CapabilityKind.TEXT_READ.value}:{target.id}",
                        tool_name=f"read_text_{suffix}",
                        kind=CapabilityKind.TEXT_READ,
                        agent_id=agent.id,
                        target_id=target.id,
                        target_type=target.type,
                        target_name=target.name,
                        description=f"Read the managed text resource {target.name!r}.",
                        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    )
                )
                if edge.relationship is Relationship.READ_EDIT:
                    capabilities.append(
                        Capability(
                            id=f"{CapabilityKind.TEXT_EDIT.value}:{target.id}",
                            tool_name=f"replace_text_{suffix}",
                            kind=CapabilityKind.TEXT_EDIT,
                            agent_id=agent.id,
                            target_id=target.id,
                            target_type=target.type,
                            target_name=target.name,
                            description=f"Replace the contents of {target.name!r}.",
                            input_schema={
                                "type": "object",
                                "properties": {"content": {"type": "string"}},
                                "required": ["content"],
                                "additionalProperties": False,
                            },
                        )
                    )
            elif target.type is CardType.IMAGE and edge.relationship is Relationship.VIEW:
                capabilities.append(
                    Capability(
                        id=f"{CapabilityKind.IMAGE_VIEW.value}:{target.id}",
                        tool_name=f"view_image_{suffix}",
                        kind=CapabilityKind.IMAGE_VIEW,
                        agent_id=agent.id,
                        target_id=target.id,
                        target_type=target.type,
                        target_name=target.name,
                        description=f"Inspect the managed image {target.name!r}.",
                        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    )
                )
            elif target.type is CardType.SANDBOX and edge.relationship is Relationship.EXECUTE:
                capabilities.append(
                    Capability(
                        id=f"{CapabilityKind.SANDBOX_EXECUTE.value}:{target.id}",
                        tool_name=f"execute_in_{suffix}",
                        kind=CapabilityKind.SANDBOX_EXECUTE,
                        agent_id=agent.id,
                        target_id=target.id,
                        target_type=target.type,
                        target_name=target.name,
                        description=f"Execute an argv command in sandbox {target.name!r}.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "argv": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                }
                            },
                            "required": ["argv"],
                            "additionalProperties": False,
                        },
                    )
                )
        return CapabilitySet(agent_id=agent.id, capabilities=capabilities)

    def require_text_read(self, agent_id: str, resource_id: str) -> None:
        self._require_type(agent_id, CardType.AGENT)
        self._require_type(resource_id, CardType.TEXT)
        edge = self.world.find_edge(agent_id, resource_id)
        if edge is None or edge.relationship not in {
            Relationship.READ,
            Relationship.READ_EDIT,
        }:
            raise PermissionDeniedError(
                f"agent {agent_id!r} has no read capability for text {resource_id!r}"
            )

    def require_text_edit(self, agent_id: str, resource_id: str) -> None:
        self._require_type(agent_id, CardType.AGENT)
        self._require_type(resource_id, CardType.TEXT)
        edge = self.world.find_edge(agent_id, resource_id)
        if edge is None or edge.relationship is not Relationship.READ_EDIT:
            raise PermissionDeniedError(
                f"agent {agent_id!r} has no edit capability for text {resource_id!r}"
            )

    def require_image_view(self, agent_id: str, resource_id: str) -> None:
        self._require_type(agent_id, CardType.AGENT)
        self._require_type(resource_id, CardType.IMAGE)
        edge = self.world.find_edge(agent_id, resource_id)
        if edge is None or edge.relationship is not Relationship.VIEW:
            raise PermissionDeniedError(
                f"agent {agent_id!r} has no view capability for image {resource_id!r}"
            )

    def require_sandbox_execute(self, agent_id: str, sandbox_id: str) -> None:
        self._require_type(agent_id, CardType.AGENT)
        self._require_type(sandbox_id, CardType.SANDBOX)
        edge = self.world.find_edge(agent_id, sandbox_id)
        if edge is None or edge.relationship is not Relationship.EXECUTE:
            raise PermissionDeniedError(
                f"agent {agent_id!r} cannot execute in sandbox {sandbox_id!r}"
            )

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
        if write and resource.type is CardType.IMAGE:
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

    def capability_for_tool(self, agent_id: str, tool_name: str) -> Capability:
        # Derivation is intentionally repeated immediately before invocation.
        for capability in self.derive(agent_id).capabilities:
            if capability.tool_name == tool_name:
                return capability
        raise PermissionDeniedError(
            f"tool {tool_name!r} is not currently available to agent {agent_id!r}"
        )

    def capability_for_id(self, agent_id: str, capability_id: str) -> Capability:
        # A capability id identifies scope for dispatch, but it is never treated
        # as an authorization token. Derivation re-reads the graph here.
        for capability in self.derive(agent_id).capabilities:
            if capability.id == capability_id:
                return capability
        raise PermissionDeniedError(
            f"capability {capability_id!r} is not currently available to agent {agent_id!r}"
        )

    def invoke_structured_tool(
        self, agent_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> TextDocument | tuple[ResourceRecord, Path] | tuple[str, list[str]]:
        capability = self.capability_for_tool(agent_id, tool_name)
        if capability.kind is CapabilityKind.TEXT_READ:
            if arguments:
                raise ResourceValidationError("text read tool takes no arguments")
            return self.read_text(agent_id, capability.target_id)
        if capability.kind is CapabilityKind.TEXT_EDIT:
            if set(arguments) != {"content"} or not isinstance(arguments["content"], str):
                raise ResourceValidationError("text edit tool requires one string content argument")
            return self.replace_text(agent_id, capability.target_id, arguments["content"])
        if capability.kind is CapabilityKind.IMAGE_VIEW:
            if arguments:
                raise ResourceValidationError("image view tool takes no arguments")
            return self.view_image(agent_id, capability.target_id)
        if capability.kind is CapabilityKind.SANDBOX_EXECUTE:
            argv = arguments.get("argv")
            if set(arguments) != {"argv"} or not isinstance(argv, list) or not argv or not all(
                isinstance(value, str) and value for value in argv
            ):
                raise ResourceValidationError("sandbox execute tool requires a non-empty argv array")
            self.require_sandbox_execute(agent_id, capability.target_id)
            # Execution itself belongs to SandboxBackend. This return value is
            # an explicit dispatch request for the runtime adapter.
            return capability.target_id, argv
        raise AssertionError(f"unhandled capability kind {capability.kind}")

    def _require_type(self, card_id: str, expected: CardType) -> Card:
        card = self.world.get_card(card_id)
        if card.type is not expected:
            raise ResourceValidationError(
                f"card {card_id!r} is {card.type.value!r}, expected {expected.value!r}"
            )
        return card
