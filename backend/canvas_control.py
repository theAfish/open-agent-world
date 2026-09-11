"""Scoped automation over the existing world services, without editor state.

Only trusted host code constructs this facade and supplies its live authorizer.
Neither authority nor an unrestricted ApplicationServices object is a tool input.
No grants, tools, or Minister behavior are installed by this module.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.errors import GraphValidationError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.plugins.config_policy import agent_config_policy, readable_config, validate_agent_config
from backend.world.models import Card, CardBatchPatch, CardCreate, CardPatch, Edge, EdgeCreate, EdgePatch, Point, Size

if TYPE_CHECKING:
    from backend.services import ApplicationServices

Operation = Literal["query", "create", "delete", "move", "resize", "configure", "rename",
                    "reparent", "detach", "connect", "disconnect", "update_edge"]


class CanvasBounds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    def contains(self, card: Card) -> bool:
        # Saved logical rectangle, independent of zoom, previews and displacement.
        return (self.x <= card.position.x and self.y <= card.position.y
                and card.position.x + card.size.width <= self.x + self.width
                and card.position.y + card.size.height <= self.y + self.height)


class CanvasScope(BaseModel):
    """A host grant. All limits intersect; nothing is granted by default.

    A fixed node_ids set disallows creation of new identities. A host can instead
    grant creation by type within bounds. Revocation replaces/removes this grant
    in the host resolver; no scope is cached in the facade.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    bounds: CanvasBounds
    operations: frozenset[Operation] = frozenset()
    node_types: frozenset[str] = frozenset()
    relationships: frozenset[str] = frozenset()
    node_ids: frozenset[str] | None = None


class CanvasVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision: int = Field(ge=1, strict=True)
    # Undo can restore the same ID with revision 1. Reject that stale incarnation.
    created_at: datetime


class CanvasVersions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: dict[str, CanvasVersion] = Field(default_factory=dict)
    edges: dict[str, CanvasVersion] = Field(default_factory=dict)


class CanvasCardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    position: Point
    name: str | None = None
    size: Size | None = None
    parent_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CanvasCardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    position: Point | None = None
    size: Size | None = None
    parent_id: str | None = None
    config: dict[str, Any] | None = None


class CanvasEdgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationship: str | None = None
    direction: Literal["forward", "bidirectional"] | None = None


