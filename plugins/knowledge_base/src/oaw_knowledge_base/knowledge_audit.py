"""Reviewing a knowledge draft before it is published: statistics, a prompt, typed operations.

MKB cleans its concept graph with a review agent that runs after extraction has
already written the graph. Here the review runs on the **draft**, before the
person's publish click, so the graph still only ever receives what a person approved.
The split is the same as ``insight_stats.py`` / ``insight_prompt.py``: the plugin
computes deterministic findings (predicate distribution, near-duplicate concept
clusters, isolated concepts, mirrored pairs, matches in the published graph) and
hands them with the draft to a model, which answers not with a rewritten draft but
with a short list of **typed operations** — merge these concepts, rename this
predicate, add this alias, delete that relation — each with a reason.
``apply_operations`` validates and applies them through ``normalize`` and returns a
log the person reads before publishing.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .errors import KnowledgeError
from .knowledge_prompt import (
    MAX_ALIASES, MAX_DESCRIPTION_CHARS, VAGUE_PREDICATES, canonical_predicate, evidence_level, normalize, slug,
)

OPERATIONS = ("merge_concepts", "rename_predicate", "set_predicate", "add_alias", "delete_relation",
              "delete_concept", "set_evidence", "flag")
# A reason is one line for the person; a model that reasons inside the field is cut, not refused.
MAX_REASON_CHARS = 300

# Predicate pairs that contradict each other between the same two concepts.
_ANTONYMS = (("causes", "prevents"), ("improves", "degrades"), ("increases", "decreases"),
             ("enhances", "reduces"), ("promotes", "inhibits"), ("stabilizes", "destabilizes"))

AUDIT_INSTRUCTION = (
    "You are reviewing one knowledge draft — the concept graph a model proposed from a set of "
    "documents — before a person publishes it into a shared knowledge graph. The draft lists "
    "concepts (name, aliases, description, the sources that named them) and relations — each a "
    "statement as important as a concept: source predicate target, a one-sentence description of "
    "what is claimed, an evidence level 1-4 and a quoted snippet. You are also given findings the "
    "system computed: how often each predicate is used, relations whose predicate is vague, groups "
    "of concept names that look like the same thing, concepts with no relation, pairs of concepts "
    "related in both directions, contradicting predicates between the same pair, and draft "
    "concepts whose name matches one already in the published graph. Work in this order. First, "
    "predicates: a predicate must say what the relation is, not that one exists. For every "
    "relation flagged as vague (related_to, associated_with, is_a used loosely …) read its "
    "description and snippet and give it the specific verb they state with set_predicate "
    "(\"raises\", \"detects\", \"is_precursor_of\" …); where several predicates say the same "
    "thing (improves / enhances / increases), rename the others to the one short active-voice "
    "snake_case name that is most specific — never to a more general one; never encode direction "
    "in the name, since the graph has source and target. Second, concept "
    "deduplication: merge names that denote the same thing — an acronym and its full form, a "
    "formula and its name, spelling variants — keeping the more complete or the already "
    "published name and making the others aliases; merge only when you are confident, and when a "
    "draft concept matches a published name, keep the published name. Third, cleanup: delete a "
    "relation that is a mere restatement of another, or that the snippet does not support; delete "
    "a concept that is a bare value, a metadata item or is meaningless on its own, after its "
    "relations. Never invent a concept or a relation, and never merge two things that the "
    "snippets show to be different; when in doubt, flag it for the person instead. Reply with "
    "exactly this JSON shape, in the order to apply: {\"operations\": [{\"op\": \"merge_concepts\", "
    "\"keep\": <name>, \"drop\": [<name>, ...], \"reason\": <one line>} | {\"op\": "
    "\"rename_predicate\", \"from\": <predicate>, \"to\": <predicate>, \"reason\": ...} | {\"op\": "
    "\"set_predicate\", \"source\": <name>, \"predicate\": <current predicate>, \"target\": <name>, "
    "\"to\": <specific predicate>, \"description\": <optional corrected sentence>, \"reason\": ...} | "
    "{\"op\": \"add_alias\", \"name\": <name>, \"alias\": <alias>, \"reason\": ...} | {\"op\": "
    "\"delete_relation\", \"source\": <name>, \"predicate\": <predicate>, \"target\": <name>, "
    "\"reason\": ...} | {\"op\": \"delete_concept\", \"name\": <name>, \"reason\": ...} | {\"op\": "
    "\"set_evidence\", \"source\": <name>, \"predicate\": <predicate>, \"target\": <name>, "
    "\"level\": <1-4>, \"reason\": ...} | {\"op\": \"flag\", \"message\": <what the person should "
    "check>}]}. Each reason is one short sentence for the person (under 200 characters) — think "
    "through the findings before you answer, not inside the JSON. An empty list means the draft is clean.")

_TOKEN = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- statistics


def _stem(token):
    # Just enough to see a plural: conductivities → conductivity, electrolytes → electrolyte.
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(name):
    return {_stem(token) for token in _TOKEN.findall(str(name).casefold())}


def _initials(name):
    words = [word for word in re.split(r"[\s\-_/]+", str(name)) if word]
    return "".join(word[0] for word in words).casefold() if len(words) > 1 else ""


def _compact(name):
    return "".join(_stem(token) for token in _TOKEN.findall(str(name).casefold()))


def _looks_alike(a, b):
    """Two names that probably denote one concept: identical once punctuation and
    spaces go, one the initials of the other, or sharing most of their words."""
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if _initials(a) == cb or _initials(b) == ca:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) > 1 and len(tb) > 1:
        overlap = len(ta & tb) / max(len(ta), len(tb))
        return overlap >= 0.6
    return False


def concept_clusters(entities):
    """Groups of concept names that look like duplicates, by name or alias."""
    names = [item["name"] for item in entities]
    alias_of = {}
    for item in entities:
        for alias in item.get("aliases") or []:
            alias_of.setdefault(slug(alias), item["name"])
    parent = {name: name for name in names}

    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(a, b):
        parent[find(a)] = find(b)

    for index, a in enumerate(names):
        for b in names[index + 1:]:
            if _looks_alike(a, b) or alias_of.get(slug(a)) == b or alias_of.get(slug(b)) == a:
                union(a, b)
    groups = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    return sorted((sorted(group) for group in groups.values() if len(group) > 1), key=lambda group: group[0])


def audit_statistics(draft, published_matches=()):
    """Deterministic findings over one draft, as the prompt hands them to the model."""
    entities, relations = draft["entities"], draft["relations"]
    by_id = {item["id"]: item["name"] for item in entities}
    name = lambda identifier: by_id.get(identifier, identifier)  # noqa: E731
    predicates = Counter(item["predicate"] for item in relations)
    degree = Counter()
    pairs = {}
    for item in relations:
        degree[item["source"]] += 1
        degree[item["target"]] += 1
        pairs.setdefault(frozenset((item["source"], item["target"])), []).append(item)
    isolated = sorted(item["name"] for item in entities if not degree[item["id"]])
    vague = [{"source": name(item["source"]), "predicate": item["predicate"], "target": name(item["target"]),
              "description": item.get("description")}
             for item in relations if item["predicate"] in VAGUE_PREDICATES]
    mirrored, contradictions = [], []
    for pair, items in pairs.items():
        if len(pair) != 2:
            continue
        directions = {(item["source"], item["target"]) for item in items}
        if len(directions) > 1:
            a, b = sorted(pair, key=lambda identifier: name(identifier))
            mirrored.append({"a": name(a), "b": name(b),
                             "predicates": sorted({f"{name(i['source'])} {i['predicate']} {name(i['target'])}" for i in items})})
        used = {item["predicate"] for item in items}
        for left, right in _ANTONYMS:
            if left in used and right in used:
                a, b = sorted(pair, key=lambda identifier: name(identifier))
                contradictions.append({"a": name(a), "b": name(b), "predicates": [left, right]})
    return {
        "concepts": len(entities), "relations": len(relations),
        "predicates": [{"predicate": key, "count": count}
                       for key, count in sorted(predicates.items(), key=lambda pair: (-pair[1], pair[0]))],
        "concept_clusters": concept_clusters(entities),
        "vague": sorted(vague, key=lambda item: (item["source"], item["target"])),
        "isolated": isolated,
        "mirrored": sorted(mirrored, key=lambda item: (item["a"], item["b"])),
        "contradictions": sorted(contradictions, key=lambda item: (item["a"], item["b"])),
        "published_matches": list(published_matches),
    }


def published_matches(entities, graph_store, layer):
    """Draft concepts whose name or an alias is already a published concept's name or
    alias under a different spelling — the names the review should converge on."""
    matches = []
    for item in entities:
        probes = [item["name"]] + list(item.get("aliases") or [])
        found = graph_store.find_names([probe.casefold() for probe in probes], layer=layer, limit=5)
        for hit in found:
            if hit["name"] != item["name"] and (_looks_alike(item["name"], hit["name"])
                                                or any(_looks_alike(probe, hit["name"]) for probe in probes)
                                                or any(slug(alias) == slug(item["name"]) for alias in hit["aliases"])):
                matches.append({"draft_name": item["name"], "published_name": hit["name"],
                                "published_aliases": hit["aliases"]})
                break
    return matches


# ---------------------------------------------------------------- prompt


def build_messages(prompt):
    """Chat messages for one JSON-mode completion over the whole draft."""
    draft = prompt["draft"]
    concepts = "\n".join(
        f"- {item['name']}" + (f" (also: {', '.join(item['aliases'])})" if item.get("aliases") else "")
        + (f" — {item['description']}" if item.get("description") else "")
        + f" [{len(item.get('source_ids') or [])} source(s)]"
        for item in draft["entities"])
    by_id = {item["id"]: item["name"] for item in draft["entities"]}
    relations = "\n".join(
        f"- {by_id.get(item['source'], item['source'])} {item['predicate']} {by_id.get(item['target'], item['target'])}"
        f" [evidence {item.get('evidence_level', 3)}]"
        + (f" — {item['description']}" if item.get("description") else "")
        + (f' "{item["snippet"]}"' if item.get("snippet") else "")
        for item in draft["relations"])
    user = (f"Draft: {draft['title']}\n\nFindings:\n{json.dumps(prompt['statistics'], indent=1, ensure_ascii=False)}\n\n"
            f"Concepts:\n{concepts}\n\nRelations:\n{relations}\n")
    return [{"role": "system", "content": prompt["instruction"]}, {"role": "user", "content": user}]


def parse_operations(answer):
    """The model's reply as the operation list ``knowledge_audit_apply`` accepts."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            raise KnowledgeError("The model did not return JSON") from None
    if isinstance(answer, list):
        answer = {"operations": answer}
    if not isinstance(answer, dict) or not isinstance(answer.get("operations"), list):
        raise KnowledgeError('The model must return {"operations": [{"op": ..., ...}]}')
    operations = []
    for index, item in enumerate(answer["operations"]):
        if not isinstance(item, dict) or str(item.get("op") or "") not in OPERATIONS:
            raise KnowledgeError(f"Operation {index + 1} is not one of {', '.join(OPERATIONS)}")
        operations.append(clamp_operation({key: value for key, value in item.items() if value is not None}))
    return operations


