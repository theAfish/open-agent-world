"""Ordered navigation roads; source provenance remains in the scope records."""
from collections import defaultdict

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.world.models import CardPatch


def road_connections(value):
    """Return (source entity ID or None=index, target entity ID, road ID)."""
    roads = {road["id"]: road for road in value.get("exploration_roads", [])}
    departure = "trail:origin" if any(e["id"] == "trail:origin" for e in value.get("exploration_nodes", [])) else None
    result = [(None, departure, "trunk")] if departure else []
    for road in roads.values():
        previous = road["anchor_id"] or departure
        for member in road["member_ids"]:
            result.append((previous, member, road["id"]))
            previous = member
        if road["parent_id"] is None and road["anchor_id"] is not None:
            result.append((departure, road["anchor_id"], road["id"]))
        if road["parent_id"] is not None:
            parent = roads[road["parent_id"]]
            endpoint = (parent["member_ids"][-1] if parent["member_ids"] else parent["anchor_id"]) if parent else None
            origin = road["attach_after"] if road["mode"] == "branch" and road.get("attach_after") else endpoint
            result.append((origin, road["anchor_id"], road["id"]))
    return result


def validate_roads(value):
    roads = value.get("exploration_roads", [])
    if not roads:
        return
    mapping = {road["id"]: road for road in roads}
    entities = {entity["id"]: entity for entity in value["exploration_nodes"]}
    if len(mapping) != len(roads) or "trunk" not in mapping:
        raise ResourceValidationError("Road IDs must be unique and include the trunk")
    used = set()
    for road in roads:
        identifier = road["id"]
        if identifier == "trunk":
            if road["parent_id"] is not None or road["anchor_id"] is not None:
                raise ResourceValidationError("The trunk starts at the directory and has no parent")
        else:
            if road["parent_id"] is not None and (road["parent_id"] not in mapping or road["parent_id"] == identifier):
                raise ResourceValidationError("Choose a different existing parent road")
            anchor = entities.get(road["anchor_id"])
            if not anchor or anchor["kind"] != "trail" or anchor.get("frontier_id") != road.get("frontier_id"):
                raise ResourceValidationError("A route road must begin at its own existing trail marker")
        for member in road["member_ids"]:
            if member not in entities:
                raise PermissionDeniedError("Road members must be existing entities from this scope")
            if entities[member]["kind"] == "trail":
                raise ResourceValidationError("Use reparent to move a trail road; it cannot become an ordinary member")
            if member in used:
                raise ResourceValidationError("An entity has one navigation location; link shared perspectives through semantic connections")
            used.add(member)
        if road.get("attach_after"):
            parent = mapping.get(road["parent_id"])
            if not parent or road["attach_after"] not in [parent["anchor_id"], *parent["member_ids"]]:
                raise ResourceValidationError("The branch attachment must belong to its parent road")
        visited, cursor = {identifier}, road["parent_id"]
        while cursor is not None:
            if cursor not in mapping:
                raise ResourceValidationError("Choose an existing parent road")
            if cursor in visited:
                raise ResourceValidationError("Road parenting cannot form a cycle")
            visited.add(cursor)
            cursor = mapping[cursor]["parent_id"]


