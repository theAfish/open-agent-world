"""Seed Agent-owned resources using native world documents and StateStore slots."""
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import NotFoundError
from backend.node_documents import write_document
from backend.world.models import CardCreate


class EquipmentSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str | None = Field(default=None, max_length=100)
    type: str = Field(max_length=128)
    relationship: str = Field(max_length=128)


class DefaultsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment: dict[str, EquipmentSlot] = Field(default_factory=dict, max_length=16)


class RestoreDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: DefaultsSnapshot


def metadata(services, node_id):
    try:
        scope = services.state.get_scope("node_document", node_id)
        return services.state.get_record(scope, "plugin_defaults").value
    except NotFoundError:
        return {}


def save_metadata(services, node_id, value):
    scope = services.state.ensure_scope("node_document", node_id, schema_id="core.node_document")
    services.state.set(scope, "plugin_defaults", value)


def ensure_equipment(services, owner_id):
    """Synchronous inert creation inside the host mutation/DB transaction."""
    owner = services.world.get_card(owner_id)
    declaration = services.plugins.node_type(owner.type).default_equipment
    if not declaration:
        return []
    current = metadata(services, owner_id)
    slots = deepcopy(current.get("equipment", {}))
    created = []
    for item in declaration:
        if item.key in slots:
            continue  # An unequipped/deleted slot is deliberately not reinstalled.
        node = services.world.create_card(CardCreate(type=item.type, name=item.name,
            position=owner.position, equipment={"owner_id": owner_id, "relationship": item.relationship}))
        spec = services.plugins.node_type(item.type)
        write_document(services, node.id, deepcopy(spec.document.initial_value), 0)
        slots[item.key] = {"node_id": node.id, "type": item.type, "relationship": item.relationship}
        created.append(services.enrich_card(node))
        services._publish_card_created_nowait(created[-1])
    if slots != current.get("equipment", {}):
        save_metadata(services, owner_id, {**current, "equipment": slots})
    return created


def capture_metadata(services, node_id, node_keys):
    value = deepcopy(metadata(services, node_id))
    for slot in value.get("equipment", {}).values():
        target = services.world.maybe_get_card(slot.get("node_id", ""))
        owned = target and target.equipment and target.equipment.owner_id == node_id
        slot["node_id"] = node_keys.get(target.id) if owned else None
    return value


def restore_metadata(services, node_id, value, node_ids):
    value = DefaultsSnapshot.model_validate(value).model_dump(mode="json", exclude_none=True, exclude_unset=True)
    for slot in value.get("equipment", {}).values():
        slot["node_id"] = node_ids.get(slot.get("node_id"))
    if value:
        save_metadata(services, node_id, value)


def migrate_equipment(services):
    # Fill previously unseen slots; existing resources remain editable local copies.
    with services.events.committed_batch(), services.database.transaction(immediate=True):
        for node in services.world.list_cards():
            if not node.missing_plugin:
                adopt_equipment(services, node.id)
                ensure_equipment(services, node.id)


def adopt_equipment(services, owner_id):
    owner = services.world.get_card(owner_id)
    declaration = services.plugins.node_type(owner.type).default_equipment
    if not declaration:
        return
    current = metadata(services, owner_id)
    slots = deepcopy(current.get("equipment", {}))
    for item in declaration:
        if item.key in slots:
            continue
        matches = [node for node in services.world.equipment_for(owner_id)
            if node.type == item.type and node.equipment.relationship in {None, item.relationship}]
        if matches:
            # Preserve existing private resources without guessing between
            # multiple edited copies. Their normal grants remain available.
            slots[item.key] = {"node_id": matches[0].id if len(matches) == 1 else None,
                "type": item.type, "relationship": item.relationship}
    if slots != current.get("equipment", {}):
        save_metadata(services, owner_id, {**current, "equipment": slots})
