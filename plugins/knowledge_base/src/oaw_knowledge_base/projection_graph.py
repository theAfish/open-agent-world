"""The knowledge graph is the merge of the latest completed graph projections.

A graph-kind projection is how a project's concept graph is built now. Saving or
revising one rebuilds the graph's literature layer, so ``knowledge_graph`` queries, traversal and semantic search cover them like any other
graph element. Concepts merge by normalized name across projects; a relation is one
(source, predicate, target) triple whose evidence is every file that states it.
"""
from __future__ import annotations

from . import graph_search
from .graph_store import LITERATURE_LAYER


def _key(name):
    return graph_search.phrase(name) or str(name).strip().casefold()


MAX_SNIPPETS = 3


def _merge(target, item, files):
    """Fold one result's element in: evidence files union, the first description wins,
    and distinct supporting snippets from every project are kept (bounded)."""
    for key, values in (("source_ids", item.get("source_ids") or []),
                        ("source_files", [files[value] for value in item.get("source_ids") or [] if value in files])):
        target[key] = target.get(key, []) + [value for value in values if value not in target.get(key, [])]
    if item.get("description") and not target.get("description"):
        target["description"] = item["description"]
    snippets = target.setdefault("snippets", [])
    if item.get("snippet") and item["snippet"] not in snippets and len(snippets) < MAX_SNIPPETS:
        snippets.append(item["snippet"])
    if snippets:
        target["snippet"] = snippets[0]  # the field search and embeddings read


def build_payload(projections):
    """``{entities, relations}`` for ``kb.graph.extract``, plus the group each element
    last came from (the project that most recently contributed it)."""
    entities, relations, groups = {}, {}, {}
    for projection in projections:
        data, names = projection["data"], {}
        files = {source["source_id"]: source["relative_path"] for source in projection["sources"]}
        for item in data["entities"]:
            identifier = f"concept:{_key(item['name'])}"
            names[item["id"]] = identifier
            current = entities.setdefault(identifier, {
                "id": identifier, "type": item.get("type") or "concept", "name": item["name"],
                "aliases": []})
            if item["name"] != current["name"] and item["name"] not in current["aliases"]:
                current["aliases"].append(item["name"])
            _merge(current, item, files)
            groups[identifier] = projection["project_id"]
        for item in data["relations"]:
            source, target = names[item["source"]], names[item["target"]]
            predicate = graph_search.phrase(item["predicate"]) or item["predicate"].strip()
            identifier = f"{source}|{predicate}|{target}"
            current = relations.setdefault(identifier, {
                "id": identifier, "source": source, "target": target, "type": predicate})
            _merge(current, item, files)
            groups[identifier] = projection["project_id"]
    return {"entities": list(entities.values()), "relations": list(relations.values())}, groups


def sync(kb):
    """Rebuild the literature layer from the latest completed results."""
    store = kb.oaw_graph_store
    store.clear_layer(LITERATURE_LAYER)
    payload, groups = build_payload(kb.oaw_projections.latest_completed("graph"))
    if not payload["entities"]:
        return
    result = kb.graph.extract(payload, extractor=lambda value: value)
    # Results come back in payload order, so each element's group lines up by position.
    by_group = {}
    for kind, sent, stored in (
            [("entities", *pair) for pair in zip(payload["entities"], result.entities)]
            + [("relations", *pair) for pair in zip(payload["relations"], result.relations)]):
        by_group.setdefault(groups[sent["id"]], {"entities": [], "relations": []})[kind].append(stored.id)
    for group, ids in by_group.items():
        store.tag(ids["entities"], ids["relations"], group_id=group, layer=LITERATURE_LAYER)