def _validated(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        # Config input and plugin validation exceptions may contain secrets.
        raise ResourceValidationError("Invalid canvas request; check the operation schema") from None


class CanvasControl:
    def __init__(self, services: ApplicationServices, actor_id: str,
                 authorize: Callable[[str], CanvasScope | None]):
        self._services = services
        self._actor_id = actor_id
        self._authorize = authorize

    def _scope(self, *operations: Operation) -> CanvasScope:
        scope = self._authorize(self._actor_id)
        if not isinstance(scope, CanvasScope) or not set(operations) <= scope.operations:
            raise PermissionDeniedError("Canvas operation is not authorized")
        return scope

    def _allows(self, scope, card):
        return (card.type in scope.node_types and scope.bounds.contains(card)
                and (scope.node_ids is None or card.id in scope.node_ids))

    def _card(self, scope, card):
        if not self._allows(scope, card):
            raise PermissionDeniedError("Canvas change reaches an object outside the control scope")

    def _check(self, scope, versions, cards=(), edges=()):
        for items, expected in ((cards, versions.cards), (edges, versions.edges)):
            for item in items:
                if isinstance(item, Card):
                    self._card(scope, item)
                version = expected.get(item.id)
                if version is None or version.revision != item.revision or version.created_at != item.created_at:
                    raise RevisionConflictError("Affected canvas objects changed or were not read; query again")

    def _related_cards(self, cards):
        world = self._services.world
        return list({c.id: c for card in cards for c in [card, *world.ancestors(card)]}.values())

    def _membership_effects(self, scope, versions, parent_id, operation):
        if parent_id is None:
            return
        world = self._services.world
        parents = self._related_cards([world.get_card(parent_id)])
        edges = world.list_incident_edges([card.id for card in parents])
        edges += [edge for card in parents if (edge := world.equipment_edge(card)) is not None]
        if edges:
            self._scope(operation)
            cards, connections = self._connection_effects(scope, edges)
            self._check(scope, versions, cards, connections)

    def _connection_effects(self, scope, edges):
        """Include implicit forwarding and selectable members of resource containers."""
        services = self._services
        cards, connections, visited = {}, {}, set()
        pending = [(edge, edge.target) for edge in edges]
        pending += [(edge, edge.source) for edge in edges if edge.direction == "bidirectional"]
        while pending:
            edge, target_id = pending.pop()
            if (edge.id, target_id) in visited:
                continue
            visited.add((edge.id, target_id))
            definition = services.plugins.relationship(edge.relationship)
            if definition.generated or edge.relationship not in scope.relationships:
                raise PermissionDeniedError("This relationship is not authorized for canvas control")
            connections[edge.id] = edge
            for key in (edge.source, edge.target):
                node = services.world.get_card(key)
                for card in self._related_cards([node, *services.world.owned_descendants(key)]):
                    self._card(scope, card)
                    cards[card.id] = card
            pending.extend(services.capabilities.forwarded_connections(edge, target_id))
            # Editing a shared adapter also changes grants held by upstream actors.
            upstream = [(parent, parent.target) for parent in services.world.connections_to(edge.source)]
            upstream += [(parent, parent.source) for parent in services.world.connections_from(edge.source)
                         if parent.direction == "bidirectional"]
            pending.extend((parent, via) for parent, via in upstream
                           if services.capabilities.forwards_connection(parent, via, edge))
        return list(cards.values()), list(connections.values())

    def _project(self, card, scope):
        model = self._services.plugins.node_type(card.type).config_model
        result = card.model_dump(mode="json", exclude={"resource", "config", "expanded", "chunk"})
        result["config"] = readable_config(model, card.config)
        result["writable_config_fields"] = [key for key, value in agent_config_policy(model).items()
                                            if value["agentWritable"] and "configure" in scope.operations]
        return result

    async def read_card(self, node_id: str):
        async with self._services._node_mutation(read_only=True):
            scope = self._scope("query")
            card = self._services.world.get_card(node_id)
            self._card(scope, card)
            return self._project(card, scope)

    async def query(self) -> dict[str, Any]:
        async with self._services._node_mutation(read_only=True):
            scope = self._scope("query")
            world = self._services.world
            cards = [card for card in world.list_cards() if self._allows(scope, card)]
            ids = {card.id for card in cards}
            edges = {edge.id: edge for edge in world.list_incident_edges(ids)}
            for card in world.list_cards():
                edge = world.equipment_edge(card)
                if edge and (edge.source in ids or edge.target in ids):
                    edges[edge.id] = edge
            return {
                "nodes": [self._project(card, scope) for card in cards],
                "allowed_operations": sorted(scope.operations),
                "edges": [{**edge.model_dump(mode="json"),
                           "external": edge.source not in ids or edge.target not in ids,
                           "derived": edge.id.startswith("equipment:")}
                          for edge in edges.values()],
                "versions": CanvasVersions(
                    cards={c.id: CanvasVersion(revision=c.revision, created_at=c.created_at) for c in cards},
                    edges={e.id: CanvasVersion(revision=e.revision, created_at=e.created_at) for e in edges.values()},
                ).model_dump(mode="json"),
            }

    async def create_card(self, request: CanvasCardCreate | dict, versions: CanvasVersions | dict):
        request, versions = _validated(CanvasCardCreate, request), _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("create")
            spec = services.plugins.node_type(request.type)
            validate_agent_config(spec.config_model, request.config)
            # Collection seeds materialize additional cards through a separate
            # document operation. Do not implicitly authorize that operation.
            if spec.container and spec.container.document_field:
                raise PermissionDeniedError("Document collection creation requires its dedicated host operation")
            try:
                draft = CardCreate(**request.model_dump())
                preview = services.world.preview_card(draft)
            except (ValidationError, ValueError, GraphValidationError, ResourceValidationError):
                raise ResourceValidationError("Invalid canvas card configuration") from None
            defaults = spec.config_model().model_dump(mode="json")
            validate_agent_config(spec.config_model, {key: value for key, value in preview.config.items()
                                                     if value != defaults.get(key)})
            self._card(scope, preview)
            if preview.parent_id:
                self._scope("reparent")
                parent = services.world.get_card(preview.parent_id)
                self._check(scope, versions, self._related_cards([parent]))
                self._membership_effects(scope, versions, preview.parent_id, "connect")
            result = await services.create_card(draft)
            return self._project(result, scope)

    async def update_card(self, node_id: str, patch: CanvasCardPatch | dict, versions: CanvasVersions | dict):
        patch, versions = _validated(CanvasCardPatch, patch), _validated(CanvasVersions, versions)
        return await self._update(node_id, patch.model_dump(exclude_unset=True), versions)

    async def _update(self, node_id, patch, versions, *, detach=False):
        if not patch:
            raise ResourceValidationError("Supply at least one canvas field to update")
        services = self._services
        operations = {"name": "rename", "position": "move", "size": "resize", "config": "configure", "parent_id": "reparent", "equipment": "detach"}
        async with services._node_mutation():
            scope = self._scope(*(operations[key] for key in patch), *( ["detach"] if detach else []))
            current = services.world.get_card(node_id)
            self._check(scope, versions, [current])
            if "parent_id" in patch and current.parent_id != patch["parent_id"]:
                self._membership_effects(scope, versions, current.parent_id, "disconnect")
                self._membership_effects(scope, versions, patch["parent_id"], "connect")
            validate_agent_config(services.plugins.node_type(current.type).config_model, patch.get("config") or {})
            if detach and current.equipment:
                self._scope("disconnect")
                cards, edges = self._connection_effects(scope, [services.world.equipment_edge(current)])
                self._check(scope, versions, cards, edges)
            try:
                request = CardPatch(**patch, expected_revision=current.revision)
                updates = services.expand_card_updates([CardBatchPatch(node_id=node_id, patch=request)])
                before = [services.world.get_card(item.node_id) for item in updates]
                after = [services.world.preview_update_card(item.node_id, item.patch) for item in updates]
            except (ValidationError, ValueError, GraphValidationError):
                raise ResourceValidationError("Invalid canvas card configuration") from None
            for previous, updated in zip(before, after, strict=True):
                validate_agent_config(services.plugins.node_type(previous.type).config_model,
                                      {key: updated.config.get(key) for key in previous.config.keys() | updated.config.keys()
                                       if previous.config.get(key) != updated.config.get(key)})
            self._check(scope, versions, self._related_cards(before))
            for card in after:
                self._card(scope, card)
                self._check(scope, versions, services.world.ancestors(card))
            for item in updates:
                item.patch.expected_revision = versions.cards[item.node_id].revision
            result = await services.update_cards(updates)
            return [self._project(card, scope) for card in result]

    async def move_card(self, node_id, position, versions):
        return await self.update_card(node_id, {"position": position}, versions)

    async def resize_card(self, node_id, size, versions):
        return await self.update_card(node_id, {"size": size}, versions)

    async def set_card_config(self, node_id, config, versions):
        return await self.update_card(node_id, {"config": config}, versions)

    async def detach_card(self, node_id, versions):
        versions = _validated(CanvasVersions, versions)
        return await self._update(node_id, {"parent_id": None, "equipment": None}, versions, detach=True)

    async def delete_cards(self, node_ids: list[str], versions: CanvasVersions | dict):
        from backend.world.models import CardsDelete
        request, versions = _validated(CardsDelete, {"node_ids": node_ids}), _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("delete")
            ids = services.expand_card_deletions(request.node_ids)
            cards = [services.world.get_card(key) for key in ids]
            edges = services.world.list_incident_edges(ids)
            edges += [edge for card in cards if (edge := services.world.equipment_edge(card)) is not None]
            self._check(scope, versions, self._related_cards(cards))
            for card in cards:
                self._membership_effects(scope, versions, card.parent_id, "disconnect")
            if edges:
                self._scope("disconnect")
                affected, connections = self._connection_effects(scope, edges)
                self._check(scope, versions, affected, connections)
            result = await services.delete_cards(ids, expected_revisions={c.id: c.revision for c in cards})
            return [self._project(card, scope) for card in result]

    async def connect_cards(self, source: str, target: str, relationship: str,
                            versions: CanvasVersions | dict, direction: str = "forward"):
        versions = _validated(CanvasVersions, versions)
        request = _validated(EdgeCreate, dict(source=source, target=target, relationship=relationship, direction=direction))
        services = self._services
        async with services._node_mutation():
            scope = self._scope("connect")
            request = services.world.normalize_edge_request(request)
            origin = services.world.get_card(request.source)
            preview = Edge(id=":canvas-proposed", **request.model_dump(exclude={"id"}), revision=1,
                           created_at=origin.created_at, updated_at=origin.updated_at)
            cards, edges = self._connection_effects(scope, [preview])
            self._check(scope, versions, cards, [e for e in edges if e.id != preview.id])
            return (await services.create_edge(request)).model_dump(mode="json")

    async def update_edge(self, edge_id: str, patch: dict, versions: CanvasVersions | dict):
        versions = _validated(CanvasVersions, versions)
        request = _validated(EdgePatch, _validated(CanvasEdgePatch, patch).model_dump(exclude_none=True))
        services = self._services
        async with services._node_mutation():
            scope = self._scope("update_edge")
            old = services.world.get_edge(edge_id)
            new = old.model_copy(update=request.model_dump(exclude_none=True, exclude={"expected_revision"}))
            for edge in (old, new):
                cards, edges = self._connection_effects(scope, [edge])
                self._check(scope, versions, cards, edges)
            request.expected_revision = old.revision
            return (await services.update_edge(edge_id, request)).model_dump(mode="json")

    async def disconnect_cards(self, edge_id: str, versions: CanvasVersions | dict):
        versions = _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("disconnect")
            edge = services.world.get_edge(edge_id)
            cards, edges = self._connection_effects(scope, [edge])
            self._check(scope, versions, cards, edges)
            return (await services.delete_edge(edge_id, expected_revision=edge.revision)).model_dump(mode="json")
