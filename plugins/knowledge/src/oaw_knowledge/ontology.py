"""Ontology (knowledge.ontology): entities with aliases and typed relations, written by Agents.

The ontology resolves how papers name things ("LFP", "lithium iron phosphate", "LiFePO₄")
to one entity, and records cross-paper knowledge as relations between entities.

Provenance policy:

* relations must cite Paper pages, except the taxonomy predicates ``is_a`` and ``part_of``,
  where citations are optional and an uncited relation is reported as ``unsourced``;
* entities and aliases may cite the page where a name appears (optional, recommended).

Aliases are unique per kind among live entities after normalisation (NFKC, casefold,
whitespace, hyphens and underscores ignored). Merging moves aliases and relations to the
kept entity; retraction is a soft delete. Nothing is deleted: the history stays for audit.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata

from open_agent_world.plugin_api import NodeResourceContext, ResourceValidationError

from . import chem
from .common import (Tool, add_sources, citations_schema, cited, connect, forward, log, now, actor, register_store,
                     sources_for, trusted_sources, verify_citations)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    formula TEXT,
    reduced TEXT,
    chemsys TEXT,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active | merged | retracted
    merged_into INTEGER REFERENCES entities(id),
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    changed_at TEXT
);
CREATE INDEX IF NOT EXISTS entities_kind ON entities(status, kind);
CREATE INDEX IF NOT EXISTS entities_reduced ON entities(reduced);
CREATE INDEX IF NOT EXISTS entities_chemsys ON entities(chemsys);
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    entity INTEGER NOT NULL REFERENCES entities(id),
    alias TEXT NOT NULL,
    normalized TEXT NOT NULL,
    kind TEXT NOT NULL,                     -- the entity's kind: aliases are unique per kind
    is_name INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS aliases_unique ON aliases(normalized, kind) WHERE active = 1;
CREATE INDEX IF NOT EXISTS aliases_entity ON aliases(entity);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    subject INTEGER NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object INTEGER NOT NULL REFERENCES entities(id),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active | merged (collapsed into merged_into) | retracted
    merged_into INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    changed_at TEXT
);
CREATE INDEX IF NOT EXISTS relations_subject ON relations(subject, status);
CREATE INDEX IF NOT EXISTS relations_object ON relations(object, status);
CREATE INDEX IF NOT EXISTS relations_predicate ON relations(predicate, status);
"""

KINDS = {
    "material": "A substance or compound, ideally with a formula (LiFePO4, graphite, PEO)",
    "phase": "A specific structure or polymorph of a material (olivine LiFePO4, alpha-Fe2O3)",
    "property": "A measurable property (ionic conductivity, band gap, specific capacity)",
    "method": "A characterisation, testing or computational method (XRD, EIS, DFT)",
    "synthesis_route": "A way of making a material (solid-state reaction, sol-gel, hydrothermal)",
    "application": "A use (Li-ion cathode, photocatalysis)",
    "element": "A chemical element",
    "concept": "A mechanism, phenomenon or claim (one-dimensional Li diffusion, antisite defects)",
}
CHEMICAL = {"material", "phase"}
# predicate: (description, subject kinds, object kinds, symmetric); empty kinds mean any.
PREDICATES = {
    "is_a": ("Taxonomy: the subject is a kind or an instance of the object (olivine LiFePO4 is_a polyanion cathode)", (), (), False),
    "part_of": ("Taxonomy: the subject is a component or constituent of the object", (), (), False),
    "has_property": ("The subject shows or is characterised by the property", ("material", "phase"), ("property",), False),
    "measured_by": ("The property is measured or characterised by the method", ("property",), ("method",), False),
    "synthesized_by": ("The material is made by the synthesis route", ("material", "phase"), ("synthesis_route",), False),
    "used_for": ("The subject is used for the application", (), ("application",), False),
    "doped_with": ("The material is doped or substituted with the element or material", ("material", "phase"), ("element", "material"), False),
    "polymorph_of": ("The two phases share a composition but differ in structure (symmetric)", ("phase", "material"), ("phase", "material"), True),
    "derived_from": ("The subject is obtained from the object (precursor, parent structure, derived concept)", (), (), False),
    "contradicts": ("Papers disagree: the subject claim contradicts the object claim; cite both sides (symmetric)", (), (), True),
    "related_to": ("A weaker link when no specific predicate fits; say how in the note (symmetric)", (), (), True),
}
TAXONOMY = {"is_a", "part_of"}
SLUG = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_IGNORED = re.compile(r"[\s\-‐‑‒–—―−_]+")
MAX_ALIASES = 50
MAX_ID = 2**63 - 1  # SQLite INTEGER
FUZZY_CALLS, FUZZY_CHARS = 2000, 64


def normalize(text: str) -> str:
    """Alias key: NFKC (subscripts become digits), casefolded, whitespace, hyphens and underscores removed."""
    return _IGNORED.sub("", unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold()))


# ---- Argument checks --------------------------------------------------------------------------

def _fail(message: str):
    raise ResourceValidationError(message)


def _text(arguments: dict, key: str, limit: int, required: bool = False) -> str:
    value = arguments.get(key)
    if value is None or value == "":
        if required:
            _fail(f"{key} is required")
        return ""
    if not isinstance(value, str) or len(value.strip()) > limit:
        _fail(f"{key} must be text of at most {limit} characters")
    return value.strip()


def _int(arguments: dict, key: str, default: int, low: int, high: int) -> int:
    value = arguments.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail(f"{key} must be an integer between {low} and {high}")
    return value


def _id(value, field: str) -> int:
    # Models sometimes pass "12" or the record key "entity:12"; accept both.
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(?:entity:)?(\d+)\s*", value)
        value = int(match.group(1)) if match else None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ID:
        _fail(f"{field} must be an entity id (an integer from resolve_entity or find_entities)")
    return value


def _slug(value, field: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value.strip().lower().replace(" ", "_").replace("-", "_")):
        _fail(f"{field} must be a lowercase slug such as {'material' if field == 'kind' else 'has_property'} "
              "(letters, digits and underscores, 2-40 characters)")
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _predicate(value) -> str:
    predicate = _slug(value, "predicate")
    if predicate in KINDS:
        _fail(f"{predicate!r} is an entity kind, not a predicate. Predicates: {', '.join(PREDICATES)}")
    return predicate