def clamp_reason(value):
    """A reason or message cut to one line's worth; ``None`` stays ``None``."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_REASON_CHARS else text[:MAX_REASON_CHARS - 1].rstrip() + "…"


def clamp_operation(operation):
    for key in ("reason", "message"):
        if key in operation:
            operation[key] = clamp_reason(operation[key])
    return operation


# ---------------------------------------------------------------- apply


def apply_operations(draft, operations):
    """Apply typed operations to a draft's elements; returns ``(entities, relations,
    applied, skipped)``. An operation naming something the draft does not have is
    skipped with its reason recorded, never guessed at."""
    entities = [dict(item, aliases=list(item.get("aliases") or [])) for item in draft["entities"]]
    relations = [dict(item) for item in draft["relations"]]
    applied, skipped = [], []

    def by_name(value):
        wanted = slug(value)
        for item in entities:
            if slug(item["name"]) == wanted or any(slug(alias) == wanted for alias in item["aliases"]):
                return item
        return None

    def relation_matches(item, source, predicate, target):
        return (item["source"] == source["id"] and item["target"] == target["id"]
                and item["predicate"] == canonical_predicate(predicate))

    for index, operation in enumerate(operations):
        op = operation.get("op")
        reason = str(operation.get("reason") or "").strip()
        entry = {"index": index, **operation}
        if op == "flag":
            applied.append(entry)
            continue
        if op == "merge_concepts":
            keep_name = str(operation.get("keep") or "").strip()
            keep = next((item for item in entities if slug(item["name"]) == slug(keep_name)), None) if keep_name else None
            drops = [by_name(value) for value in (operation.get("drop") or [])]
            drops = [item for item in drops if item is not None and item is not keep]
            renamed = None
            if keep is None and keep_name:
                # The kept name is not a concept of the draft — a published label, or
                # one of the dropped concepts' aliases: the first dropped concept takes it.
                holder = by_name(keep_name)
                candidate = holder if holder is not None else (drops[0] if drops else None)
                if candidate is not None:
                    renamed, keep = candidate["name"], candidate
                    keep["aliases"] = [alias for alias in keep["aliases"] if slug(alias) != slug(keep_name)]
                    if slug(renamed) != slug(keep_name):
                        keep["aliases"].insert(0, renamed)
                    keep["name"] = keep_name
                    drops = [item for item in drops if item is not keep]
            if keep is None or (not drops and renamed is None):
                skipped.append({**entry, "skipped": "no such concept in the draft"})
                continue
            for dropped in drops:
                for alias in [dropped["name"]] + dropped["aliases"]:
                    if slug(alias) != slug(keep["name"]) and slug(alias) not in {slug(a) for a in keep["aliases"]}:
                        keep["aliases"].append(alias)
                if dropped.get("description") and not keep.get("description"):
                    keep["description"] = dropped["description"]
                keep["source_ids"] = list(keep.get("source_ids") or []) + [
                    value for value in dropped.get("source_ids") or [] if value not in (keep.get("source_ids") or [])]
                for relation in relations:
                    for end in ("source", "target"):
                        if relation[end] == dropped["id"]:
                            relation[end] = keep["id"]
                entities.remove(dropped)
            keep["aliases"] = keep["aliases"][:MAX_ALIASES]
            applied.append({**entry, "merged": [item["name"] for item in drops], "into": keep["name"],
                            **({"renamed": renamed} if renamed else {})})
        elif op == "rename_predicate":
            source, target = canonical_predicate(operation.get("from") or ""), canonical_predicate(operation.get("to") or "")
            touched = [item for item in relations if item["predicate"] == source]
            if not touched or source == target:
                skipped.append({**entry, "skipped": "no relation uses that predicate"})
                continue
            for item in touched:
                item["predicate"] = target
            applied.append({**entry, "from": source, "to": target, "count": len(touched)})
        elif op == "add_alias":
            item, alias = by_name(operation.get("name") or ""), str(operation.get("alias") or "").strip()
            if item is None or not alias or by_name(alias) not in (None, item):
                skipped.append({**entry, "skipped": "no such concept, or the alias names another concept"})
                continue
            if slug(alias) != slug(item["name"]) and slug(alias) not in {slug(a) for a in item["aliases"]}:
                item["aliases"] = (item["aliases"] + [alias])[:MAX_ALIASES]
            applied.append(entry)
        elif op in ("delete_relation", "set_evidence", "set_predicate"):
            source, target = by_name(operation.get("source") or ""), by_name(operation.get("target") or "")
            matched = [item for item in relations if source and target
                       and relation_matches(item, source, operation.get("predicate") or "", target)]
            if not matched:
                skipped.append({**entry, "skipped": "no such relation in the draft"})
                continue
            if op == "delete_relation":
                for item in matched:
                    relations.remove(item)
            elif op == "set_evidence":
                for item in matched:
                    item["evidence_level"] = evidence_level(operation.get("level"))
            else:
                to = canonical_predicate(operation.get("to") or "")
                description = str(operation.get("description") or "").strip()[:MAX_DESCRIPTION_CHARS]
                if to == canonical_predicate(operation.get("predicate") or "") and not description:
                    skipped.append({**entry, "skipped": "the predicate is unchanged"})
                    continue
                for item in matched:
                    item["predicate"] = to
                    if description:
                        item["description"] = description
                entry["to"] = to
            applied.append(entry)
        elif op == "delete_concept":
            item = by_name(operation.get("name") or "")
            if item is None:
                skipped.append({**entry, "skipped": "no such concept in the draft"})
                continue
            relations[:] = [relation for relation in relations
                            if relation["source"] != item["id"] and relation["target"] != item["id"]]
            entities.remove(item)
            applied.append(entry)
        else:
            skipped.append({**entry, "skipped": "unknown operation"})
    # Re-key through normalize: merged endpoints collapse, renamed predicates re-id.
    entities, relations = normalize(entities, relations)
    return entities, relations, applied, skipped


