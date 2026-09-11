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

from backend.errors import DomainError, GraphValidationError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.plugins.config_policy import agent_config_policy, readable_config, validate_agent_config, config_write_risk
from backend.world.models import Card, CardBatchPatch, CardCreate, CardPatch, Edge, EdgeCreate, EdgePatch, Point, Size

if TYPE_CHECKING:
    from backend.services import ApplicationServices

Operation = Literal["query", "create", "delete", "move", "resize", "configure", "rename",
                    "reparent", "attach", "detach", "connect", "disconnect", "update_edge", "group", "glue"]


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
    # Opt-in endpoint authority for the facade's own actor, never ordinary card
    # mutation authority. Intersects with relationships; no caller-supplied ID.
    principal_relationships: frozenset[str] = frozenset()
    node_ids: frozenset[str] | None = None
    # Host opt-in, effective only when a pre-commit reviewer is installed.
    review_config: bool = False


class CanvasVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision: int = Field(ge=1, strict=True)
    # Undo can restore the same ID with revision 1. Reject that stale incarnation.
    created_at: datetime


class CanvasVersions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: dict[str, CanvasVersion] = Field(default_factory=dict)
    edges: dict[str, CanvasVersion] = Field(default_factory=dict)
    glue: int | None = Field(default=None, ge=0, strict=True)


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
                 authorize: Callable[[str], CanvasScope | None], *, review=None):
        self._services = services
        self._actor_id = actor_id
        self._authorize = authorize
        self._reviewer = review
        self._affected_cards = {}
        self._affected_edges = {}

    def _config(self, scope, model, patch):
        if scope.review_config and self._reviewer is not None:
            config_write_risk(model, patch)
        else:
            validate_agent_config(model, patch)

    def _review(self, operation, *, before=(), after=(), removed_edges=(), added_edges=(), **details):
        """One host check after validation, before any lifecycle effect or write.

        The reviewer can stop to request human confirmation. It cannot waive
        scope, schema or revision checks, and is never supplied by tool callers.
        """
        if self._reviewer is not None:
            self._reviewer(dict(operation=operation, before=list(before), after=list(after),
                affected_cards=list(self._affected_cards.values()), affected_edges=list(self._affected_edges.values()),
                removed_edges=list(removed_edges), added_edges=list(added_edges), **details))
        self._affected_cards.clear()
        self._affected_edges.clear()

    def _scope(self, *operations: Operation) -> CanvasScope:
        scope = self._authorize(self._actor_id)
        if not isinstance(scope, CanvasScope) or not set(operations) <= scope.operations:
            raise PermissionDeniedError("Canvas operation is not authorized")
        return scope

    def _allows(self, scope, card):
        return (not (scope.principal_relationships and card.id == self._actor_id)
                and card.type in scope.node_types and scope.bounds.contains(card)
                and (scope.node_ids is None or card.id in scope.node_ids))

    def _card(self, scope, card):
        if not self._allows(scope, card):
            raise PermissionDeniedError("Canvas change reaches an object outside the control scope")

    def _check(self, scope, versions, cards=(), edges=(), *, connection=False):
        for items, expected in ((cards, versions.cards), (edges, versions.edges)):
            for item in items:
                if isinstance(item, Card) and not (connection and scope.principal_relationships and item.id == self._actor_id):
                    self._card(scope, item)
                version = expected.get(item.id)
                if version is None or version.revision != item.revision or version.created_at != item.created_at:
                    raise RevisionConflictError("Affected canvas objects changed or were not read; query again")
                (self._affected_cards if isinstance(item, Card) else self._affected_edges)[item.id] = item

    def _related_cards(self, cards):
        world = self._services.world
        return list({c.id: c for card in cards for c in [card, *world.ancestors(card)]}.values())

    def _glue_effects(self, scope, versions, node_ids):
        from backend.canvas_glue import read_glue
        glue = read_glue(self._services)
        bonds = [bond for bond in glue["bonds"] if {bond["a"], bond["b"]} & set(node_ids)]
        if bonds:
            if versions.glue != glue["revision"]:
                raise RevisionConflictError("Glue changed; inspect before changing attached cards")
            self._check(scope, versions, [self._services.world.get_card(key) for key in
                                         {key for bond in bonds for key in (bond["a"], bond["b"])}])
        return bonds

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
            self._check(scope, versions, cards, connections, connection=True)

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
                if scope.principal_relationships and key == self._actor_id:
                    if edge.relationship not in scope.principal_relationships:
                        raise PermissionDeniedError("This relationship is not authorized for the controller")
                    # Connecting the principal does not edit its position, grant,
                    # or private equipment. Actual forwarding is checked below.
                    cards[key] = node
                    continue
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
            principal = world.maybe_get_card(self._actor_id) if scope.principal_relationships else None
            ids = {card.id for card in cards}
            if principal:
                ids.add(principal.id)
            edges = {edge.id: edge for edge in world.list_incident_edges(ids)}
            for card in world.list_cards():
                edge = world.equipment_edge(card)
                if edge and (edge.source in ids or edge.target in ids):
                    edges[edge.id] = edge
            from backend.canvas_glue import read_glue
            glue = read_glue(self._services)
            return {
                "nodes": [self._project(card, scope) for card in cards],
                **({"principal": {**self._project(principal, scope), "writable_config_fields": []}} if principal else {}),
                "allowed_operations": sorted(scope.operations),
                "glue": {"boxes": {key: box for key, box in glue["boxes"].items() if key in ids},
                         "bonds": [{**bond, "external": not {bond["a"], bond["b"]} <= ids}
                                   for bond in glue["bonds"] if {bond["a"], bond["b"]} & ids]},
                "edges": [{**edge.model_dump(mode="json"),
                           "external": edge.source not in ids or edge.target not in ids,
                           "derived": edge.id.startswith("equipment:")}
                          for edge in edges.values()],
                "versions": CanvasVersions(
                    glue=glue["revision"],
                    cards={c.id: CanvasVersion(revision=c.revision, created_at=c.created_at) for c in [*cards, *([principal] if principal else [])]},
                    edges={e.id: CanvasVersion(revision=e.revision, created_at=e.created_at) for e in edges.values()},
                ).model_dump(mode="json"),
            }

    async def create_card(self, request: CanvasCardCreate | dict, versions: CanvasVersions | dict):
        request, versions = _validated(CanvasCardCreate, request), _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("create")
            spec = services.plugins.node_type(request.type)
            self._config(scope, spec.config_model, request.config)
            # Collection seeds materialize additional cards through a separate
            # document operation. Do not implicitly authorize that operation.
            if spec.container and spec.container.document_field:
                raise ResourceValidationError("Collection creation is not implemented by this canvas operation; use the dedicated collection operation")
            try:
                draft = CardCreate(**request.model_dump())
                preview = services.world.preview_card(draft)
            except (ValidationError, ValueError, GraphValidationError, ResourceValidationError):
                raise ResourceValidationError("Invalid canvas card configuration") from None
            defaults = spec.config_model().model_dump(mode="json")
            self._config(scope, spec.config_model, {key: value for key, value in preview.config.items()
                                                     if value != defaults.get(key)})
            self._card(scope, preview)
            if preview.parent_id:
                self._scope("reparent")
                parent = services.world.get_card(preview.parent_id)
                self._check(scope, versions, self._related_cards([parent]))
                self._membership_effects(scope, versions, preview.parent_id, "connect")
            self._review("create", after=[preview], config=request.config)
            result = await services.create_card(draft)
            return self._project(result, scope)

    async def update_card(self, node_id: str, patch: CanvasCardPatch | dict, versions: CanvasVersions | dict):
        patch, versions = _validated(CanvasCardPatch, patch), _validated(CanvasVersions, versions)
        return await self._update(node_id, patch.model_dump(exclude_unset=True), versions)

    async def _update(self, node_id, patch, versions, *, detach=False):
        return await self._update_batch([(node_id, patch)], versions)

    async def update_cards(self, updates, versions):
        versions = _validated(CanvasVersions, versions)
        if not 1 <= len(updates) <= 100 or len({item["node_id"] for item in updates}) != len(updates):
            raise ResourceValidationError("Supply 1–100 distinct cards for a layout/configuration batch")
        return await self._update_batch([(item["node_id"], _validated(CanvasCardPatch, item["patch"]).model_dump(exclude_unset=True)) for item in updates], versions)

    async def _update_batch(self, patches, versions):
        services = self._services
        operations = {"name": "rename", "position": "move", "size": "resize", "config": "configure", "parent_id": "reparent"}
        async with services._node_mutation():
            requests, removed, added, unglued = [], [], [], []
            scope = self._scope()
            for node_id, patch in patches:
                if not patch:
                    raise ResourceValidationError("Supply at least one canvas field to update")
                self._scope(*(operations[key] if key != "equipment" else "attach" if patch[key] else "detach" for key in patch))
                current = services.world.get_card(node_id)
                self._check(scope, versions, [current])
                if "parent_id" in patch or "equipment" in patch:
                    unglued.extend(self._glue_effects(scope, versions, [node_id]))
                if "parent_id" in patch and current.parent_id != patch["parent_id"]:
                    self._membership_effects(scope, versions, current.parent_id, "disconnect")
                    self._membership_effects(scope, versions, patch["parent_id"], "connect")
                self._config(scope, services.plugins.node_type(current.type).config_model, patch.get("config") or {})
                if "config" in patch or "parent_id" in patch:
                    edges = services.world.list_incident_edges([current.id])
                    if edges:
                        cards, connections = self._connection_effects(scope, edges)
                        self._check(scope, versions, cards, connections, connection=True)
                request = _validated(CardPatch, {**patch, "expected_revision": current.revision})
                if "equipment" in patch:
                    updated = services.world.preview_update_card(node_id, request)
                    old_edge, new_edge = services.world.equipment_edge(current), services.world.equipment_edge(updated)
                    for edge, operation in ((old_edge, "disconnect"), (new_edge, "connect")):
                        if edge:
                            self._scope(operation)
                            cards, edges = self._connection_effects(scope, [edge])
                            self._check(scope, versions, cards, [e for e in edges if e.id != f"equipment:{node_id}"], connection=True)
                    if old_edge:
                        self._check(scope, versions, edges=[old_edge])
                        removed.append(old_edge)
                    if new_edge:
                        added.append(new_edge)
                requests.append(CardBatchPatch(node_id=node_id, patch=request))
            try:
                updates = services.expand_card_updates(requests)
                before = [services.world.get_card(item.node_id) for item in updates]
                after = [services.world.preview_update_card(item.node_id, item.patch) for item in updates]
            except (ValidationError, ValueError, GraphValidationError):
                raise ResourceValidationError("Invalid canvas card configuration") from None
            for previous, updated in zip(before, after, strict=True):
                self._config(scope, services.plugins.node_type(previous.type).config_model,
                                      {key: updated.config.get(key) for key in previous.config.keys() | updated.config.keys()
                                       if previous.config.get(key) != updated.config.get(key)})
            self._check(scope, versions, self._related_cards(before))
            for card in after:
                self._card(scope, card)
                self._check(scope, versions, services.world.ancestors(card))
            from backend.canvas_glue import read_glue
            glue = read_glue(services)
            if any(card.id in glue["boxes"] for card in before):
                if versions.glue != glue["revision"]:
                    raise RevisionConflictError("Glue layout changed; inspect again before moving or resizing")
                for previous, updated in zip(before, after, strict=True):
                    if box := glue["boxes"].get(previous.id):
                        self._card(scope, updated.model_copy(update={"position": Point(
                            x=box["x"] + updated.position.x - previous.position.x,
                            y=box["y"] + updated.position.y - previous.position.y), "size": Size(
                            width=max(1, box["width"] + updated.size.width - previous.size.width),
                            height=max(1, box["height"] + updated.size.height - previous.size.height))}))
            for item in updates:
                item.patch.expected_revision = versions.cards[item.node_id].revision
            self._review("update", before=before, after=after, removed_edges=removed, added_edges=added,
                         organization={"removed_glue": unglued} if unglued else None)
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

    async def attach_card(self, node_id, owner_id, relationship, versions):
        return await self._update(node_id, {"parent_id": None, "equipment": {"owner_id": owner_id, "relationship": relationship}}, _validated(CanvasVersions, versions))

    async def group_cards(self, name, node_ids, versions):
        versions = _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("group", "reparent", "create")
            cards = [services.world.get_card(key) for key in node_ids]
            self._check(scope, versions, self._related_cards(cards))
            unglued = self._glue_effects(scope, versions, node_ids)
            edges = services.world.list_incident_edges(node_ids)
            if edges:
                affected, connections = self._connection_effects(scope, edges)
                self._check(scope, versions, affected, connections, connection=True)
            draft = services.preview_legion_group(name, node_ids)
            preview = services.world.preview_card(draft)
            self._card(scope, preview)
            self._review("group", before=cards, organization={"name": name, "members": node_ids,
                "position": preview.position.model_dump(), "size": preview.size.model_dump(), "removed_glue": unglued})
            return [self._project(card, scope) for card in await services.form_legion_group(name, node_ids)]

    async def glue_cards(self, node_ids, target_id, side, versions, *, detach=False):
        from backend.canvas_glue import change_glue
        return await change_glue(self, node_ids, target_id, side, _validated(CanvasVersions, versions), detach=detach)

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
            bonds = self._glue_effects(scope, versions, ids)
            for card in cards:
                self._membership_effects(scope, versions, card.parent_id, "disconnect")
            if edges:
                self._scope("disconnect")
                affected, connections = self._connection_effects(scope, edges)
                self._check(scope, versions, affected, connections, connection=True)
            self._review("delete", before=cards, removed_edges=edges, organization={"removed_glue": bonds} if bonds else None)
            result = await services.delete_cards(ids, expected_revisions={c.id: c.revision for c in cards})
            return [self._project(card, scope) for card in result]

    def _prepare_connection(self, scope, request):
        world = self._services.world
        request = world.normalize_edge_request(request)
        origin = world.get_card(request.source)
        preview = Edge(id=":canvas-proposed", **request.model_dump(exclude={"id"}), revision=1,
                       created_at=origin.created_at, updated_at=origin.updated_at)
        cards, edges = self._connection_effects(scope, [preview])
        return request, cards, [edge for edge in edges if edge.id != preview.id]

    async def connection_options(self, source: str, target: str):
        """Read-only preflight using the same checks as connect, without a grant cache."""
        services = self._services
        async with services._node_mutation(read_only=True):
            scope = self._scope("query")
            endpoints = [services.world.get_card(key) for key in (source, target)]
            for card in endpoints:
                if not (scope.principal_relationships and card.id == self._actor_id):
                    self._card(scope, card)
            result = []
            for definition in services.plugins.relationship_options(*(card.type for card in endpoints)):
                if definition.generated:
                    continue
                item = {"relationship": definition.id, "label": definition.label, "directions": sorted(definition.directions),
                        "description": definition.description, "permitted": False}
                try:
                    self._scope("connect")
                    request, _, affected_edges = self._prepare_connection(scope, EdgeCreate(
                        source=source, target=target, relationship=definition.id))
                    item.update(source=request.source, target=request.target, permitted=True)
                    item["requires_confirmation"] = definition.canvas_requires_confirmation or any(
                        services.plugins.relationship(edge.relationship).canvas_requires_confirmation for edge in affected_edges)
                    existing = next((edge for edge in services.world.connections_from(request.source)
                                     if edge.target == request.target), None)
                    if existing:
                        item.update(existing_edge_id=existing.id, existing_relationship=existing.relationship, permitted=False,
                                    reason="Already connected; reuse this relationship" if existing.relationship == request.relationship else
                                           "This pair already has another relationship")
                except DomainError as error:
                    item["reason"] = error.message
                result.append(item)
            return result

    async def connect_cards(self, source: str, target: str, relationship: str,
                            versions: CanvasVersions | dict, direction: str = "forward"):
        versions = _validated(CanvasVersions, versions)
        request = _validated(EdgeCreate, dict(source=source, target=target, relationship=relationship, direction=direction))
        services = self._services
        async with services._node_mutation():
            scope = self._scope("connect")
            request, cards, edges = self._prepare_connection(scope, request)
            self._check(scope, versions, cards, edges, connection=True)
            self._review("connect", added_edges=[request])
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
                self._check(scope, versions, cards, edges, connection=True)
            request.expected_revision = old.revision
            self._review("update_edge", removed_edges=[old], added_edges=[new])
            return (await services.update_edge(edge_id, request)).model_dump(mode="json")

    async def disconnect_cards(self, edge_id: str, versions: CanvasVersions | dict):
        versions = _validated(CanvasVersions, versions)
        services = self._services
        async with services._node_mutation():
            scope = self._scope("disconnect")
            edge = services.world.get_edge(edge_id)
            cards, edges = self._connection_effects(scope, [edge])
            self._check(scope, versions, cards, edges, connection=True)
            self._review("disconnect", removed_edges=[edge])
            return (await services.delete_edge(edge_id, expected_revision=edge.revision)).model_dump(mode="json")
