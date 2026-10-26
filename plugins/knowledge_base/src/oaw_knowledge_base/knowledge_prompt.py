"""Turning a knowledge prompt into one model request, and its answer back into a graph.

Mirrors ``insight_prompt.py``: the host bridge holds the model credentials the plugin
never gets, so building the chat messages and parsing the reply live in one place.
This is the agent-free path for **Extract knowledge** — the same
``knowledge_extract_prompt`` → ``knowledge_save`` pair a connected Lab assistant
drives through its tools, collapsed into one JSON-mode completion per chunk of a source.

The graph is concept-only, as in MKB's concept graph: every node is a concept named
as the document names it (with aliases for its other spellings), and every edge is a
free-text predicate the plugin normalizes to snake_case. Elements carry content-keyed
ids — ``concept:<slug>`` and ``<source>|<predicate>|<target>`` — so the same concept
named by two papers is one node, and republishing a draft upserts instead of
duplicating. Relations carry MKB's evidence level (1 causal … 4 inferred) and a short
quote so a reader can check the claim without opening the source.
"""
from __future__ import annotations

import json
import re

from .errors import KnowledgeError

# Offered to the model as examples, never enforced: any predicate the document
# supports is kept once normalized.
SUGGESTED_PREDICATES = ("related_to", "part_of", "causes", "improves", "degrades", "measured_by",
                        "is_a", "has_property", "used_in", "produced_by", "depends_on")
DEFAULT_PREDICATE = "related_to"
DEFAULT_EVIDENCE_LEVEL = 3
EVIDENCE_LEVELS = {
    1: "causal experimental evidence (controlled experiments demonstrating cause and effect)",
    2: "direct experimental observation (measurements, characterizations)",
    3: "correlative evidence (trends, associations without mechanistic proof)",
    4: "predicted or inferred (theory, simulation, extrapolation, the authors' speculation)",
}
MAX_ALIASES = 20
MAX_SNIPPET_CHARS = 500
MAX_DESCRIPTION_CHARS = 2000
MAX_PREDICATE_CHARS = 64
# Predicates that say a link exists without saying what it is. Allowed only when the
# document itself is that vague; the review step asks for something specific otherwise.
VAGUE_PREDICATES = frozenset({"related_to", "associated_with", "relates_to", "associates_with", "linked_to",
                              "connected_to", "correlated_with", "involves", "interacts_with"})

KNOWLEDGE_INSTRUCTION = (
    "You are reading part of one document from a research knowledge base and building its "
    "concept graph: what the document is about, and what it states about how those things "
    "relate. Nodes are concepts only — materials, phenomena, methods, properties, structures, "
    "conditions as named ideas (e.g. \"sintering temperature\"), never a bare value, a "
    "measurement result, a figure or table number, an author or any other metadata; such detail "
    "belongs in the description of the relation it supports. Each concept has a short canonical name as the "
    "document uses it, optional aliases (an acronym, a formula, another spelling the document "
    "also uses — list them so they do not become separate nodes), and an optional one-sentence "
    "description grounded in the text. A relation is a statement the document makes about two "
    "concepts, and is as important as a concept: it has a predicate — a short, specific verb "
    "phrase in the active voice, read as \"<source> <predicate> <target>\" (e.g. \"grain size\" "
    "\"improves\" \"fracture toughness\", \"XRD\" \"detects\" \"secondary phase\") — and a "
    "description: one sentence in the document's own terms saying exactly what it claims, with "
    "the values, conditions and comparisons that matter (e.g. \"Sintering at 1200 °C raises the "
    "ionic conductivity of LLZO from 1e-5 to 3e-4 S/cm\"). Never use a predicate that only says a "
    "link exists (related_to, associated_with, is_a used loosely) when the description says what "
    "the link is — turn the verb of the description into the predicate. The direction is the "
    "source acting on or belonging to the target; never encode direction in the predicate "
    "(write \"A causes B\", not \"B is_caused_by A\"). Reuse the names and predicates listed as "
    "already in this draft or in the published graph whenever the document means the same thing; "
    "prefer a name over its acronym. Every relation carries an evidence_level: "
    + "; ".join(f"{level} = {text}" for level, text in EVIDENCE_LEVELS.items())
    + " — and a snippet: the shortest quote (under 25 words) from the text that states it. "
    "Name only what the document actually says; never invent a concept or a relation. Prefer "
    "the ten to forty concepts that matter in this part over an exhaustive list. Reply with "
    "exactly this JSON shape: {\"entities\": [{\"name\": <name>, \"aliases\": [<name>, ...], "
    "\"description\": <optional>}], \"relations\": [{\"source\": <concept name>, \"predicate\": "
    "<verb phrase>, \"target\": <concept name>, \"description\": <one sentence>, "
    "\"evidence_level\": <1-4>, \"snippet\": <quote>}]}.")

