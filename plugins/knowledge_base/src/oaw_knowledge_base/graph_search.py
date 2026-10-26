"""Ranked lookup over the published graph: name resolution, free-text search and paths.

Pure functions over ``Entity``/``Relation`` lists that ``actions.graph`` has already
scoped by group and layer, so nothing here touches the database. There is no
embedding model: matching is word-boundary phrase and acronym matching, and relations
are ranked by reciprocal-rank fusion of two channels (how well their endpoints match
the query, and how well their predicate/statement/snippet text does). An embedding
channel could be fused in the same way later.
"""
from __future__ import annotations

import re
from collections import deque

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
RRF_K = 60
# A name phrase found inside the query, or an acronym equal to a query word.
STRONG = 2

_STOP_WORDS = frozenset("""
a an the and or of in on at to for from by with as is are was were be been it its this that
these those what which who how why does do did can could between among about based
relation relations relationship relationships connect connected connection link linked
graph knowledge base tell me show find
""".split())


def tokens(text):
    return _TOKEN_RE.findall(str(text or "").casefold())


def phrase(text):
    """Casefolded words joined by ``_`` — spacing and punctuation do not matter."""
    return "_".join(tokens(text))


def _content_tokens(text):
    # One-character tokens ("2" from "DPA-2") carry no meaning on their own; the
    # full name still matches as a phrase.
    return [token for token in tokens(text) if token not in _STOP_WORDS and len(token) > 1]


def _contains(outer, inner):
    return bool(inner) and f"_{inner}_" in f"_{outer}_"


def _labels(entity):
    """``(phrases, acronyms)`` of an entity's name and aliases. An acronym is the
    initials of a multi-word label, so "Nudged Elastic Band" answers to "NEB"."""
    phrases, acronyms = set(), set()
    values = [entity.name] + [a for a in (entity.properties or {}).get("aliases") or [] if isinstance(a, str)]
    for value in values:
        words = tokens(value)
        if not words:
            continue
        phrases.add("_".join(words))
        if len(words) >= 2:
            acronyms.add("".join(word[0] for word in words))
    return phrases, acronyms


def name_score(entity, needle):
    """How well a name the caller typed (already a ``phrase``) identifies ``entity``:
    4 exact, 3 one contains the other on word boundaries, 2 acronym, 1 loose substring."""
    if not needle:
        return 0
    phrases, acronyms = _labels(entity)
    best = 0
    for key in phrases:
        if key == needle:
            return 4
        if _contains(needle, key) and len(key) >= 2 or _contains(key, needle):
            best = max(best, 3)
        elif len(needle) >= 4 and needle in key:
            best = max(best, 1)
    if needle in acronyms:
        best = max(best, 2)
    return best


def resolve(entities, name):
    """Entities a typed name refers to, best matches first (stable within a score)."""
    needle = phrase(name)
    scored = [(name_score(entity, needle), entity) for entity in entities]
    return [entity for score, entity in sorted((s for s in scored if s[0]), key=lambda s: -s[0])]


def entity_query_score(entity, query_phrase, query_tokens):
    """Score an entity against a free-text question: a full name or alias found in it
    (longer is stronger), an acronym equal to one of its words, then word overlap."""
    phrases, acronyms = _labels(entity)
    score = 0.0
    for key in phrases:
        if len(key) >= 2 and _contains(query_phrase, key):
            score = max(score, 3 + len(key.split("_")))
    if any(acronym in query_tokens for acronym in acronyms if len(acronym) >= 2):
        score = max(score, 2.0)
    if score:
        return score
    wanted = set(query_tokens)
    name_words = {word for key in phrases for word in key.split("_")}
    description = (entity.properties or {}).get("description")
    overlap = len(wanted & name_words) + 0.5 * len(wanted & set(_content_tokens(description)))
    return overlap * 0.5


def _text_hits(relation, query_tokens):
    properties = relation.properties or {}
    words = set(tokens(relation.type))
    for key in ("description", "snippet"):
        words.update(tokens(properties.get(key)))
    return len(set(query_tokens) & words)


def _ranks(scores):
    """Competition ranking of the positive scores: equal scores share a rank, so a
    channel that cannot tell sixty relations apart does not pretend to."""
    ordered = sorted((item for item in scores.items() if item[1] > 0), key=lambda item: -item[1])
    ranks, rank = {}, 0
    for position, (key, score) in enumerate(ordered, start=1):
        if position == 1 or score != ordered[position - 2][1]:
            rank = position
        ranks[key] = rank
    return ranks


def search(entities, relations, query, *, needles=(), semantic=None):
    """``(entity_scores, relation_scores)`` for a free-text question, each ``{id: score}``
    holding only the positive ones. Relations are ranked by RRF over their endpoint
    match and their text match; a relation joining two matched concepts scores twice
    on the first channel, which is what puts "X and Y" relations on top.

    ``needles`` are names the caller typed explicitly (already ``phrase``d): they score
    through ``name_score`` so "DPA-SSE" is a strong match even when the question text
    is only those words. ``semantic`` is an optional ``{id: cosine}`` from an embedding
    model, already cut to the elements that stand out. It adds a third relation channel
    and surfaces concepts the words missed, scored below a name match so they never
    trigger path-finding."""
    semantic = semantic or {}
    query_phrase = phrase(query)
    query_tokens = _content_tokens(query)
    entity_scores = {}
    for entity in entities:
        score = entity_query_score(entity, query_phrase, query_tokens)
        if needles:
            score = max(score, max(name_score(entity, needle) for needle in needles))
        if entity.id in semantic:
            score = max(score, 1.0)
        if score > 0:
            entity_scores[entity.id] = score
    endpoint = {r.id: entity_scores.get(r.source_id, 0) + entity_scores.get(r.target_id, 0) for r in relations}
    text = {r.id: _text_hits(r, query_tokens) for r in relations}
    channels = [_ranks(endpoint), _ranks(text), _ranks({r.id: semantic.get(r.id, 0) for r in relations})]
    fused = {}
    for ranks in channels:
        for identifier, position in ranks.items():
            fused[identifier] = fused.get(identifier, 0) + 1 / (RRF_K + position)
    return entity_scores, fused


def adjacency(relations):
    graph = {}
    for relation in relations:
        if relation.source_id == relation.target_id:
            continue
        graph.setdefault(relation.source_id, []).append((relation, relation.target_id))
        graph.setdefault(relation.target_id, []).append((relation, relation.source_id))
    return graph


def shortest_paths(graph, start, goal, *, max_depth, max_paths=3):
    """Up to ``max_paths`` shortest paths from ``start`` to ``goal`` ignoring edge
    direction, each a list of relations; ``[]`` when none is within ``max_depth``.
    Parallel relations between the same two concepts are distinct paths."""
    if start == goal:
        return []
    distance = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal or distance[node] >= max_depth:
            continue
        for _, other in graph.get(node, ()):
            if other not in distance:
                distance[other] = distance[node] + 1
                queue.append(other)
    if goal not in distance:
        return []
    paths = []

    def walk(node, suffix):
        if len(paths) >= max_paths:
            return
        if node == start:
            paths.append(list(reversed(suffix)))
            return
        for relation, other in graph.get(node, ()):
            if distance.get(other) == distance[node] - 1:
                walk(other, suffix + [relation])

    walk(goal, [])
    return paths