def ensure_roads(value):
    """Append new results without reordering prior user-authored navigation."""
    roads = value.setdefault("exploration_roads", [])
    revision = value["current_revision"]
    if not roads:
        roads.append({"id": "trunk", "title": "初始检索与种子文献", "parent_id": None, "anchor_id": None,
            "attach_after": None, "mode": "chain", "member_ids": [], "frontier_id": None, "scope_revision": revision})
    mapping = {road["id"]: road for road in roads}
    entities = {entity["id"]: entity for entity in value["exploration_nodes"]}
    for frontier in value["frontiers"]:
        identifier = "route:" + frontier["id"]
        if identifier not in mapping and "trail:" + frontier["id"] in entities:
            road = {"id": identifier, "title": frontier["query"], "parent_id": None,
                "anchor_id": "trail:" + frontier["id"], "attach_after": None,
                "mode": "chain" if len(roads) == 1 else "branch", "member_ids": [],
                "frontier_id": frontier["id"], "scope_revision": frontier["scope_revision"]}
            roads.append(road)
            mapping[identifier] = road
    # First discovery determines default navigation. A prior unscoped search is
    # retained on the trunk even if a later branch rediscovers the same Paper.
    first_discovery = {}
    for run in value["search_runs"]:
        for paper_id in run.get("paper_ids", []):
            first_discovery.setdefault("paper:" + paper_id, "route:" + run["frontier_id"] if run.get("frontier_id") else "trunk")
    existing = {member for road in roads for member in road["member_ids"]}
    seeds = value["revisions"][-1]["seed_paper_ids"] if value["revisions"] else []
    ordered = ["paper:" + key for key in dict.fromkeys([*value["paper_ids"], *seeds])]
    # Source-bound methods and Micro-Skills are nearby attachments by default.
    # Users may explicitly move them onto a road when appropriate.
    ordered += [entity["id"] for entity in value["exploration_nodes"]
                if entity["kind"] not in {"trail", "paper", "method", "collection"} and not entity.get("micro_skill_id")]
    for identifier in ordered:
        if identifier in existing or identifier not in entities:
            continue
        entity = entities[identifier]
        target = first_discovery.get(identifier, "route:" + entity["frontier_id"] if entity.get("frontier_id") else "trunk")
        road = mapping.get(target, mapping["trunk"])
        # Methods/other source-derived findings follow their source's road.
        if entity["kind"] != "paper" and not entity.get("frontier_id") and entity.get("paper_ids"):
            source_id = "paper:" + entity["paper_ids"][0]
            road = next((candidate for candidate in roads if source_id in candidate["member_ids"]), road)
        road["member_ids"].append(identifier)
        existing.add(identifier)
    validate_roads(value)


def edit_road(value, action, arguments):
    ensure_roads(value)
    roads = {road["id"]: road for road in value["exploration_roads"]}
    road = roads.get(arguments.get("road_id"))
    if road is None:
        raise ResourceValidationError("Choose an existing road from this scope")
    if action == "reorder":
        members = arguments.get("member_ids")
        if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
            raise ResourceValidationError("Provide the full ordered member_ids list")
        if len(members) != len(road["member_ids"]) or set(members) != set(road["member_ids"]):
            raise ResourceValidationError("Reordering must include every current member exactly once")
        road["member_ids"] = members
    elif action == "reparent":
        if road["id"] == "trunk":
            raise ResourceValidationError("The directory trunk cannot be reparented")
        road["parent_id"] = arguments.get("parent_id")
        road["attach_after"] = arguments.get("attach_after")
    elif action == "route_mode":
        mode = arguments.get("mode")
        if mode not in {"chain", "branch"}:
            raise ResourceValidationError("Choose chain or branch")
        road["mode"] = mode
    elif action == "move_member":
        member = arguments.get("entity_id")
        entities = {entity["id"]: entity for entity in value["exploration_nodes"]}
        if member not in entities:
            raise PermissionDeniedError("Choose an existing entity from this scope")
        if entities[member]["kind"] == "trail":
            raise ResourceValidationError("Use reparent for trail roads")
        offset = arguments.get("index", len(road["member_ids"]))
        if type(offset) is not int or offset < 0 or offset > len(road["member_ids"]):
            raise ResourceValidationError("Insertion index is outside the selected road")
        for candidate in roads.values():
            candidate["member_ids"] = [key for key in candidate["member_ids"] if key != member]
            if candidate.get("attach_after") == member and candidate["parent_id"] != road["id"]:
                # Keep the child on its declared parent, using that road's end.
                candidate["attach_after"] = None
        road["member_ids"].insert(min(offset, len(road["member_ids"])), member)
    validate_roads(value)


