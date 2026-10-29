"""Identity migration for the three utility Packs absorbed into Core essentials."""

CORE = "open-agent-world.core"
CORE_PACK = CORE + ".default"
RETIRED_PACKS = {
    "oaw.barracks.default": ("oaw.barracks", "0.3.0"),
    "oaw.skills.default": ("oaw.skills", "0.2.0"),
    "oaw.tasks.default": ("oaw.tasks", "0.2.1"),
}
CONTRIBUTIONS = {
    "oaw.barracks": {
        "node_type": ("oaw.barracks", "oaw.barracks.summoner"),
        "relationship": ("oaw.barracks.use", "oaw.barracks.summon"),
        "capability_handler": ("oaw.barracks.summon",),
    },
    "oaw.skills": {
        "node_type": ("oaw.skills", "oaw.skills.skill"),
        "relationship": ("oaw.skills.use", "oaw.skills.skill.use"),
        "capability_handler": ("oaw.skills.read", "oaw.skills.skill.read"),
    },
    "oaw.tasks": {
        "node_type": ("oaw.tasks",),
        "relationship": ("oaw.tasks.executor", "oaw.tasks.control", "oaw.tasks.read", "oaw.tasks.progress", "oaw.tasks.edit"),
        "capability_handler": ("oaw.tasks.read", "oaw.tasks.upsert", "oaw.tasks.progress", "oaw.tasks.remove", "oaw.tasks.execute"),
    },
}


def migrated_owner(kind: str, identifier: str, owner: str) -> str:
    # Match the original owner AND exact contribution; never accept a namespace
    # prefix or turn an unrelated ownership mismatch into trusted Core content.
    return CORE if identifier in CONTRIBUTIONS.get(owner, {}).get(kind, ()) else owner


def migrate_world(connection) -> None:
    for owner, contributions in CONTRIBUTIONS.items():
        for table, column, kind in (("cards", "type", "node_type"), ("edges", "relationship", "relationship")):
            connection.executemany(
                f"UPDATE {table} SET plugin_id=? WHERE plugin_id=? AND {column}=?",
                [(CORE, owner, identifier) for identifier in contributions[kind]],
            )
        # These utility cards had no external lifecycle cleanup. Preserve any
        # unexpected finalizer journal for explicit recovery instead of claiming it.
        connection.executemany(
            "UPDATE pending_node_deletions SET plugin_id=? WHERE plugin_id=? "
            "AND requires_finalize=0 AND json_extract(card_json, '$.type')=?",
            [(CORE, owner, identifier) for identifier in contributions['node_type']],
        )


def legacy_pack_providers(registry):
    """Honor existing archive requirements without reviving separate Library packs.

    Versions identify the exact utility implementations incorporated into Core;
    newer or unrelated requirements must still fail normal version checks.
    """
    if not registry.has_plugin(CORE):
        return {}
    return {pack: (version, CORE, owner) for pack, (owner, version) in RETIRED_PACKS.items()}


def pack_owner(registry, pack_id):
    legacy = legacy_pack_providers(registry).get(pack_id)
    return legacy[1] if legacy else registry.owner_id("pack", pack_id)
