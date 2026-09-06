"""Materialize declared document collections as live world nodes."""
from backend.events import EventType
from backend.events.models import RuntimeEvent
from backend.state import StateContext
from backend.world.models import CardCreate, CardPatch


def touch_parent(services, parent_id):
    if parent_id is None:
        return
    parent = services.world.maybe_get_card(parent_id)
    if parent is None:
        return
    spec = services.plugins.node_type(parent.type).container
    if spec is None or spec.document_field is None:
        return
    scope = services.state.ensure_scope("node_document", parent_id, schema_id="core.node_document")
    value = services.state.resolve(StateContext((scope,)), "document")
    services.state.set(scope, "document", value.value, expected_revision=value.revision)


def member_documents(services, node_id):
    from backend.node_documents import read_document
    return [{**read_document(services, member.id)["value"], "node_id": member.id}
            for member in services.world.list_members(node_id)]


def sync_members(services, node_id, entries):
    from backend.node_documents import read_document, write_document
    parent = services.world.get_card(node_id)
    spec = services.plugins.node_type(parent.type).container
    members = {member.id: member for member in services.world.list_members(node_id)}
    kept = set()
    for index, entry in enumerate(entries):
        key = entry.get("node_id")
        member = members.get(key)
        if member is None:
            member = services.world.create_card(CardCreate(type=spec.member_type, parent_id=node_id,
                name=entry["name"], position={"x": parent.position.x + spec.content_inset[0] + 100 + index % 3 * 310,
                                            "y": parent.position.y + spec.content_inset[1] + 40 + index // 3 * 210}))
            services._publish_card_created_nowait(member)
        kept.add(member.id)
        current = read_document(services, member.id)
        value = {**entry, "node_id": None}
        if current["value"] != value:
            write_document(services, member.id, value, current["revision"])
        if member.name != entry["name"]:
            updated = services.world.update_card(member.id, CardPatch(name=entry["name"]))
            services.events.publish_event_nowait(RuntimeEvent(type=EventType.CARD_UPDATED, node_id=member.id,
                payload={"node": updated.model_dump(mode="json")}))
    for key, member in members.items():
        if key not in kept:
            # Removing from a collection detaches the skill, preserving its edges.
            updated = services.world.update_card(key, CardPatch(parent_id=None))
            services.events.publish_event_nowait(RuntimeEvent(type=EventType.CARD_UPDATED, node_id=key,
                payload={"node": updated.model_dump(mode="json")}))


def migrate_collections(services):
    from backend.node_documents import write_document
    for node in services.world.list_cards():
        spec = services.plugins.node_type(node.type).container
        if spec is None or spec.document_field is None:
            continue
        scope = services.state.ensure_scope("node_document", node.id, schema_id="core.node_document")
        current = services.state.resolve(StateContext((scope,)), "document")
        if current.value.get(spec.document_field):
            write_document(services, node.id, current.value, current.revision)
        if node.size.width < spec.min_size[0] or node.size.height < spec.min_size[1]:
            services.world.update_card(node.id, CardPatch(size={"width": max(node.size.width, spec.min_size[0]), "height": max(node.size.height, spec.min_size[1])}))


def parent_first(nodes, *, key=lambda node: node.id, parent=lambda node: node.parent_id):
    by_id = {key(node): node for node in nodes}
    def depth(node):
        ancestor = by_id.get(parent(node))
        return 0 if ancestor is None else 1 + depth(ancestor)
    return sorted(nodes, key=depth)