_SLUG = re.compile(r"[^a-z0-9]+")
_COPULAS = {"is", "are", "was", "were", "be", "been", "being"}
_ARTICLE_HOLDERS = _COPULAS | {"has", "have", "had"}
_ARTICLES = {"a", "an", "the"}


def slug(value):
    return _SLUG.sub("_", str(value).strip().lower()).strip("_") or "value"


def canonical_predicate(value):
    """A free-text predicate as the snake_case key the graph stores.

    Lexical only: casefold, non-alphanumerics to ``_``, a leading copula and inner
    articles removed (``"is caused by"`` → ``caused_by``, ``"Improves the"`` →
    ``improves``). Synonyms are the lookup and audit steps' job, not this one.
    """
    text = slug(value) if str(value or "").strip() else ""
    if not text or text == "value":
        return DEFAULT_PREDICATE
    tokens = text.split("_")
    if set(tokens) <= _ARTICLES:
        return DEFAULT_PREDICATE
    # "is caused by" → "caused by", but "is a" stays: the copula is the predicate there.
    while len(tokens) > 1 and tokens[0] in _COPULAS and not set(tokens[1:]) <= _ARTICLES:
        tokens = tokens[1:]
    kept = [token for index, token in enumerate(tokens)
            if token not in _ARTICLES or (index and tokens[index - 1] in _ARTICLE_HOLDERS)]
    text = "_".join(kept or tokens)[:MAX_PREDICATE_CHARS].strip("_")
    return text or DEFAULT_PREDICATE


def entity_id(name):
    return f"concept:{slug(name)}"


def relation_id(source, predicate, target):
    return f"{source}|{predicate}|{target}"


def evidence_level(value):
    try:
        level = int(value)
    except (TypeError, ValueError):
        return DEFAULT_EVIDENCE_LEVEL
    return min(max(level, 1), 4)


def _aliases(raw, name):
    values = raw.get("aliases") or []
    if isinstance(values, str):
        values = [values]
    seen, result = {slug(name)}, []
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()[:200]
        key = slug(text) if text else ""
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result[:MAX_ALIASES]


def normalize(entities, relations):
    """Model or human-supplied elements as the lists the store keeps.

    Names are trimmed and deduplicated by id (first description wins, aliases union).
    Relation endpoints may be names, aliases or entity ids (what a saved draft
    hands back on update); a relation whose endpoint names no entity is dropped rather
    than refused, since one bad edge should not lose the document. Predicates are
    normalized; the legacy ``type`` key is read as ``predicate``. ``source_ids`` are
    carried through when present, unioned across duplicates, and left empty for the
    store to fill in otherwise.
    """
    cleaned, by_name = {}, {}
    for index, item in enumerate(entities or []):
        if not isinstance(item, dict):
            raise KnowledgeError(f"Entity {index + 1} must be an object")
        name = str(item.get("name") or "").strip()[:500]
        if not name:
            continue
        identifier = entity_id(name)
        description = str(item.get("description") or "").strip()[:2000] or None
        current = cleaned.get(identifier)
        if current is None:
            current = cleaned[identifier] = {"id": identifier, "name": name, "description": description,
                                             "aliases": [], "source_ids": []}
        elif description and not current["description"]:
            current["description"] = description
        for alias in _aliases(item, name):
            if slug(alias) not in {slug(existing) for existing in current["aliases"]}:
                current["aliases"].append(alias)
        current["aliases"] = current["aliases"][:MAX_ALIASES]
        _add_sources(current, item)
        by_name.setdefault(name.casefold(), identifier)
        by_name.setdefault(identifier, identifier)
        if item.get("id"):
            by_name.setdefault(str(item["id"]), identifier)
    # Aliases resolve endpoints only after every name has, and never override a name:
    # an alias that is also another concept's name stays listed (the review step
    # merges such pairs) but points nowhere new.
    names = {slug(item["name"]) for item in cleaned.values()}
    for item in cleaned.values():
        for alias in item["aliases"]:
            if slug(alias) not in names:
                by_name.setdefault(alias.casefold(), item["id"])
    links = {}
    for index, item in enumerate(relations or []):
        if not isinstance(item, dict):
            raise KnowledgeError(f"Relation {index + 1} must be an object")
        source = by_name.get(str(item.get("source") or "").strip().casefold())
        target = by_name.get(str(item.get("target") or "").strip().casefold())
        if not source or not target or source == target:
            continue
        predicate = canonical_predicate(item.get("predicate") or item.get("type") or "")
        identifier = relation_id(source, predicate, target)
        level = evidence_level(item.get("evidence_level", DEFAULT_EVIDENCE_LEVEL))
        snippet = str(item.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS] or None
        description = str(item.get("description") or "").strip()[:MAX_DESCRIPTION_CHARS] or None
        current = links.get(identifier)
        if current is None:
            current = links[identifier] = {"id": identifier, "source": source, "target": target,
                                           "predicate": predicate, "description": description,
                                           "evidence_level": level, "snippet": snippet, "source_ids": []}
        else:
            current["evidence_level"] = min(current["evidence_level"], level)
            for key, value in (("description", description), ("snippet", snippet)):
                if value and not current[key]:
                    current[key] = value
        _add_sources(current, item)
    return list(cleaned.values()), list(links.values())