def _aliases(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > MAX_ALIASES:
        _fail(f"aliases must be a list of up to {MAX_ALIASES} names")
    aliases = []
    for index, alias in enumerate(value):
        if not isinstance(alias, str) or not alias.strip() or len(alias.strip()) > 200 or not normalize(alias):
            _fail(f"aliases.{index} must be a name of 1 to 200 characters")
        aliases.append(alias.strip())
    return aliases


def _user(action):
    """A workspace (user) action: never trusts provenance from the caller."""
    def handler(context: NodeResourceContext, arguments: dict) -> dict:
        return action(context, {key: value for key, value in arguments.items() if not key.startswith("_")})
    return handler


def _db(context: NodeResourceContext):
    return connect(context, SCHEMA)  # commits or rolls back, then closes, in ``with``


# ---- Reading ----------------------------------------------------------------------------------

def _row(connection: sqlite3.Connection, entity: int):
    return connection.execute("SELECT * FROM entities WHERE id = ?", (entity,)).fetchone()


def _live(connection: sqlite3.Connection, value, field: str) -> tuple[sqlite3.Row, int | None]:
    """The active entity for an id, following merges; returns (row, id it was redirected from)."""
    requested = _id(value, field)
    row = _row(connection, requested)
    # merge keeps every merged_into pointing at an active entity (one hop); allow a few for safety.
    for _ in range(3):
        if row is None or row["status"] != "merged":
            break
        row = _row(connection, row["merged_into"])
    if row is None:
        _fail(f"{field}: there is no entity #{requested}. Find ids with resolve_entity or find_entities")
    if row["status"] == "retracted":
        _fail(f"{field}: entity #{row['id']} {row['name']!r} was retracted ({row['reason']}). Use another entity or add_entity")
    if row["status"] != "active":
        _fail(f"{field}: entity #{requested} was merged but its replacement #{row['id']} is not active. "
              "Look the name up again with resolve_entity")
    return row, (requested if row["id"] != requested else None)


def _alias_owner(connection: sqlite3.Connection, normalized: str, kind: str):
    return connection.execute(
        "SELECT e.id, e.name, e.kind, a.alias FROM aliases a JOIN entities e ON e.id = a.entity"
        " WHERE a.normalized = ? AND a.kind = ? AND a.active = 1", (normalized, kind)).fetchone()


def _summaries(connection: sqlite3.Connection, ids, *, aliases: int = 10) -> dict[int, dict]:
    ids = list(dict.fromkeys(ids))
    found: dict[int, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in connection.execute(f"SELECT * FROM entities WHERE id IN ({marks})", chunk):
            item = {"id": row["id"], "kind": row["kind"], "name": row["name"], "formula": row["formula"],
                    "reduced": row["reduced"], "chemsys": row["chemsys"], "status": row["status"],
                    "description": row["description"][:300], "aliases": [], "alias_count": 0}
            if row["status"] != "active":
                item.update(merged_into=row["merged_into"], reason=row["reason"])
            found[row["id"]] = item
        for row in connection.execute(
                f"SELECT entity, alias FROM aliases WHERE entity IN ({marks}) AND is_name = 0"
                f" AND (active = 1 OR entity IN (SELECT id FROM entities WHERE status = 'retracted')) ORDER BY id", chunk):
            item = found[row["entity"]]
            item["alias_count"] += 1
            if len(item["aliases"]) < aliases:
                item["aliases"].append(row["alias"])
    return found


def _brief(entity: dict | None, entity_id: int) -> dict:
    return {"id": entity_id, "name": entity["name"], "kind": entity["kind"]} if entity else {"id": entity_id}


def _relations(connection: sqlite3.Connection, rows) -> list[dict]:
    rows = list(rows)
    names = _summaries(connection, [r["subject"] for r in rows] + [r["object"] for r in rows], aliases=0)
    sources = sources_for(connection, [f"relation:{r['id']}" for r in rows])
    result = []
    for row in rows:
        cited = sources[f"relation:{row['id']}"]
        item = {"id": row["id"], "record": f"relation:{row['id']}", "subject": _brief(names.get(row["subject"]), row["subject"]),
                "predicate": row["predicate"], "object": _brief(names.get(row["object"]), row["object"]),
                "note": row["note"], "status": row["status"], "sources": cited, "unsourced": not cited,
                "created_by": row["created_by"], "created_at": row["created_at"]}
        if row["predicate"] not in PREDICATES:
            item["custom_predicate"] = True
        if row["status"] != "active":
            item.update(reason=row["reason"], merged_into=row["merged_into"])
        result.append(item)
    return result


def _like(text: str) -> str:
    return re.sub(r"([%_\\])", r"\\\1", text)


def resolve(context: NodeResourceContext, arguments: dict) -> dict:
    text = _text(arguments, "text", 300, required=True)
    kind = _slug(arguments["kind"], "kind") if arguments.get("kind") else None
    limit = _int(arguments, "limit", 10, 1, 50)
    query = normalize(text)
    if not query:
        _fail("text needs at least one letter or digit")
    kind_filter, kind_values = (" AND a.kind = ?", [kind]) if kind else ("", [])
    candidates: dict[int, dict] = {}

    def hit(entity: int, score: float, why: dict):
        entry = candidates.setdefault(entity, {"score": 0.0, "matched": []})
        entry["score"] = max(entry["score"], score)
        if len(entry["matched"]) < 4 and why not in entry["matched"]:
            entry["matched"].append(why)

    with _db(context) as connection:
        alias_rows = lambda where, values, limit_=200: connection.execute(
            f"SELECT a.entity, a.alias, a.normalized, a.is_name FROM aliases a WHERE a.active = 1{kind_filter} AND {where}"
            f" ORDER BY a.id LIMIT {limit_}", [*kind_values, *values])
        for row in alias_rows("a.normalized = ?", [query]):
            hit(row["entity"], 1.0, {"by": "name" if row["is_name"] else "alias", "text": row["alias"]})
        keys = chem.try_keys(text)
        if keys:
            entity_kind = " AND kind = ?" if kind else ""
            for row in connection.execute(f"SELECT id, reduced FROM entities WHERE status = 'active' AND reduced = ?{entity_kind}"
                                          " LIMIT 200", [keys["reduced"], *kind_values]):
                hit(row["id"], 0.95, {"by": "formula", "reduced": row["reduced"]})
            for row in connection.execute(f"SELECT id, chemsys FROM entities WHERE status = 'active' AND chemsys = ?"
                                          f" AND reduced != ?{entity_kind} LIMIT 200", [keys["chemsys"], keys["reduced"], *kind_values]):
                hit(row["id"], 0.5, {"by": "chemsys", "chemsys": row["chemsys"]})
        pattern = _like(query)
        for row in alias_rows("a.normalized LIKE ? ESCAPE '\\' AND a.normalized != ?", [pattern + "%", query]):
            hit(row["entity"], 0.7, {"by": "prefix", "text": row["alias"]})
        if len(query) >= 3:
            for row in alias_rows("a.normalized LIKE ? ESCAPE '\\' AND a.normalized NOT LIKE ? ESCAPE '\\'",
                                  [f"%{pattern}%", pattern + "%"]):
                hit(row["entity"], 0.5, {"by": "contains", "text": row["alias"]})
        if len(candidates) < limit and len(query) >= 3:
            # Typos and spelling variants. This runs under the node lock, so the scan is bounded: similar
            # length and a shared trigram first, then at most FUZZY_CALLS ratio() calls on 64-char prefixes.
            short = query[:FUZZY_CHARS]
            trigrams = {short[i:i + 3] for i in range(len(short) - 2)}
            matcher, calls = difflib.SequenceMatcher(autojunk=False), 0
            matcher.set_seq2(short)
            low, high = int(len(short) * 0.6), int(len(short) * 1.4) + 1
            for row in alias_rows("min(length(a.normalized), ?) BETWEEN ? AND ?", [FUZZY_CHARS, low, high], 20000):
                key = row["normalized"][:FUZZY_CHARS]
                if not any(trigram in key for trigram in trigrams):
                    continue
                matcher.set_seq1(key)
                if matcher.real_quick_ratio() < 0.75 or matcher.quick_ratio() < 0.75:
                    continue
                calls += 1
                if (ratio := matcher.ratio()) >= 0.75:
                    hit(row["entity"], round(0.6 * ratio, 3), {"by": "fuzzy", "text": row["alias"], "similarity": round(ratio, 2)})
                if calls >= FUZZY_CALLS:
                    break
        ranked = sorted(candidates.items(), key=lambda item: (-item[1]["score"], item[0]))[:limit]
        entities = _summaries(connection, [entity for entity, _ in ranked])
    result = {"query": text, "candidates": [{**entities[entity], **match} for entity, match in ranked if entity in entities]}
    if not result["candidates"]:
        result["hint"] = "No match. Try a shorter or different name, find_entities with filters, or add_entity if it is new."
    return result


def find(context: NodeResourceContext, arguments: dict) -> dict:
    limit, offset = _int(arguments, "limit", 50, 1, 200), _int(arguments, "offset", 0, 0, 1_000_000)
    where, values = ["status = 'active'" if not arguments.get("include_retracted") else "status != 'merged'"], []
    if arguments.get("kind"):
        where.append("kind = ?")
        values.append(_slug(arguments["kind"], "kind"))
    if text := _text(arguments, "text", 300):
        where.append("(id IN (SELECT entity FROM aliases WHERE normalized LIKE ? ESCAPE '\\') OR description LIKE ? ESCAPE '\\')")
        values += [f"%{_like(normalize(text))}%", f"%{_like(text)}%"]
    try:
        if formula := _text(arguments, "formula", 200):
            where.append("reduced = ?")
            values.append(chem.keys(formula)["reduced"])
        if chemsys := arguments.get("chemsys"):
            where.append("chemsys = ?")
            values.append(chem.chemsys_of(chemsys))
        elements = arguments.get("elements") or []
        if isinstance(elements, str):
            elements = re.split(r"[-,\s]+", elements.strip())
        if not isinstance(elements, list) or len(elements) > 20:
            _fail("elements must be a list of up to 20 element symbols")
        for element in chem.chemsys_of(elements).split("-") if elements else []:
            where.append("('-' || chemsys || '-') LIKE ?")
            values.append(f"%-{element}-%")
    except chem.FormulaError as error:
        _fail(f"{error}. Give a formula such as LiFePO4 and element symbols such as Fe, Li")
    clause = " AND ".join(where)
    with _db(context) as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM entities WHERE {clause}", values).fetchone()[0]
        ids = [row[0] for row in connection.execute(
            f"SELECT id FROM entities WHERE {clause} ORDER BY kind, name COLLATE NOCASE, id LIMIT ? OFFSET ?", [*values, limit, offset])]
        found = _summaries(connection, ids)
        counts = _relation_counts(connection, ids)
    return {"total": total, "offset": offset, "entities": [{**found[i], "relations": counts.get(i, 0)} for i in ids],
            "more": offset + len(ids) < total}


def _relation_counts(connection: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    counts: dict[int, int] = {}
    for column in ("subject", "object"):
        for row in connection.execute(f"SELECT {column} AS e, COUNT(*) AS n FROM relations WHERE status = 'active'"
                                      f" AND {column} IN ({marks}) GROUP BY {column}", ids):
            counts[row["e"]] = counts.get(row["e"], 0) + row["n"]
    return counts


def _detail(connection: sqlite3.Connection, entity: int) -> dict:
    """An entity with every alias and its provenance, as Agents and the workspace see it."""
    item = _summaries(connection, [entity], aliases=0)[entity]
    rows = connection.execute("SELECT id, alias, is_name, active, created_by FROM aliases WHERE entity = ? ORDER BY is_name DESC, id"
                              " LIMIT 200", (entity,)).fetchall()
    sources = sources_for(connection, [f"entity:{entity}", *(f"alias:{row['id']}" for row in rows)])
    item["aliases"] = [{"alias": row["alias"], "record": f"alias:{row['id']}", "sources": sources[f"alias:{row['id']}"],
                        "created_by": row["created_by"]} for row in rows if not row["is_name"] and (row["active"] or item["status"] == "retracted")]
    item["description"] = _row(connection, entity)["description"]
    item["record"], item["sources"] = f"entity:{entity}", sources[f"entity:{entity}"]
    item["merged_from"] = [{"id": row["id"], "name": row["name"], "record": f"entity:{row['id']}", "reason": row["reason"],
                            "sources": sources_for(connection, [f"entity:{row['id']}"])[f"entity:{row['id']}"]}
                           for row in connection.execute("SELECT id, name, reason FROM entities WHERE merged_into = ? ORDER BY id LIMIT 50", (entity,))]
    return item


def neighbors(context: NodeResourceContext, arguments: dict) -> dict:
    depth = _int(arguments, "depth", 1, 1, 2)
    limit = _int(arguments, "limit", 50, 1, 200)
    direction = arguments.get("direction") or "both"
    if direction not in {"out", "in", "both"}:
        _fail("direction must be out (entity is the subject), in (entity is the object) or both")
    predicates = arguments.get("predicates") or []
    if not isinstance(predicates, list) or len(predicates) > 20:
        _fail("predicates must be a list of up to 20 predicate names")
    predicates = [_predicate(p) for p in predicates]
    with _db(context) as connection:
        center, redirected = _live(connection, arguments.get("entity"), "entity")
        seen: dict[int, int] = {}  # relation id -> hop
        rows, frontier, visited, truncated = [], {center["id"]}, {center["id"]}, False
        for hop in range(1, depth + 1):
            marks = ",".join("?" * len(frontier))
            ends = {"out": [f"subject IN ({marks})"], "in": [f"object IN ({marks})"],
                    "both": [f"subject IN ({marks})", f"object IN ({marks})"]}[direction]
            where = f"status = 'active' AND ({' OR '.join(ends)})"
            values = [*frontier] * len(ends)
            if predicates:
                where += f" AND predicate IN ({','.join('?' * len(predicates))})"
                values += predicates
            found = connection.execute(f"SELECT * FROM relations WHERE {where} ORDER BY id LIMIT ?",
                                       [*values, limit - len(rows) + 1 + len(seen)]).fetchall()
            following = set()
            for row in found:
                if row["id"] in seen:
                    continue
                if len(rows) >= limit:
                    truncated = True
                    break
                seen[row["id"]] = hop
                rows.append(row)
                following |= {row["subject"], row["object"]} - visited
            if truncated or not following:
                break
            visited |= following
            frontier = following
        relations = _relations(connection, rows)
        result = {"entity": _detail(connection, center["id"]),
                  "relations": [{**relation, "hop": seen[relation["id"]]} for relation in relations],
                  "truncated": truncated}
    if redirected:
        result["redirected_from"] = redirected
    if truncated:
        result["hint"] = f"Only {limit} relations are returned; filter by predicates or direction, or lower depth."
    return result


def vocabulary(context: NodeResourceContext, arguments: dict) -> dict:
    with _db(context) as connection:
        kinds = {row["kind"]: row["n"] for row in connection.execute(
            "SELECT kind, COUNT(*) AS n FROM entities WHERE status = 'active' GROUP BY kind ORDER BY n DESC LIMIT 200")}
        used = {row["predicate"]: {"relations": row["n"], "unsourced": row["unsourced"]} for row in connection.execute(
            "SELECT predicate, COUNT(*) AS n, SUM(NOT EXISTS (SELECT 1 FROM sources s WHERE s.record = 'relation:' || r.id))"
            " AS unsourced FROM relations r WHERE status = 'active' GROUP BY predicate ORDER BY n DESC LIMIT 200")}
        totals = {"entities": sum(kinds.values()), "relations": sum(item["relations"] for item in used.values()),
                  "retracted_entities": connection.execute("SELECT COUNT(*) FROM entities WHERE status = 'retracted'").fetchone()[0],
                  "merged_entities": connection.execute("SELECT COUNT(*) FROM entities WHERE status = 'merged'").fetchone()[0]}
    return {
        "totals": totals,
        "kinds": [{"kind": kind, "description": KINDS.get(kind, "custom kind"), "entities": kinds.get(kind, 0)}
                  for kind in dict.fromkeys([*KINDS, *kinds])],
        "predicates": [{"predicate": name, "description": description, "subject_kinds": list(subjects), "object_kinds": list(objects),
                        "citations": "optional" if name in TAXONOMY else "required", "symmetric": symmetric,
                        **used.get(name, {"relations": 0, "unsourced": 0})}
                       for name, (description, subjects, objects, symmetric) in PREDICATES.items()],
        "custom_predicates": [{"predicate": name, **counts} for name, counts in used.items() if name not in PREDICATES],
    }


# ---- Writing ----------------------------------------------------------------------------------

def _insert_aliases(connection, context, entity: int, kind: str, aliases: list[str], *, is_name=False) -> list[int]:
    ids = []
    for alias in aliases:
        cursor = connection.execute(
            "INSERT INTO aliases (entity, alias, normalized, kind, is_name, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity, alias, normalize(alias), kind, int(is_name), now(), actor(context)))
        ids.append(cursor.lastrowid)
    return ids


def _taken(connection, text: str, kind: str, entity: int | None = None):
    owner = _alias_owner(connection, normalize(text), kind)
    if owner and owner["id"] != entity:
        _fail(f"{text!r} already names entity #{owner['id']} {owner['name']!r} ({kind}, as {owner['alias']!r}). "
              f"Use that entity (resolve_entity finds it), extend it with add_aliases, or merge_entities if both "
              f"entries describe the same thing")
    return owner


def add_entity(context: NodeResourceContext, arguments: dict) -> dict:
    kind = _slug(arguments.get("kind"), "kind")
    name = _text(arguments, "name", 200, required=True)
    formula = _text(arguments, "formula", 200) or None
    description = _text(arguments, "description", 2000)
    if not normalize(name):
        _fail("name needs at least one letter or digit")
    names: dict[str, str] = {}
    for text in [name, *_aliases(arguments.get("aliases"))]:
        names.setdefault(normalize(text), text)
    keys = chem.try_keys(formula)
    warnings = []
    if formula and not keys:
        warnings.append(f"formula {formula!r} could not be parsed, so this entity has no formula keys and will not match "
                        "formula searches. Use a plain formula such as LiFePO4 or Li0.5CoO2 if one applies")
    if kind in CHEMICAL and not formula:
        warnings.append("No formula given: pass formula (e.g. LiFePO4) so formula and chemical-system searches find it")
    if kind not in KINDS:
        warnings.append(f"{kind!r} is not a suggested kind ({', '.join(KINDS)}); reuse existing kinds where they fit")
    with _db(context) as connection:
        for text in names.values():
            _taken(connection, text, kind)
        if keys and not arguments.get("allow_same_formula"):
            same = connection.execute("SELECT id, name FROM entities WHERE status = 'active' AND kind = ? AND reduced = ?",
                                      (kind, keys["reduced"])).fetchone()
            if same:
                _fail(f"Entity #{same['id']} {same['name']!r} ({kind}) already has formula {keys['reduced']}. Add your names to it "
                      f"with add_aliases, or, if this is really a different {kind} (e.g. another polymorph), "
                      "call again with allow_same_formula: true")
        cursor = connection.execute(
            "INSERT INTO entities (kind, name, formula, reduced, chemsys, description, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (kind, name, formula, keys and keys["reduced"], keys and keys["chemsys"],
                                                 description, now(), actor(context)))
        entity = cursor.lastrowid
        _insert_aliases(connection, context, entity, kind, [name], is_name=True)
        _insert_aliases(connection, context, entity, kind, list(names.values())[1:])
        add_sources(connection, context, f"entity:{entity}", trusted_sources(arguments))
        log(connection, context, "add_entity", [f"entity:{entity}"], f"{kind} {name}")
        result = {"entity": _detail(connection, entity)}
    if warnings:
        result["warnings"] = warnings
    return result


def add_aliases(context: NodeResourceContext, arguments: dict) -> dict:
    aliases = _aliases(arguments.get("aliases"))
    if not aliases:
        _fail("aliases must list at least one name")
    sources = trusted_sources(arguments)
    with _db(context) as connection:
        entity, redirected = _live(connection, arguments.get("entity"), "entity")
        added, already, fresh = [], [], {}
        for alias in aliases:
            if _taken(connection, alias, entity["kind"], entity["id"]) or normalize(alias) in fresh:
                already.append(alias)
            else:
                fresh[normalize(alias)] = alias
        ids = _insert_aliases(connection, context, entity["id"], entity["kind"], list(fresh.values()))
        for alias_id in ids:
            add_sources(connection, context, f"alias:{alias_id}", sources)
        added = list(fresh.values())
        if ids:
            log(connection, context, "add_aliases", [f"entity:{entity['id']}", *(f"alias:{i}" for i in ids)], ", ".join(added))
        result = {"entity": _summaries(connection, [entity["id"]], aliases=MAX_ALIASES)[entity["id"]], "added": added,
                  "already_known": already}
    if redirected:
        result["redirected_from"] = redirected
    return result


def _hints(predicate: str, subject, object_) -> list[str]:
    if predicate not in PREDICATES:
        return [f"{predicate!r} is a custom predicate; prefer the vocabulary ({', '.join(PREDICATES)}) when one fits"]
    _, subjects, objects, _ = PREDICATES[predicate]
    hints = []
    if subjects and subject["kind"] not in subjects:
        hints.append(f"{predicate} usually has a {' or '.join(subjects)} subject, not {subject['kind']}")
    if objects and object_["kind"] not in objects:
        hints.append(f"{predicate} usually has a {' or '.join(objects)} object, not {object_['kind']}")
    return hints


def _duplicate(connection, subject: int, predicate: str, object_: int, *, other_than: int | None = None):
    symmetric = PREDICATES.get(predicate, ("", (), (), False))[3]
    pairs = [(subject, object_), (object_, subject)] if symmetric else [(subject, object_)]
    for s, o in pairs:
        row = connection.execute("SELECT id FROM relations WHERE status = 'active' AND subject = ? AND predicate = ? AND object = ?"
                                 " AND id != ? ORDER BY id LIMIT 1", (s, predicate, o, other_than or 0)).fetchone()
        if row:
            return row["id"]
    return None


def relate(context: NodeResourceContext, arguments: dict) -> dict:
    predicate = _predicate(arguments.get("predicate"))
    note = _text(arguments, "note", 1000)
    sources = trusted_sources(arguments)
    if not sources and predicate not in TAXONOMY and context.actor_id:
        _fail(f"{predicate} relations need citations")
    with _db(context) as connection:
        subject, _ = _live(connection, arguments.get("subject"), "subject")
        object_, _ = _live(connection, arguments.get("object"), "object")
        if subject["id"] == object_["id"]:
            _fail(f"subject and object are the same entity #{subject['id']} {subject['name']!r}; a relation needs two entities")
        if existing := _duplicate(connection, subject["id"], predicate, object_["id"]):
            _fail(f"relation #{existing} already states {subject['name']!r} {predicate} {object_['name']!r}. "
                  "Add evidence to it with cite_ontology_record; retract it first if it should be replaced")
        cursor = connection.execute(
            "INSERT INTO relations (subject, predicate, object, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (subject["id"], predicate, object_["id"], note, now(), actor(context)))
        record = f"relation:{cursor.lastrowid}"
        add_sources(connection, context, record, sources)
        log(connection, context, "relate", [record, f"entity:{subject['id']}", f"entity:{object_['id']}"],
            f"{subject['name']} {predicate} {object_['name']}")
        relation = _relations(connection, [connection.execute("SELECT * FROM relations WHERE id = ?", (cursor.lastrowid,)).fetchone()])[0]
    result = {"relation": relation}
    if hints := _hints(predicate, subject, object_):
        result["warnings"] = hints
    return result


def merge(context: NodeResourceContext, arguments: dict) -> dict:
    merging = arguments.get("merge")
    merging = merging if isinstance(merging, list) else [merging]
    if not 1 <= len(merging) <= 20:
        _fail("merge must be one entity id or a list of up to 20")
    reason = _text(arguments, "reason", 500)
    with _db(context) as connection:
        keep, _ = _live(connection, arguments.get("keep"), "keep")
        gone, records = [], [f"entity:{keep['id']}"]
        report = {"aliases_moved": 0, "relations_moved": 0, "collapsed": [], "self_relations_retracted": []}
        for index, value in enumerate(merging):
            row, _ = _live(connection, value, f"merge.{index}")
            if row["id"] == keep["id"] or row["id"] in gone:
                _fail(f"merge.{index}: entity #{row['id']} is already {'the kept entity' if row['id'] == keep['id'] else 'listed'}")
            if row["kind"] != keep["kind"]:
                _fail(f"merge.{index}: entity #{row['id']} is a {row['kind']} and #{keep['id']} a {keep['kind']}; only entities "
                      "of one kind merge. Relate them instead (e.g. is_a or polymorph_of)")
            gone.append(row["id"])
            report["aliases_moved"] += connection.execute("UPDATE aliases SET entity = ?, is_name = 0 WHERE entity = ? AND active = 1",
                                                          (keep["id"], row["id"])).rowcount
            moved = [r["id"] for r in connection.execute(
                "SELECT id FROM relations WHERE status = 'active' AND (subject = ? OR object = ?)", (row["id"], row["id"]))]
            connection.execute("UPDATE relations SET subject = ? WHERE status = 'active' AND subject = ?", (keep["id"], row["id"]))
            connection.execute("UPDATE relations SET object = ? WHERE status = 'active' AND object = ?", (keep["id"], row["id"]))
            report["relations_moved"] += len(moved)
            for relation_id in moved:
                relation = connection.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
                if relation["subject"] == relation["object"]:
                    connection.execute("UPDATE relations SET status = 'retracted', reason = ?, changed_at = ? WHERE id = ?",
                                       (f"became a self-relation when #{row['id']} was merged into #{keep['id']}", now(), relation_id))
                    report["self_relations_retracted"].append(relation_id)
                elif survivor := _duplicate(connection, relation["subject"], relation["predicate"], relation["object"], other_than=relation_id):
                    # One statement survives and carries every citation; the duplicate stays for audit.
                    connection.execute("UPDATE relations SET status = 'merged', merged_into = ?, reason = ?, changed_at = ? WHERE id = ?",
                                       (survivor, f"duplicate after merging #{row['id']} into #{keep['id']}", now(), relation_id))
                    connection.execute("UPDATE sources SET record = ? WHERE record = ?", (f"relation:{survivor}", f"relation:{relation_id}"))
                    if relation["note"]:
                        connection.execute("UPDATE relations SET note = CASE WHEN note = '' THEN ? ELSE substr(note || ' | ' || ?, 1, 1000) END"
                                           " WHERE id = ?", (relation["note"], relation["note"], survivor))
                    report["collapsed"].append({"relation": relation_id, "into": survivor})
                records.append(f"relation:{relation_id}")
            connection.execute("UPDATE entities SET status = 'merged', merged_into = ?, reason = ?, changed_at = ? WHERE id = ?",
                               (keep["id"], reason or f"merged into #{keep['id']}", now(), row["id"]))
            # Path compression: entities merged earlier into this one now point at keep, so chains stay one hop.
            connection.execute("UPDATE entities SET merged_into = ? WHERE merged_into = ? AND status = 'merged'", (keep["id"], row["id"]))
            # The kept entry gains a formula or description it lacked.
            connection.execute("UPDATE entities SET formula = coalesce(formula, ?), reduced = coalesce(reduced, ?),"
                               " chemsys = coalesce(chemsys, ?), description = CASE WHEN description = '' THEN ? ELSE description END,"
                               " changed_at = ? WHERE id = ?",
                               (row["formula"], row["reduced"], row["chemsys"], row["description"], now(), keep["id"]))
            records.append(f"entity:{row['id']}")
        log(connection, context, "merge", records, reason or f"merged {', '.join(f'#{i}' for i in gone)} into #{keep['id']}")
        report["entity"] = _summaries(connection, [keep["id"]], aliases=MAX_ALIASES)[keep["id"]]
    return {**report, "merged": gone}


def retract(context: NodeResourceContext, arguments: dict) -> dict:
    record = _text(arguments, "record", 40, required=True)
    reason = _text(arguments, "reason", 500, required=True)
    if len(reason) < 3:
        _fail("reason must say why the record is wrong (at least 3 characters)")
    match = re.fullmatch(r"(entity|relation|alias):(\d+)", record)
    if not match or int(match.group(2)) > MAX_ID:
        _fail("record must be a record key such as entity:12, relation:7 or alias:30")
    table, key = match.group(1), int(match.group(2))
    retracted = [record]
    with _db(context) as connection:
        if table == "alias":
            row = connection.execute("SELECT * FROM aliases WHERE id = ?", (key,)).fetchone()
            if row is None or not row["active"]:
                _fail(f"{record} does not exist or is already retracted")
            if row["is_name"]:
                _fail(f"{record} is the name of entity #{row['entity']}; retract entity:{row['entity']} instead")
            connection.execute("UPDATE aliases SET active = 0 WHERE id = ?", (key,))
        elif table == "relation":
            row = connection.execute("SELECT status FROM relations WHERE id = ?", (key,)).fetchone()
            if row is None or row["status"] != "active":
                _fail(f"{record} does not exist or is already {row['status'] if row else 'gone'}")
            connection.execute("UPDATE relations SET status = 'retracted', reason = ?, changed_at = ? WHERE id = ?", (reason, now(), key))
        else:
            row = _row(connection, key)
            if row is None or row["status"] != "active":
                where = f"; it was merged into entity:{row['merged_into']}" if row and row["status"] == "merged" else ""
                _fail(f"{record} does not exist or is not active{where}")
            connection.execute("UPDATE entities SET status = 'retracted', reason = ?, changed_at = ? WHERE id = ?", (reason, now(), key))
            connection.execute("UPDATE aliases SET active = 0 WHERE entity = ?", (key,))
            relations = [r["id"] for r in connection.execute(
                "SELECT id FROM relations WHERE status = 'active' AND (subject = ? OR object = ?)", (key, key))]
            connection.executemany("UPDATE relations SET status = 'retracted', reason = ?, changed_at = ? WHERE id = ?",
                                   [(f"entity:{key} retracted: {reason}"[:500], now(), r) for r in relations])
            retracted += [f"relation:{r}" for r in relations]
        log(connection, context, "retract", retracted, reason)
    return {"retracted": retracted, "reason": reason}


def cite(context: NodeResourceContext, arguments: dict) -> dict:
    record = _text(arguments, "record", 40, required=True)
    match = re.fullmatch(r"(entity|relation|alias):(\d+)", record)
    if not match or int(match.group(2)) > MAX_ID:
        _fail("record must be a record key such as entity:12, relation:7 or alias:30")
    table, key = match.group(1), int(match.group(2))
    sources = trusted_sources(arguments)
    if not sources:
        _fail("citations are required")
    with _db(context) as connection:
        if table == "alias":
            row = connection.execute("SELECT active FROM aliases WHERE id = ?", (key,)).fetchone()
            live = bool(row and row["active"])
        else:
            row = connection.execute(f"SELECT status FROM {'entities' if table == 'entity' else 'relations'} WHERE id = ?", (key,)).fetchone()
            live = bool(row and row["status"] == "active")
        if not live:
            _fail(f"{record} does not exist or is not active; cite a live record (find it with entity_neighbors)")
        add_sources(connection, context, record, sources)
        log(connection, context, "cite", [record], _text(arguments, "note", 500))
        cited_now = sources_for(connection, [record])[record]
    return {"record": record, "sources": cited_now}


# ---- Workspace (user) -------------------------------------------------------------------------

def ui_search(context: NodeResourceContext, arguments: dict) -> dict:
    if (arguments.get("text") or "").strip():
        found = resolve(context, {"text": arguments["text"], "kind": arguments.get("kind") or None, "limit": 50})
        return {"entities": found["candidates"], "total": len(found["candidates"]), "more": False}
    return find(context, {"kind": arguments.get("kind") or None, "limit": 100, "offset": arguments.get("offset") or 0,
                          "include_retracted": bool(arguments.get("include_retracted"))})


def ui_entity(context: NodeResourceContext, arguments: dict) -> dict:
    with _db(context) as connection:
        entity = _id(arguments.get("entity"), "entity")
        if _row(connection, entity) is None:
            _fail(f"There is no entity #{entity}")
        status = "status != 'merged'" if arguments.get("include_retracted") else "status = 'active'"
        detail = _detail(connection, entity)
        for side, column in (("outgoing", "subject"), ("incoming", "object")):
            detail[side] = _relations(connection, connection.execute(
                f"SELECT * FROM relations WHERE {column} = ? AND {status} ORDER BY predicate, id LIMIT 200", (entity,)))
    return detail


def ui_add_alias(context: NodeResourceContext, arguments: dict) -> dict:
    return add_aliases(context, {"entity": arguments.get("entity"), "aliases": [arguments.get("alias")]})


# ---- Tools ------------------------------------------------------------------------------------

ENTITY_ID = {"type": "integer", "minimum": 1, "description": "Entity id (from resolve_entity or find_entities)"}
KIND = {"type": "string", "maxLength": 40, "description": f"Entity kind: one of {', '.join(KINDS)}, or another lowercase slug"}
PREDICATE = {"type": "string", "maxLength": 40, "description": "Relation predicate: is_a or part_of (taxonomy), or "
             + ", ".join(name for name in PREDICATES if name not in TAXONOMY) + ". list_ontology_vocabulary explains each. "
             "Another lowercase slug is accepted when none fits."}
STRICT = {"type": "object", "additionalProperties": False}


async def _relate(context, capability, arguments):
    arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
    predicate = _predicate(arguments.get("predicate"))
    citations = arguments.pop("citations", None)
    if not citations and predicate not in TAXONOMY:
        _fail(f"{predicate} relations must cite Paper pages: pass citations with paper, page and a verbatim quote from "
              "read_paper. Only is_a and part_of may be written without citations")
    sources = await verify_citations(context, capability, citations, required=False)
    return await context.node_resource_action(capability, "relate", {**arguments, "_sources": sources})


READ = [
    Tool("resolve", "resolve_entity",
         "Resolve a name as written in a paper (\"LFP\", \"lithium iron phosphate\", \"LiFePO₄\") to ontology entities. "
         "Returns ranked candidates, each with why it matched: exact name or alias (case, spacing, hyphens and "
         "subscripts ignored), same reduced formula, same chemical system, prefix, substring or a close spelling. "
         "Call this before add_entity or relate so you reuse existing entities instead of creating duplicates.",
         {**STRICT, "required": ["text"], "properties": {
             "text": {"type": "string", "minLength": 1, "maxLength": 300, "description": "Name, abbreviation or formula"},
             "kind": KIND, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
         forward("resolve"), {"resolve": resolve}),
    Tool("find", "find_entities",
         "List ontology entities by filters: kind, text (in names, aliases or descriptions), exact formula (matched "
         "by reduced composition), chemsys (e.g. Fe-Li-O-P) or elements that must all be present. Paged with "
         "limit/offset; each entity shows its aliases and number of relations.",
         {**STRICT, "properties": {
             "kind": KIND, "text": {"type": "string", "maxLength": 300},
             "formula": {"type": "string", "maxLength": 200},
             "chemsys": {"type": "string", "maxLength": 200, "description": "Exact element set, e.g. Fe-Li-O-P"},
             "elements": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": "Elements that must all be present"},
             "include_retracted": {"type": "boolean"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "offset": {"type": "integer", "minimum": 0}}},
         forward("find"), {"find": find}),
    Tool("neighbors", "entity_neighbors",
         "Show one entity in full (aliases, formula keys, provenance) and its relations, with each relation's "
         "citations. Uncited taxonomy relations are flagged unsourced. direction: out (entity is the subject), in, "
         "or both; depth 2 also follows the neighbours' relations. At most `limit` relations are returned.",
         {**STRICT, "required": ["entity"], "properties": {
             "entity": ENTITY_ID,
             "predicates": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": "Only these predicates"},
             "direction": {"type": "string", "enum": ["out", "in", "both"]},
             "depth": {"type": "integer", "minimum": 1, "maximum": 2},
             "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
         forward("neighbors"), {"neighbors": neighbors}),
    Tool("vocabulary", "list_ontology_vocabulary",
         "List the ontology's vocabulary: entity kinds (suggested and in use, with counts) and relation predicates "
         "with their meaning, expected subject/object kinds, whether citations are required, and how often each is used.",
         {**STRICT, "properties": {}}, forward("vocabulary"), {"vocabulary": vocabulary}),
]

CURATE = [
    Tool("add_entity", "add_entity",
         "Add an entity (a material, phase, property, method, synthesis route, application, element or concept) with "
         "its preferred name, the other names it goes by (aliases) and, for materials, a formula. Call resolve_entity "
         "first: a name or alias already used by an entity of the same kind is refused, and so is a second entity of "
         "the kind with the same reduced formula unless allow_same_formula is true (e.g. a distinct polymorph). "
         "Citations are optional; cite the page where the names appear when you can.",
         {**STRICT, "required": ["kind", "name"], "properties": {
             "kind": KIND, "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Preferred name"},
             "aliases": {"type": "array", "maxItems": MAX_ALIASES, "items": {"type": "string", "maxLength": 200},
                         "description": "Abbreviations, spellings and formulas that name the same thing"},
             "formula": {"type": "string", "maxLength": 200,
                         "description": "Chemical formula, e.g. LiFePO4 (enables formula and chemical-system matching)"},
             "description": {"type": "string", "maxLength": 2000},
             "allow_same_formula": {"type": "boolean"},
             "citations": citations_schema(0)}},
         cited("add_entity", required=False), {"add_entity": add_entity}),
    Tool("add_aliases", "add_aliases",
         "Add names an entity goes by (abbreviations, spellings, formulas, trade names) so resolve_entity finds it. "
         "Names it already has are reported, not duplicated; a name used by another entity of the same kind is "
         "refused (merge_entities if they are the same thing). Citations are optional; cite the page that uses the name.",
         {**STRICT, "required": ["entity", "aliases"], "properties": {
             "entity": ENTITY_ID,
             "aliases": {"type": "array", "minItems": 1, "maxItems": MAX_ALIASES, "items": {"type": "string", "maxLength": 200}},
             "citations": citations_schema(0)}},
         cited("add_aliases", required=False), {"add_aliases": add_aliases}),
    Tool("relate", "relate",
         "State a typed relation between two entities: subject predicate object (e.g. LiFePO4 has_property ionic "
         "conductivity; ionic conductivity measured_by EIS; paper A's claim contradicts paper B's claim). Citations "
         "are required: each gives paper, page and a verbatim quote from that page, checked against the Paper with "
         "your own access. Only the taxonomy predicates is_a and part_of may be uncited; they are then shown as "
         "unsourced. Self-relations and relations already stated are refused. See list_ontology_vocabulary for predicates.",
         {**STRICT, "required": ["subject", "predicate", "object"], "properties": {
             "subject": ENTITY_ID, "predicate": PREDICATE, "object": ENTITY_ID,
             "note": {"type": "string", "maxLength": 1000, "description": "Qualifiers: conditions, scope, how papers disagree"},
             "citations": citations_schema(0)}},
         _relate, {"relate": relate}),
    Tool("merge", "merge_entities",
         "Merge duplicate entities of one kind into the one to keep. Their aliases and relations move to `keep`; "
         "relations that become identical collapse into one carrying all citations; the merged entities stay on "
         "record as merged (their old ids and names now resolve to `keep`). Everything is logged.",
         {**STRICT, "required": ["keep", "merge"], "properties": {
             "keep": ENTITY_ID,
             "merge": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "integer", "minimum": 1},
                       "description": "Entity ids to merge into keep"},
             "reason": {"type": "string", "maxLength": 500}}},
         forward("merge"), {"merge": merge}),
    Tool("retract", "retract_ontology_record",
         "Retract a wrong entity, relation or alias with a reason (soft delete: it stays on record for audit and is "
         "hidden from searches). Retracting an entity also retracts its relations and frees its names. Use this to "
         "revise: retract, then add the corrected record with fresh citations.",
         {**STRICT, "required": ["record", "reason"], "properties": {
             "record": {"type": "string", "maxLength": 40, "description": "Record key: entity:12, relation:7 or alias:30"},
             "reason": {"type": "string", "minLength": 3, "maxLength": 500}}},
         forward("retract"), {"retract": retract}),
    Tool("cite", "cite_ontology_record",
         "Add evidence to an existing entity, relation or alias: each citation gives paper, page and a verbatim quote, "
         "checked against the Paper with your own access. Use it when another paper supports a relation that is "
         "already stated, to source an uncited is_a/part_of relation, or to re-cite a record whose source went stale "
         "(after re-reading the page). Existing citations are kept.",
         {**STRICT, "required": ["record", "citations"], "properties": {
             "record": {"type": "string", "maxLength": 40, "description": "Record key: entity:12, relation:7 or alias:30"},
             "note": {"type": "string", "maxLength": 500, "description": "Why this evidence is added (logged)"},
             "citations": citations_schema(1)}},
         cited("cite", required=True), {"cite": cite}),
]


def register(registration):
    register_store(registration, card="ontology", label="Ontology",
                   description="Entities with aliases and typed relations that Agents curate from cited Papers",
                   icon="share-2", color="#5f8fb0", schema=SCHEMA, read_tools=READ, curate_tools=CURATE,
                   user_actions={"ui_search": _user(ui_search), "ui_entity": _user(ui_entity),
                                 "ui_vocabulary": _user(vocabulary), "ui_retract": _user(retract),
                                 "ui_add_alias": _user(ui_add_alias)})