def layout_roads(services, scope, index, value, updated, *, only_node_ids=None, road_id=None):
    """Outward layout. Existing positions change only on explicit layout."""
    from backend.literature_exploration import basecamp
    ensure_roads(value)
    roads = {road["id"]: road for road in value["exploration_roads"]}
    if road_id is not None and road_id not in roads:
        raise ResourceValidationError("Choose an existing road to lay out")
    cards = {node.id: node for node in services.world.list_cards()}
    entities = {entity["id"]: entity for entity in value["exploration_nodes"]}
    children = defaultdict(list)
    for road in roads.values():
        children[road["parent_id"]].append(road["id"])
    selected = set(roads) if road_id is None else set()
    def select(identifier):
        selected.add(identifier)
        for child in children[identifier]:
            select(child)
    if road_id is not None:
        select(road_id)
    camp = basecamp(services, scope)
    exit_x = max(index.position.x + index.size.width + 240,
                 camp.position.x + camp.size.width + 240 if camp else index.position.x + 640)
    placements = {}
    def node_for(entity_id):
        entity = entities.get(entity_id)
        return cards.get(entity["node_id"]) if entity else index
    def position(entity_id):
        node = node_for(entity_id)
        return placements.get(entity_id, {"x": node.position.x, "y": node.position.y}) if node else {"x":exit_x,"y":index.position.y}
    def place(entity_id, x, y, identifier):
        node = node_for(entity_id)
        if not node or (entity_id is not None and node.parent_id is not None):
            return
        if identifier in selected and (only_node_ids is None or node.id in only_node_ids):
            placements[entity_id] = {"x": x, "y": y}
    def walk(identifier):
        road = roads[identifier]
        if identifier == "trunk":
            x, y = exit_x, index.position.y
        else:
            parent = roads.get(road["parent_id"])
            parent_endpoint = (parent["member_ids"][-1] if parent["member_ids"] else parent["anchor_id"]) if parent else None
            origin = road.get("attach_after") if road["mode"] == "branch" and road.get("attach_after") else parent_endpoint
            source = node_for(origin)
            source_position = position(origin)
            siblings = children[road["parent_id"]]
            offset = siblings.index(identifier)
            spread = (offset // 2 + 1) * (1 if offset % 2 == 0 else -1) * 640
            x = max(exit_x, source_position["x"] + (source.size.width if source else 360) + 220)
            y = source_position["y"] + (0 if road["mode"] == "chain" and offset == 0 else spread)
            place(road["anchor_id"], x, y, identifier)
            anchor = node_for(road["anchor_id"])
            anchor_position = position(road["anchor_id"])
            x, y = anchor_position["x"] + (anchor.size.width if anchor else 220) + 220, anchor_position["y"]
        for member in road["member_ids"]:
            place(member, x, y, identifier)
            node = node_for(member)
            placed = position(member)
            x, y = placed["x"] + (node.size.width if node else 360) + 220, placed["y"]
        for child in children[identifier]:
            walk(child)
    for root_id in children[None]:
        walk(root_id)
    memberships = {member:road["id"] for road in roads.values() for member in road["member_ids"]}
    method_slots = defaultdict(int)
    for entity in entities.values():
        if (entity["kind"] != "method" and not entity.get("micro_skill_id")) or entity["id"] in memberships or not entity.get("paper_ids"):
            continue
        source_id = "paper:" + entity["paper_ids"][0]
        source = node_for(source_id)
        source_road = memberships.get(source_id)
        if not source or not source_road:
            continue
        point = position(source_id)
        slot = method_slots[source_id]
        method_slots[source_id] += 1
        place(entity["id"], point["x"] + slot % 2 * 400,
              point["y"] + source.size.height + 90 + slot // 2 * 380, source_road)
    for entity_id, point in placements.items():
        node = node_for(entity_id)
        if node.position.x == point["x"] and node.position.y == point["y"]:
            continue
        services.node_execution.assert_editable(node.id, allow_delegated=True)
        changed = services.world.update_card(node.id, CardPatch(position=point, expected_revision=node.revision))
        cards[changed.id] = changed
        updated.append(changed)