def _add_sources(element, raw):
    for value in raw.get("source_ids") or []:
        if value and str(value) not in element["source_ids"]:
            element["source_ids"].append(str(value))


def build_system_prompt(prompt):
    return (prompt["instruction"]
            + "\n\nPredicates already in use or suggested (reuse when the meaning matches, "
              "otherwise choose your own): "
            + ", ".join(prompt.get("suggested_predicates") or SUGGESTED_PREDICATES))


def _known_block(prompt):
    lines = []
    for item in prompt.get("existing") or []:
        aliases = item.get("aliases") or []
        lines.append(f"- {item['name']}" + (f" (also: {', '.join(aliases)})" if aliases else ""))
    for item in prompt.get("published") or []:
        aliases = item.get("aliases") or []
        lines.append(f"- {item['name']} [published]" + (f" (also: {', '.join(aliases)})" if aliases else ""))
    return "\n".join(lines)


def build_messages(prompt, markdown=None):
    """Chat messages for an OpenAI-compatible completion. ``markdown`` lets a caller
    that paged through ``has_more`` pass the whole document or chunk instead of one window."""
    known = _known_block(prompt)
    predicates = prompt.get("existing_predicates") or []
    heading = prompt.get("heading_path")
    user = (f"Document: {prompt.get('filename') or prompt['source_id']}\n"
            + (f"Section: {heading}\n" if heading else "")
            + (f"\nConcepts already in this draft or the published graph (reuse these names where "
               f"they fit):\n{known}\n" if known else "")
            + (f"\nPredicates already used around them: "
               + ", ".join(f"{item['predicate']} ({item['count']})" if isinstance(item, dict) else str(item)
                           for item in predicates) + "\n" if predicates else "")
            + "\nText:\n\n" + (markdown if markdown is not None else prompt["markdown"]))
    return [{"role": "system", "content": build_system_prompt(prompt)},
            {"role": "user", "content": user}]


def parse_graph(answer, *, allow_empty=False):
    """The model's reply as ``(entities, relations)`` ``knowledge_save`` accepts.
    ``allow_empty`` lets one chunk of a long document (a reference list, a figure
    index) legitimately name nothing without failing the whole extraction."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            raise KnowledgeError("The model did not return JSON") from None
    if not isinstance(answer, dict) or not isinstance(answer.get("entities"), list):
        raise KnowledgeError('The model must return {"entities": [{"name": ..., "aliases": [...]}], '
                             '"relations": [{"source": ..., "predicate": ..., "target": ..., '
                             '"description": ..., "evidence_level": ..., "snippet": ...}]}')
    entities, relations = normalize(answer["entities"], answer.get("relations") or [])
    if not entities and not allow_empty:
        raise KnowledgeError("The model named no concepts in this document")
    return entities, relations
