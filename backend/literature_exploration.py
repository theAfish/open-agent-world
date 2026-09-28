"""Scope-owned exploration topology, separate from capability-granting edges.

All mutations run under the host node lock and one database transaction. Papers
remain original objects; the basecamp contains an index, never copies of them.
"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from backend.errors import NotFoundError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.events import EventType, RuntimeEvent
from backend.node_documents import read_document, write_document
from backend.world.models import CardCreate, CardPatch, EdgeCreate
from backend.literature_records import allowed_papers
from backend.literature_skill_notes import skill_note_projection, publish_skill_resources


def basecamp(services, scope):
    return next((node for node in services.world.ancestors(scope) if node.type == "legion"), None)


def paper_position(services, scope, index, frontier_id=None):
    camp = basecamp(services, scope)
    origin = camp or scope
    x = origin.position.x + origin.size.width + 650
    y = origin.position.y + 80
    value = read_document(services, scope.id)["value"]
    road_id = "route:" + frontier_id if frontier_id else "trunk"
    road = next((road for road in value.get("exploration_roads", []) if road["id"] == road_id), None)
    if road:
        entities = {entity["id"]: entity for entity in value["exploration_nodes"]}
        tail = road["member_ids"][-1] if road["member_ids"] else road["anchor_id"]
        if tail in entities:
            try:
                previous = services.world.get_card(entities[tail]["node_id"])
                # New intake is placed outside the current tail; existing cards
                # are never moved merely because another search completes.
                offset = max(0, index - len(value["paper_ids"]))
                return {"x": previous.position.x + previous.size.width + 220 + offset * 580,
                        "y": previous.position.y}
            except NotFoundError:
                pass
    if frontier_id:
        marker = next((item for item in value["exploration_nodes"] if item.get("frontier_id") == frontier_id and item["kind"] == "trail"), None)
        if marker:
            try:
                route = services.world.get_card(marker["node_id"])
                x, y = route.position.x + 460, route.position.y
            except NotFoundError:
                pass
    return {"x": x + index * 580, "y": y}


def meaningful(arguments, key, limit=4000):
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ResourceValidationError(f"Provide {key}, up to {limit} characters")
    return value.strip()


def scoped_papers(services, value, identifiers, *, required=False):
    if not isinstance(identifiers, list) or len(identifiers) > 500 or any(not isinstance(key, str) for key in identifiers):
        raise ResourceValidationError("paper_ids must be a bounded list of scoped Paper IDs")
    ids = list(dict.fromkeys(identifiers))
    if required and not ids:
        raise ResourceValidationError("Select at least one existing scoped Paper")
    if not set(ids) <= allowed_papers(value):
        raise PermissionDeniedError("Selection contains a Paper outside this scope")
    if any(services.world.get_card(key).type != "library.paper" for key in ids):
        raise ResourceValidationError("Selection must contain Paper objects")
    return ids


def route(value, frontier_id):
    result = next((item for item in value["frontiers"] if item["id"] == frontier_id), None)
    if result is None or result["scope_revision"] != value["current_revision"]:
        raise ResourceValidationError("Choose a current route from this scope")
    return result


def link(value, source, target, relation, rationale=""):
    if source == target:
        raise ResourceValidationError("A topology connection needs two distinct entities")
    current = next((item for item in value["exploration_links"] if item["source"] == source and item["target"] == target), None)
    if current:
        if current["relation"] != relation:
            raise ResourceValidationError("This pair already has a different semantic relationship")
        return current
    result = {"id": uuid4().hex, "source": source, "target": target, "relation": relation, "rationale": rationale}
    value["exploration_links"].append(result)
    return result


async def materialize(services, scope, value, created, updated):
    """Idempotently bind logical results to physical external cards."""
    camp = basecamp(services, scope)
    origin = camp or scope
    cards = {node.id: node for node in services.world.list_cards()}
    indexes = [node for node in cards.values() if node.type == "literature.index" and node.config.get("scope_id") == scope.id]
    index = next(iter(indexes), None)
    if index is None:
        index = await services._create_card(CardCreate(type="literature.index", name="文献目录 · Literature index",
            parent_id=camp.id if camp else scope.parent_id,
            position={"x": scope.position.x, "y": scope.position.y + scope.size.height + 60},
            config={"scope_id": scope.id}), _publish_event=False)
        created.append(index)
        cards[index.id] = index
    if camp and index.parent_id != camp.id:
        services.node_execution.assert_editable(index.id, allow_delegated=True)
        index = services.world.update_card(index.id, CardPatch(parent_id=camp.id,
            position={"x": scope.position.x, "y": scope.position.y + scope.size.height + 60}, expected_revision=index.revision))
        cards[index.id] = index
        updated.append(index)
    entities = {item["id"]: item for item in value["exploration_nodes"]}
    entities.setdefault("trail:origin", {"id": "trail:origin", "node_id": None, "kind": "trail",
        "title": "研究范围起点", "scope_revision": value["current_revision"],
        "rationale": "研究范围的第一张路标；各条主干道从这里出发。"})
    for number, frontier in enumerate(value["frontiers"]):
        identifier = "trail:" + frontier["id"]
        entities.setdefault(identifier, {"id": identifier, "node_id": None, "kind": "trail",
            "title": frontier["query"], "frontier_id": frontier["id"], "rationale": frontier["rationale"],
            "scope_revision": frontier["scope_revision"]})
    paper_ids = sorted(allowed_papers(value))
    for number, paper_id in enumerate(paper_ids):
        paper = cards.get(paper_id)
        if not paper or paper.type != "library.paper":
            continue  # Keep stale provenance, but never recreate a deleted PDF.
        if camp and paper.parent_id == camp.id:
            services.node_execution.assert_editable(paper_id, allow_delegated=True)
            paper = services.world.update_card(paper.id, CardPatch(parent_id=None,
                position=paper_position(services, scope, number), expected_revision=paper.revision))
            cards[paper.id] = paper
            updated.append(paper)
        identifier = "paper:" + paper_id
        entities[identifier] = {**entities.get(identifier, {}), "id": identifier, "node_id": paper_id,
            "kind": "paper", "title": paper.name, "paper_id": paper_id, "scope_revision": value["current_revision"]}
    from backend.literature_skill_notes import materialize_skill_notes
    skill_notes = await materialize_skill_notes(services, scope, value, cards, created, updated)
    for method in value["methods"]:
        identifier = "method:" + method["id"]
        sources = list(dict.fromkeys(source["paper_id"] for source in method["sources"]))
        previous = entities.get(identifier, {})
        previous_node = cards.get(previous.get('node_id'))
        method_node_id = (skill_notes.get(method['id']) if previous_node is None or previous_node.config.get('research_projection') == 'paper_skill'
                          else previous_node.id)
        entities[identifier] = {**previous, "node_id": method_node_id, "id": identifier, "kind": "method", "title": method["name"],
            "method_id": method["id"], "method_revision": method["revision"], "paper_ids": sources, "scope_revision": value["current_revision"]}
    for item in value.get('micro_skills', []):
        identifier = 'micro_skill:' + item['id']
        sources = list(dict.fromkeys(source['paper_id'] for source in item['sources']))
        entities[identifier] = {**entities.get(identifier, {}), 'id': identifier,
            'node_id': skill_notes.get(item['id']), 'kind': 'perspective', 'title': item['name'],
            'micro_skill_id': item['id'], 'method_revision': item['revision'], 'paper_ids': sources,
            'scope_revision': value['current_revision'], 'rationale': '题录或摘要研究策略草稿；未读取全文或验证方法'}
    counters = {"trail": 0, "finding": 0}
    nearby_counts = {}
    for entity in entities.values():
        if entity["kind"] == "paper":
            continue
        category = "trail" if entity["kind"] == "trail" else "finding"
        number = counters[category]
        counters[category] += 1
        source_id = next(iter(entity.get("paper_ids", [])), "")
        nearby_number = nearby_counts.get(source_id, 0)
        nearby_counts[source_id] = nearby_number + 1
        node = cards.get(entity.get("node_id"))
        if node and (node.config.get("scope_id") != scope.id or node.config.get("entity_id") != entity["id"]):
            raise ResourceValidationError("Exploration marker does not belong to this scope")
        if node:
            continue
        if entity["kind"] == "collection" and not any(entity["id"] in road["member_ids"] for road in value.get("exploration_roads", [])):
            continue  # Curated directories stay in the hub until explicitly mounted.
        position = {"x": origin.position.x + origin.size.width + (180 if category == "trail" else 1650),
                    "y": origin.position.y + 80 + number * 680}
        if entity["id"] == "trail:origin":
            position = {"x": origin.position.x + origin.size.width + 40, "y": index.position.y}
        nearby = entities.get("paper:" + source_id)
        nearby = cards.get(nearby["node_id"]) if nearby else None
        if nearby:
            position = {"x": nearby.position.x + nearby.size.width + 80 + nearby_number % 2 * 400,
                        "y": nearby.position.y + 240 + nearby_number // 2 * 380}
        node = await services._create_card(CardCreate(type="literature." + category, name=entity["title"][:200],
            parent_id=None, position=position, config={"scope_id": scope.id, "entity_id": entity["id"],
                "frontier_id": entity.get("frontier_id")}), _publish_event=False)
        entity["node_id"] = node.id
        cards[node.id] = node
        created.append(node)
    value["exploration_nodes"] = list(entities.values())
    for frontier in value['frontiers']:
        previous = frontier.get('continued_from')
        if previous and 'trail:' + previous in entities:
            link(value, 'trail:' + previous, 'trail:' + frontier['id'], 'related', '经用户审阅后续接；历史检索与预算保留')
    for run in value["search_runs"]:
        frontier_id = run.get("frontier_id")
        if "trail:" + str(frontier_id) in entities:
            for paper_id in run.get("paper_ids", []):
                if "paper:" + paper_id in entities:
                    link(value, "trail:" + frontier_id, "paper:" + paper_id, "discovers", "Discovered in this route's recorded search")
    for entity in entities.values():
        if entity["kind"] == "method":
            valid_sources = {"paper:" + paper_id for paper_id in entity.get("paper_ids", [])}
            value["exploration_links"] = [item for item in value["exploration_links"] if not (
                item["target"] == entity["id"] and item["relation"] == "method" and item["source"] not in valid_sources)]
            for paper_id in entity.get("paper_ids", []):
                if "paper:" + paper_id in entities:
                    link(value, "paper:" + paper_id, entity["id"], "method", "Source-linked method; verification remains in MethodSpec")
        elif entity.get('micro_skill_id'):
            valid_sources = {'paper:' + paper_id for paper_id in entity.get('paper_ids', [])}
            value['exploration_links'] = [item for item in value['exploration_links'] if not (
                item['target'] == entity['id'] and item.get('micro_skill_source') and item['source'] not in valid_sources)]
            for paper_id in entity.get('paper_ids', []):
                if 'paper:' + paper_id in entities:
                    source = 'paper:' + paper_id
                    if not any(item['source'] == source and item['target'] == entity['id'] for item in value['exploration_links']):
                        item = link(value, source, entity['id'], 'related', '题录或摘要来源；研究策略未科学核验')
                        item['micro_skill_source'] = True
    return index


def publish(services, event, node):
    services.events.publish_event_nowait(RuntimeEvent(type=event, node_id=node.id,
        payload={"node": node.model_dump(mode="json")}))


async def physical_edges(services, index, value, created_edges, removed_edges):
    from backend.literature_roads import road_connections
    entities = {item["id"]: item for item in value["exploration_nodes"]}
    current_ids = {node.id for node in services.world.list_cards()}
    pairs = [(entities[item["source"]]["node_id"], entities[item["target"]]["node_id"], item["relation"])
              for item in value["exploration_links"] if item["source"] in entities and item["target"] in entities
              and not (item["relation"] == "discovers" and entities[item["source"]]["kind"] == "trail"
                       and entities[item["target"]]["kind"] == "paper")]
    pairs += [(entities["trail:" + item["frontier_id"]]["node_id"], item["barracks_id"], "camp")
              for item in value["path_camps"] if "trail:" + item["frontier_id"] in entities]
    pairs += [(node.config['paper_id'], node.id, 'related' if node.config.get('projection_kind') == 'micro_skill' else 'method') for node in services.world.list_cards()
               if node.type == 'text' and node.config.get('research_projection') == 'paper_skill'
               and not node.config.get('projection_historical')
              and node.config.get('scope_id') == index.config['scope_id'] and node.config.get('paper_id') in current_ids
              and not any(source == node.config['paper_id'] and target == node.id for source, target, _ in pairs)]
    road_pairs = [(entities[source]["node_id"] if source else index.id, entities[target]["node_id"], "road")
                  for source, target, _ in road_connections(value) if target in entities and (source is None or source in entities)]
    # The world has one physical edge per ordered pair. Navigation wins its
    # rendering slot; source/claim relationships remain intact in the document.
    road_endpoints = {(source, target) for source, target, _ in road_pairs}
    pairs = [pair for pair in pairs if (pair[0], pair[1]) not in road_endpoints] + road_pairs
    expected = {(source, target, "literature." + relation) for source, target, relation in pairs}
    trail_ids = {item["node_id"] for item in entities.values() if item["kind"] == "trail"}
    method_ids = {item["node_id"] for item in entities.values() if item["kind"] == "method"}
    method_ids.update(node.id for node in services.world.list_cards() if node.type == 'text'
        and node.config.get('research_projection') == 'paper_skill' and node.config.get('scope_id') == index.config['scope_id'])
    micro_note_ids = {node.id for node in services.world.list_cards() if node.type == 'text'
        and node.config.get('projection_kind') == 'micro_skill' and node.config.get('scope_id') == index.config['scope_id']}
    owned_ids = {index.id, *(item["node_id"] for item in entities.values() if item["kind"] != "paper")}
    road_prefix = "literature-road_" + sha256(index.config["scope_id"].encode()).hexdigest()[:24] + "_"
    for edge in services.world.list_edges():
        if ((edge.relationship == "literature.camp" and edge.source in trail_ids)
                or (edge.relationship == "literature.method" and edge.target in method_ids)
                or (edge.relationship == 'literature.related' and edge.target in micro_note_ids)
                or (edge.relationship == "literature.discovers" and edge.source in trail_ids
                    and any(item["kind"] == "paper" and item["node_id"] == edge.target for item in entities.values()))
                or (edge.relationship == "literature.contains" and edge.source == index.id)
                or (edge.relationship == "literature.road" and edge.id.startswith(road_prefix))) and (
                    edge.source, edge.target, edge.relationship) not in expected:
            removed_edges.append(services.world.delete_edge(edge.id))
    for source, target, relation in pairs:
        if source not in current_ids or target not in current_ids or source == target:
            continue
        existing = services.world.find_edge(source, target)
        if existing and existing.relationship != "literature." + relation:
            if relation == "road":
                if (source in owned_ids or target in owned_ids) and existing.relationship in {
                    "literature.contains", "literature.discovers", "literature.method", "literature.related",
                    "literature.supports", "literature.contrasts"}:
                    removed_edges.append(services.world.delete_edge(existing.id))
                    existing = None
                else:
                    # Another scope/world relationship may already own a shared
                    # Paper pair. This road remains authoritative in its scope.
                    continue
            elif existing.relationship == "literature.road":
                continue
            else:
                raise ResourceValidationError("A world connection conflicts with this scope's semantic relationship; resolve it before synchronizing")
        if not existing:
            created_edges.append(await services._create_edge_locked(EdgeCreate(source=source, target=target,
                id=road_prefix + uuid4().hex if relation == "road" else None,
                relationship="literature." + relation), _publish_event=False))


async def project_results(service, scope_id, capability=None):
    """Host projection after an already-authorized result write, never a grant."""
    services = service.services
    current = read_document(services, scope_id)
    value = deepcopy(current["value"])
    created, updated, edges, removed_edges = [], [], [], []
    with skill_note_projection(services) as note_changes, services.database.transaction(immediate=True):
        index = await materialize(services, services.world.get_card(scope_id), value, created, updated)
        from backend.literature_roads import ensure_roads, layout_roads
        ensure_roads(value)
        layout_roads(services, services.world.get_card(scope_id), index, value, updated,
            only_node_ids={node.id for node in created if node.id != index.id} |
                {node.id for node in updated if node.id != index.id and node.config.get('research_projection') != 'paper_skill'})
        await physical_edges(services, index, value, edges, removed_edges)
        result = service.save(scope_id, value, current["revision"], capability)
    for node in created:
        publish(services, EventType.CARD_CREATED, node)
    for node in updated:
        publish(services, EventType.CARD_UPDATED, node)
    await publish_skill_resources(services, note_changes)
    for edge in edges:
        await services._publish_edge_change(EventType.EDGE_CREATED, edge)
    for edge in removed_edges:
        await services._publish_edge_change(EventType.EDGE_DELETED, edge)
    return result


def stage_task(services, scope_id, value, arguments, capability):
    board_id = value.get("task_board_id")
    if not board_id or services.world.get_card(board_id).type != "matcreator.tasks":
        raise ResourceValidationError("Connect an existing research task board first")
    if capability and not any(cap.target_id == board_id and cap.kind == "matcreator.tasks.create_plan"
                              for cap in services.capabilities.derive(capability.agent_id).capabilities):
        raise PermissionDeniedError("Staging a task also requires a separate task-board manage grant")
    strategy = arguments.get("strategy")
    if strategy not in {"close_read", "method", "branch_search"}:
        raise ResourceValidationError("Choose close_read, method or branch_search")
    frontier_id, paper_id, method_id = (arguments.get(key) for key in ("frontier_id", "paper_id", "method_id"))
    if frontier_id:
        route(value, frontier_id)
    if strategy == "branch_search" and not frontier_id:
        raise ResourceValidationError("Branch search requires a current route")
    if paper_id:
        scoped_papers(services, value, [paper_id], required=True)
    if method_id and not any(item["id"] == method_id for item in value["methods"]):
        raise ResourceValidationError("Choose a method from this scope")
    if strategy != "branch_search" and not (paper_id or method_id):
        raise ResourceValidationError("Select the Paper or method to inspect")
    rationale = meaningful(arguments, "rationale")
    camp = next((item for item in value["path_camps"] if item["frontier_id"] == frontier_id), None)
    selected_camp = arguments.get("barracks_id") or (camp or {}).get("barracks_id")
    if selected_camp and (not camp or selected_camp != camp["barracks_id"]):
        raise ResourceValidationError("Select the camp explicitly attached to this route")
    from oaw_matcreator.tasks import create_plan
    services.node_execution.assert_editable(board_id, allow_delegated=True)
    current = read_document(services, board_id)
    title = {"close_read": "微观精读", "method": "方法提炼", "branch_search": "路径增强检索"}[strategy]
    description = (f"scope_id={scope_id}; scope_revision={value['current_revision']}; frontier_id={frontier_id}; "
        f"paper_id={paper_id}; method_id={method_id}; attached_barracks_id={selected_camp}.\n{rationale}\n"
        "Read the matching KDG skill before work. Use only existing explicit grants and saved budgets. "
        "The camp is a scheduling preference, not a permission grant. Report unavailable capabilities. "
        "No task is started by this plan. Scientific acceptance requires source-linked review.")
    if strategy in {"close_read", "method"}:
        description += (" Without full text, continue from held Paper metadata or source abstracts: read the MicroSkill "
            "contract and record kind=micro_skill with basis=metadata or abstract, exact Paper revision, research-strategy "
            "steps, and explicit missing information. A PDF is not a prerequisite for this draft. Clearly name its source "
            "level; never describe it as a reconstructed full-text experimental method or invent pages/anchors. "
            "When full text is available and a paper method is claimed, retain the strict source-bound MethodSpec path.")
    result = create_plan(deepcopy(current["value"]), {"title": title, "goal": rationale,
        "tasks": [{"id": uuid4().hex, "title": title, "description": description,
                   "acceptance": "Retain exact scope/source versions, source-level metadata snapshots, search receipts or genuine source anchors, and explicit limitations."}]})
    write_document(services, board_id, result, current["revision"], actor_id=capability.agent_id if capability else None)
    value["exploration_tasks"].append({"plan_id": result["plans"][-1]["id"], "task_board_id": board_id,
        "strategy": strategy, "frontier_id": frontier_id, "paper_id": paper_id, "method_id": method_id,
        "barracks_id": selected_camp, "scope_revision": value["current_revision"], "created_at": datetime.now(UTC).isoformat()})


async def organize(service, scope_id, arguments, capability=None):
    services = service.services
    scope = service.authorize(scope_id, "organize", capability)
    current = read_document(services, scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Reload the scope before changing exploration topology")
    value = deepcopy(current["value"])
    action = arguments.get("action", "sync")
    for key in ("action", "kind", "title", "rationale", "url", "frontier_id", "source", "target", "relation",
                "barracks_id", "strategy", "paper_id", "method_id", "road_id", "parent_id", "attach_after", "entity_id", "mode"):
        if arguments.get(key) is not None and not isinstance(arguments[key], str):
            raise ResourceValidationError(f"{key} must be text")
    if action not in {"sync", "add", "link", "core_collection", "attach_camp", "stage_task",
                      "migrate_roads", "reorder", "reparent", "route_mode", "move_member", "layout"}:
        raise ResourceValidationError("Unknown exploration action")
    if "layout" in arguments and type(arguments["layout"]) is not bool:
        raise ResourceValidationError("layout must be a boolean")
    created, updated, edges, removed_edges = [], [], [], []
    with skill_note_projection(services) as note_changes, services.database.transaction(immediate=True):
        index = await materialize(services, scope, value, created, updated)
        if action in {"add", "core_collection"}:
            kind = "collection" if action == "core_collection" else arguments.get("kind")
            if kind not in {"web", "perspective", "collection"}:
                raise ResourceValidationError("Add a web source or a perspective")
            title, rationale = meaningful(arguments, "title", 500), meaningful(arguments, "rationale")
            paper_ids = scoped_papers(services, value, arguments.get("paper_ids", []), required=kind == "collection")
            frontier_id = arguments.get("frontier_id")
            if frontier_id:
                route(value, frontier_id)
            elif kind == "collection":
                raise ResourceValidationError("A core collection must belong to an explicit current route")
            url = arguments.get("url")
            if url is not None:
                from oaw_library.contracts import normalize_source_url
                try:
                    url = normalize_source_url(url)
                except ValueError as exc:
                    raise ResourceValidationError(str(exc)) from exc
                if not url:
                    raise ResourceValidationError("Use a public HTTP(S) source URL")
            if kind == "web" and not url:
                raise ResourceValidationError("A web finding requires its source URL")
            if kind == "perspective" and not (paper_ids or url):
                raise ResourceValidationError("A perspective requires scoped Papers or an explicit source URL")
            existing = next((item for item in value["exploration_nodes"] if item["kind"] == kind and item["scope_revision"] == value["current_revision"] and
                (item.get("url") == url if kind == "web" else item["title"].casefold() == title.casefold()) and
                (kind != "collection" or item.get("frontier_id") == frontier_id)), None)
            if existing:
                entity = existing
                if kind == "perspective" and entity.get("url") and url and entity["url"] != url:
                    raise ResourceValidationError("This perspective title already has a different source URL; use a distinct title or link the existing entity explicitly")
                if url and not entity.get("url"):
                    entity["url"] = url
                entity["paper_ids"] = list(dict.fromkeys([*entity.get("paper_ids", []), *paper_ids]))
            else:
                entity = {"id": kind + ":" + uuid4().hex, "node_id": None, "kind": kind, "title": title,
                    "paper_ids": paper_ids, "url": url, "rationale": rationale, "frontier_id": frontier_id,
                    "scope_revision": value["current_revision"], "created_by": capability.agent_id if capability else "desktop"}
                value["exploration_nodes"].append(entity)
            if frontier_id:
                link(value, "trail:" + frontier_id, entity["id"], "contains" if kind == "collection" else "discovers", rationale)
            if kind == "collection":
                for paper_id in paper_ids:
                    link(value, entity["id"], "paper:" + paper_id, "contains", rationale)
            else:
                for paper_id in paper_ids:
                    link(value, "paper:" + paper_id, entity["id"], "related", "Source provenance; does not assert scientific support")
        elif action == "link":
            entities = {item["id"]: item for item in value["exploration_nodes"]}
            source, target = arguments.get("source"), arguments.get("target")
            if source not in entities or target not in entities:
                raise PermissionDeniedError("Both topology endpoints must belong to this scope")
            if any(entities[key]["scope_revision"] != value["current_revision"] for key in (source, target)):
                raise ResourceValidationError("Use entities from the current scope revision")
            relation = arguments.get("relation")
            if relation not in {"supports", "contrasts", "related"}:
                raise ResourceValidationError("Choose supports, contrasts or related")
            link(value, source, target, relation, meaningful(arguments, "rationale"))
        elif action == "attach_camp":
            frontier_id = arguments.get("frontier_id")
            route(value, frontier_id)
            camp_id = arguments.get("barracks_id")
            camp = services.world.get_card(camp_id or "")
            if camp.type != "oaw.barracks":
                raise ResourceValidationError("Choose an existing Barracks camp")
            if capability:
                owners = {capability.agent_id, *(node.id for node in services.world.list_cards()
                    if node.equipment and node.equipment.owner_id == capability.agent_id)}
                if not any(edge.target == camp_id and edge.source in owners and edge.relationship == "oaw.barracks.summon"
                           for edge in services.world.list_edges()):
                    raise PermissionDeniedError("This coordinator has no existing summon connection to the selected camp")
            value["path_camps"] = [item for item in value["path_camps"] if item["frontier_id"] != frontier_id]
            value["path_camps"].append({"frontier_id": frontier_id, "barracks_id": camp_id,
                "rationale": meaningful(arguments, "rationale"), "scope_revision": value["current_revision"]})
        elif action == "stage_task":
            stage_task(services, scope_id, value, arguments, capability)
        elif action in {"reorder", "reparent", "route_mode", "move_member"}:
            from backend.literature_roads import edit_road
            edit_road(value, action, arguments)
        index = await materialize(services, scope, value, created, updated)
        from backend.literature_roads import ensure_roads, layout_roads
        ensure_roads(value)
        explicit_layout = action == "layout" or action == "migrate_roads" and arguments.get("layout", False)
        layout_roads(services, scope, index, value, updated,
            only_node_ids=None if explicit_layout else {node.id for node in created if node.id != index.id} |
                {node.id for node in updated if node.id != index.id and node.config.get('research_projection') != 'paper_skill'},
            road_id=arguments.get("road_id") if action == "layout" else None)
        await physical_edges(services, index, value, edges, removed_edges)
        result = service.save(scope_id, value, current["revision"], capability)
    for node in created:
        publish(services, EventType.CARD_CREATED, node)
    for node in updated:
        publish(services, EventType.CARD_UPDATED, node)
    await publish_skill_resources(services, note_changes)
    for edge in edges:
        await services._publish_edge_change(EventType.EDGE_CREATED, edge)
    for edge in removed_edges:
        await services._publish_edge_change(EventType.EDGE_DELETED, edge)
    return result
