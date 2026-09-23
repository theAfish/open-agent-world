"""Fact table (knowledge.facts): material property records with verified sources.

One row says "material X has property P = value (unit) under conditions C, by method M".
Agents write rows with record_facts, citing Paper pages per fact; the user may enter rows
in the workspace without citations. Rows are never changed by a source changing: a
provenance check marks their citations stale and an Agent or the user revises explicitly.

Queries compare values in a dimension's base unit (facts_units), so 0.5 eV and 500 meV
match the same range; values in another dimension or an unknown unit are never compared
silently, the result says how many were left out.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager

from open_agent_world.plugin_api import NodeResourceContext, ResourceValidationError

from . import chem
from .common import (Tool, add_sources, citations_schema, cited, connect, forward, log, now, register_store,
                     sources_for, trusted_sources, verify_citations, actor)
from .facts_units import EXPECTED, finite, resolve

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    material TEXT NOT NULL,          -- as written, e.g. 'LiFePO4 (carbon coated)' or 'LFP'
    formula TEXT,                    -- the formula it was parsed from, when there is one
    reduced TEXT,                    -- chem.keys: NULL when the material is not a parseable formula
    chemsys TEXT,
    property TEXT NOT NULL,          -- snake_case key, e.g. band_gap
    value REAL,
    value_max REAL,                  -- upper bound of a range; NULL for a single value
    value_text TEXT,                 -- qualitative value when there is no number
    unit TEXT NOT NULL DEFAULT '',   -- as given
    unit_key TEXT NOT NULL DEFAULT '',  -- canonical spelling (facts_units.canonical)
    dimension TEXT,                  -- NULL when the unit is not in the normalisation table
    base_value REAL,
    base_value_max REAL,
    conditions TEXT NOT NULL DEFAULT '{}',
    method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active | retracted
    retract_reason TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS facts_reduced ON facts(reduced);
CREATE INDEX IF NOT EXISTS facts_chemsys ON facts(chemsys);
CREATE INDEX IF NOT EXISTS facts_property ON facts(property, dimension);
CREATE TABLE IF NOT EXISTS fact_elements (
    fact INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    element TEXT NOT NULL,
    PRIMARY KEY (fact, element)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS fact_elements_element ON fact_elements(element, fact);
"""

MAX_BATCH = 50
MAX_LIMIT = 100
# Fields whose change makes old citations stop supporting the record ('note' is a caveat, not a claim).
CONTENT = {"material", "formula", "property", "value", "value_max", "value_text", "unit", "conditions", "method"}
FIELDS = ("material", "formula", "property", "value", "value_max", "value_text", "unit", "conditions", "method", "note")
PROPERTY_ALIASES = {"bandgap": "band_gap", "band_gap_energy": "band_gap", "eg": "band_gap",
                    "a": "lattice_a", "b": "lattice_b", "c": "lattice_c", "lattice_parameter_a": "lattice_a",
                    "lattice_parameter_b": "lattice_b", "lattice_parameter_c": "lattice_c"}


def key(fact_id: int) -> str:
    return f"fact:{fact_id}"


@contextmanager
def database(context: NodeResourceContext):
    with closing(connect(context, SCHEMA)) as connection, connection:
        yield connection


# ---- Validation --------------------------------------------------------------------------------

def snake(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("'", "")
    return re.sub(r"[^0-9a-z]+", "_", text).strip("_")


def _text(item: dict, name: str, where: str, limit: int, required: bool = False) -> str:
    value = item.get(name)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ResourceValidationError(f"{where}{name} must be a string")
    value = " ".join(value.split())
    if required and not value:
        raise ResourceValidationError(f"{where}{name} is required")
    if len(value) > limit:
        raise ResourceValidationError(f"{where}{name} is longer than {limit} characters; shorten it")
    return value


def _number(item: dict, name: str, where: str) -> float | None:
    value = item.get(name)
    if value is None:
        return None
    if not finite(value):
        raise ResourceValidationError(f"{where}{name} must be a finite number (use value_text for qualitative values)")
    return float(value)


def clean_fact(item: dict, where: str = "") -> tuple[dict, list[str]]:
    """Validate one fact into stored columns; returns (row, notes for the caller)."""
    if not isinstance(item, dict):
        raise ResourceValidationError(f"{where or 'fact '}must be an object")
    unknown = sorted(set(item) - set(FIELDS))
    if unknown:
        raise ResourceValidationError(f"{where}unknown field {unknown[0]!r}; allowed: {', '.join(FIELDS)}")
    notes: list[str] = []
    material = _text(item, "material", where, 200, required=True)
    formula = _text(item, "formula", where, 200) or None
    if formula:
        try:
            keys = chem.keys(formula)
        except chem.FormulaError as error:
            raise ResourceValidationError(f"{where}formula {formula!r} is not a chemical formula ({error}). "
                                          "Give an element formula such as LiFePO4, or leave formula out") from None
    else:
        keys = chem.try_keys(material)
        if keys:
            formula = material
        else:
            notes.append(f"{where}material {material!r} is not a parseable formula: stored as text only. "
                         "Give formula (e.g. LiFePO4) so composition queries find it")
    prop = PROPERTY_ALIASES.get(snake(_text(item, "property", where, 80, required=True)))
    prop = prop or snake(item["property"])
    if not prop or len(prop) > 60:
        raise ResourceValidationError(f"{where}property must be a short name such as band_gap or ionic_conductivity")
    value, value_max = _number(item, "value", where), _number(item, "value_max", where)
    value_text = _text(item, "value_text", where, 200) or None
    if value is None and value_max is not None:
        raise ResourceValidationError(f"{where}value_max needs value (the lower bound of the range)")
    if value is not None and value_max is not None and value_max < value:
        raise ResourceValidationError(f"{where}value_max ({value_max}) is below value ({value}); give the range low to high")
    if value_max == value:
        value_max = None
    if value is None and not value_text:
        raise ResourceValidationError(f"{where}give value (a number) or value_text (a qualitative value)")
    unit = resolve(_text(item, "unit", where, 40))
    if value is not None and unit.dimension is None:
        notes.append(f"{where}unit {unit.given!r} is not in the normalisation table: range queries compare it only "
                     "with the same unit spelling")
    expected = EXPECTED.get(prop)
    if value is not None and expected and unit.dimension and unit.dimension != expected:
        notes.append(f"{where}{prop} is usually a {expected.replace('_', ' ')}, but unit {unit.given!r} is a "
                     f"{unit.dimension.replace('_', ' ')}; check the property name and unit")
    conditions = item.get("conditions") or {}
    if not isinstance(conditions, dict) or len(conditions) > 20:
        raise ResourceValidationError(f"{where}conditions must be an object with at most 20 entries, "
                                      "e.g. {\"temperature\": \"300 K\", \"c_rate\": \"0.1C\"}")
    cleaned_conditions = {}
    for name, condition in conditions.items():
        name = snake(str(name))[:40]
        if not name:
            raise ResourceValidationError(f"{where}conditions: {name!r} is not a condition name; use e.g. temperature")
        cleaned_conditions[name] = _scalar(condition, f"{where}conditions.{name}")
    base_value, base_value_max = unit.to_base(value), unit.to_base(value_max)
    if not (finite(base_value) and finite(base_value_max)):
        raise ResourceValidationError(f"{where}value is too large to convert from {unit.given!r} to {unit.base!r}; "
                                      "check the number and unit")
    return {"material": material, "formula": formula, "reduced": keys and keys["reduced"],
            "chemsys": keys and keys["chemsys"], "elements": keys["elements"] if keys else [],
            "property": prop, "value": value, "value_max": value_max, "value_text": value_text,
            "unit": unit.given, "unit_key": unit.key, "dimension": unit.dimension,
            "base_value": base_value, "base_value_max": base_value_max,
            "conditions": json.dumps(cleaned_conditions, sort_keys=True, ensure_ascii=False),
            "method": _text(item, "method", where, 120), "note": _text(item, "note", where, 500)}, notes


def _scalar(value, where: str):
    """A condition value: a short string, a finite number within SQLite's integer range, a boolean or null."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str) and len(value) <= 200:
        return value
    if isinstance(value, (int, float)) and finite(value) and abs(value) < 2**63:
        return value
    raise ResourceValidationError(f"{where} must be a short string, a finite number, true/false or null "
                                  "(not an object or list), e.g. \"300 K\" or 300")


def _fact_id(value, where: str = "fact") -> int:
    if isinstance(value, str) and value.startswith("fact:"):
        value = value[5:]
    try:
        number = int(value) if not isinstance(value, (bool, float)) else None
    except (TypeError, ValueError):
        number = None
    if number is None:
        raise ResourceValidationError(f"{where} must be a fact id such as 12 (or 'fact:12'), as query_facts returns")
    if not 1 <= number < 2**63:
        raise ResourceValidationError(f"{where} must be a positive fact id")
    return number


# ---- Storage -----------------------------------------------------------------------------------

COLUMNS = ("material", "formula", "reduced", "chemsys", "property", "value", "value_max", "value_text", "unit",
           "unit_key", "dimension", "base_value", "base_value_max", "conditions", "method", "note")


def _insert(connection: sqlite3.Connection, context, row: dict) -> int:
    cursor = connection.execute(
        f"INSERT INTO facts ({', '.join(COLUMNS)}, created_at, created_by) VALUES ({', '.join('?' * (len(COLUMNS) + 2))})",
        (*(row[c] for c in COLUMNS), now(), actor(context)))
    _elements(connection, cursor.lastrowid, row["elements"])
    return cursor.lastrowid


def _elements(connection: sqlite3.Connection, fact_id: int, elements: list[str]) -> None:
    connection.execute("DELETE FROM fact_elements WHERE fact = ?", (fact_id,))
    connection.executemany("INSERT INTO fact_elements (fact, element) VALUES (?, ?)", [(fact_id, e) for e in elements])


def _row(connection: sqlite3.Connection, fact_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise ResourceValidationError(f"There is no fact {fact_id} in this table; use query_facts to find ids")
    return row


def present(rows: list[sqlite3.Row], connection: sqlite3.Connection) -> list[dict]:
    sources = sources_for(connection, [key(row["id"]) for row in rows])
    facts = []
    for row in rows:
        record = {"id": row["id"], "key": key(row["id"]), "material": row["material"], "formula": row["formula"],
                  "reduced": row["reduced"], "chemsys": row["chemsys"], "property": row["property"],
                  "value": row["value"], "value_max": row["value_max"], "value_text": row["value_text"], "unit": row["unit"],
                  "conditions": json.loads(row["conditions"]), "method": row["method"], "note": row["note"],
                  "status": row["status"], "created_by": row["created_by"], "created_at": row["created_at"],
                  "sources": sources[key(row["id"])]}
        if row["dimension"] and row["value"] is not None:
            record["normalized"] = {"value": row["base_value"], "value_max": row["base_value_max"],
                                    "unit": resolve(row["unit"]).base, "dimension": row["dimension"]}
        if row["updated_at"]:
            record.update(updated_by=row["updated_by"], updated_at=row["updated_at"])
        if row["status"] == "retracted":
            record["retract_reason"] = row["retract_reason"]
        facts.append(record)
    return facts


def _duplicates(connection: sqlite3.Connection, fact_id: int, row: dict, papers: set[str]) -> list[int]:
    """Earlier active facts with the same material, property and value that cite one of the same Papers."""
    if not papers:
        return []
    material = ("reduced = ?", row["reduced"]) if row["reduced"] else ("reduced IS NULL AND lower(material) = lower(?)", row["material"])
    candidates = connection.execute(
        f"SELECT DISTINCT f.* FROM facts f JOIN sources s ON s.record = 'fact:' || f.id"
        f" WHERE f.id < ? AND f.status = 'active' AND f.property = ? AND {material[0]}"
        f" AND s.paper IN ({','.join('?' * len(papers))}) LIMIT 50",
        (fact_id, row["property"], material[1], *sorted(papers))).fetchall()
    close = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)))
    found = []
    for other in candidates:
        if row["value"] is None:
            same = other["value"] is None and (other["value_text"] or "").casefold() == (row["value_text"] or "").casefold()
        elif row["dimension"] and other["dimension"] == row["dimension"]:
            same = close(other["base_value"], row["base_value"]) and close(other["base_value_max"], row["base_value_max"])
        else:
            same = other["unit_key"] == row["unit_key"] and close(other["value"], row["value"]) and close(other["value_max"], row["value_max"])
        if same:
            found.append(other["id"])
    return found


# ---- Resource actions --------------------------------------------------------------------------

def record(context: NodeResourceContext, arguments: dict) -> dict:
    """Insert a verified batch (all or nothing). Items carry ``_sources`` from the tool handler."""
    items = arguments.get("facts")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise ResourceValidationError(f"facts must be a list of 1 to {MAX_BATCH} facts")
    note = _text(arguments, "note", "", 500)
    prepared = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ResourceValidationError(f"facts.{index} must be an object")
        sources = trusted_sources(item)
        row, notes = clean_fact({k: v for k, v in item.items() if not k.startswith("_")}, f"facts.{index}.")
        prepared.append((row, notes, sources))
    written, warnings, duplicates = [], [], []
    with database(context) as connection:
        for row, notes, sources in prepared:
            fact_id = _insert(connection, context, row)
            add_sources(connection, context, key(fact_id), sources)
            written.append(fact_id)
            warnings.extend(notes)
        for index, (fact_id, (row, _, sources)) in enumerate(zip(written, prepared)):
            if same := _duplicates(connection, fact_id, row, {s["paper"] for s in sources}):
                duplicates.append({"index": index, "fact": fact_id, "same_as": same})
        log(connection, context, "record", [key(i) for i in written], note)
    result: dict = {"recorded": [{"index": i, "id": fact_id, "key": key(fact_id)} for i, fact_id in enumerate(written)]}
    if warnings:
        result["warnings"] = warnings[:100]
    if duplicates:
        result["possible_duplicates"] = duplicates
        result["hint"] = ("Some facts repeat an earlier fact (same material, property and value citing the same Paper). "
                          "If they are the same measurement, retract the new one with retract_facts.")
    return result


def user_add(context: NodeResourceContext, arguments: dict) -> dict:
    """The workspace user's entry: one fact, no citations."""
    row, notes = clean_fact({k: v for k, v in (arguments.get("fact") or {}).items() if k in FIELDS})
    with database(context) as connection:
        fact_id = _insert(connection, context, row)
        log(connection, context, "record", [key(fact_id)], _text(arguments, "note", "", 500) or "entered by the user")
        fact = present([_row(connection, fact_id)], connection)[0]
    return {"fact": fact, "warnings": notes}


def revise(context: NodeResourceContext, arguments: dict) -> dict:
    fact_id = _fact_id(arguments.get("fact"))
    changes = arguments.get("changes") or {}
    if not isinstance(changes, dict):
        raise ResourceValidationError("changes must be an object of fields to set, e.g. {\"value\": 3.2, \"unit\": \"eV\"}")
    note = _text(arguments, "note", "", 500)
    if len(note) < 3:
        raise ResourceValidationError("note is required: say why the fact is revised (e.g. 'misread table 2')")
    sources = trusted_sources(arguments)
    replace = bool(arguments.get("replace_citations"))
    if replace and not sources:
        raise ResourceValidationError("replace_citations needs new citations that support the revised fact")
    if not changes and not sources:
        raise ResourceValidationError("Nothing to revise: give changes and/or citations")
    with database(context) as connection:
        current = _row(connection, fact_id)
        if current["status"] != "active":
            raise ResourceValidationError(f"Fact {fact_id} is retracted ({current['retract_reason']}); record a new fact instead")
        merged = {name: current[name] for name in FIELDS}
        merged["conditions"] = json.loads(current["conditions"])
        # A new material without a new formula must not keep the old formula.
        if "material" in changes and "formula" not in changes:
            merged["formula"] = None
        # A new number replaces a range, and vice versa, unless both are given.
        if "value" in changes and "value_max" not in changes:
            merged["value_max"] = None
        merged.update(changes)
        row, notes = clean_fact(merged, "changes.")
        changed = [name for name in FIELDS if name in changes and row[name] != current[name]]
        content = [name for name in changed if name in CONTENT]
        user = context.actor_id is None
        if content and not user and not sources:
            raise ResourceValidationError(
                f"Changing {', '.join(content)} needs citations that support the revised fact (they replace the old "
                "ones, which no longer support it). Re-read the page and pass citations; nothing was changed")
        # Old sources supported the old content only: they go (kept in the log), never stay as current support.
        replace = replace or bool(content)
        old = [f"{s['paper']}#p{s['page']}" for s in connection.execute(
            "SELECT paper, page FROM sources WHERE record = ? ORDER BY id", (key(fact_id),))] if replace else []
        connection.execute(f"UPDATE facts SET {', '.join(f'{c} = ?' for c in COLUMNS)}, updated_at = ?, updated_by = ? WHERE id = ?",
                           (*(row[c] for c in COLUMNS), now(), actor(context), fact_id))
        _elements(connection, fact_id, row["elements"])
        if replace:
            connection.execute("DELETE FROM sources WHERE record = ?", (key(fact_id),))
        add_sources(connection, context, key(fact_id), sources)
        detail = [f"changed {', '.join(changed)}" if changed else "",
                  f"{'replaced' if replace else 'added'} {len(sources)} citation(s)" if sources else "",
                  f"removed citation(s) {', '.join(old[:20])}" if old else ""]
        log(connection, context, "revise", [key(fact_id)], "; ".join([note, *filter(None, detail)]))
        fact = present([_row(connection, fact_id)], connection)[0]
    return {"fact": fact, "changed": changed, **({"warnings": notes} if notes else {})}


def user_revise(context: NodeResourceContext, arguments: dict) -> dict:
    return revise(context, {"fact": arguments.get("fact"), "changes": arguments.get("changes"), "note": arguments.get("note")})


def retract(context: NodeResourceContext, arguments: dict) -> dict:
    ids = arguments.get("facts")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH:
        raise ResourceValidationError(f"facts must be a list of 1 to {MAX_BATCH} fact ids")
    ids = list(dict.fromkeys(_fact_id(value, f"facts.{index}") for index, value in enumerate(ids)))
    reason = _text(arguments, "reason", "", 500)
    if len(reason) < 3:
        raise ResourceValidationError("Give a reason for the retraction (e.g. 'duplicate of fact:3', 'misread table 2')")
    with database(context) as connection:
        rows = [_row(connection, fact_id) for fact_id in ids]
        todo = [row["id"] for row in rows if row["status"] == "active"]
        connection.executemany("UPDATE facts SET status = 'retracted', retract_reason = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                               [(reason, now(), actor(context), fact_id) for fact_id in todo])
        if todo:
            log(connection, context, "retract", [key(i) for i in todo], reason)
    return {"retracted": todo, "already_retracted": [i for i in ids if i not in todo]}


def _where(connection: sqlite3.Connection, arguments: dict, notes: list[str]) -> tuple[list[str], list]:
    """SQL conditions for every filter except the value range (see query)."""
    where, values = [], []
    if not arguments.get("include_retracted"):
        where.append("f.status = 'active'")
    if ids := arguments.get("ids"):
        if not isinstance(ids, list) or len(ids) > 200:
            raise ResourceValidationError("ids must be a list of at most 200 fact ids")
        ids = [_fact_id(value, "ids") for value in ids]
        where.append(f"f.id IN ({','.join('?' * len(ids))})")
        values += ids
    if material := _text(arguments, "material", "", 200):
        keys = chem.try_keys(material)
        escaped = material.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(lower(f.material) LIKE ? ESCAPE '\\'" + (" OR f.reduced = ?)" if keys else ")"))
        values += [f"%{escaped}%", *([keys["reduced"]] if keys else [])]
    if formula := _text(arguments, "formula", "", 200):
        try:
            keys = chem.keys(formula)
        except chem.FormulaError as error:
            raise ResourceValidationError(f"formula {formula!r} is not a chemical formula ({error}); "
                                          "use material to search the text instead") from None
        where.append("f.reduced = ?")
        values.append(keys["reduced"])
    if chemsys := arguments.get("chemsys"):
        try:
            system = chem.chemsys_of(chemsys if isinstance(chemsys, (str, list)) else "")
        except chem.FormulaError as error:
            raise ResourceValidationError(f"chemsys: {error}; give element symbols such as 'Li-Fe-P-O'") from None
        if not system:
            raise ResourceValidationError("chemsys needs element symbols such as 'Li-Fe-P-O'")
        mode = arguments.get("chemsys_mode") or "exact"
        if mode == "exact":
            where.append("f.chemsys = ?")
            values.append(system)
        elif mode == "within":
            elements = system.split("-")
            where.append("f.chemsys IS NOT NULL AND NOT EXISTS (SELECT 1 FROM fact_elements e WHERE e.fact = f.id"
                         f" AND e.element NOT IN ({','.join('?' * len(elements))}))")
            values += elements
        else:
            raise ResourceValidationError("chemsys_mode must be 'exact' or 'within'")
    if elements := arguments.get("elements"):
        if not isinstance(elements, list) or len(elements) > 20:
            raise ResourceValidationError("elements must be a list of element symbols, e.g. ['Li', 'O']")
        try:
            required = chem.chemsys_of([str(e).strip() for e in elements]).split("-")
        except chem.FormulaError as error:
            raise ResourceValidationError(f"elements: {error}") from None
        for element in required:
            where.append("EXISTS (SELECT 1 FROM fact_elements e WHERE e.fact = f.id AND e.element = ?)")
            values.append(element)
    if prop := _text(arguments, "property", "", 80):
        prop = PROPERTY_ALIASES.get(snake(prop), snake(prop))
        where.append("f.property = ?")
        values.append(prop)
        if not connection.execute("SELECT 1 FROM facts WHERE property = ? LIMIT 1", (prop,)).fetchone():
            notes.append(f"No fact has property {prop!r}; list_fact_vocabulary shows the property names in use")
    if method := _text(arguments, "method", "", 120):
        where.append("lower(f.method) LIKE ?")
        values.append(f"%{method.casefold()}%")
    if paper := _text(arguments, "paper", "", 100):
        where.append("EXISTS (SELECT 1 FROM sources s WHERE s.record = 'fact:' || f.id AND s.paper = ?)")
        values.append(paper)
    conditions = arguments.get("conditions") or {}
    if not isinstance(conditions, dict) or len(conditions) > 10:
        raise ResourceValidationError("conditions must be an object of at most 10 exact matches, e.g. {\"temperature\": \"300 K\"}")
    for name, condition in conditions.items():
        name = snake(str(name))
        if not re.fullmatch(r"[a-z0-9_]{1,40}", name):
            raise ResourceValidationError(f"conditions: {name!r} is not a condition name")
        condition = _scalar(condition, f"conditions.{name}")
        if condition is None:
            where.append(f"json_extract(f.conditions, '$.{name}') IS NULL")
        elif isinstance(condition, str):
            where.append(f"lower(CAST(json_extract(f.conditions, '$.{name}') AS TEXT)) = ?")
            values.append(condition.strip().casefold())
        else:
            where.append(f"json_extract(f.conditions, '$.{name}') = ?")
            values.append(condition)
    return where, values


def query(context: NodeResourceContext, arguments: dict) -> dict:
    limit = min(max(int(arguments.get("limit") or 20), 1), MAX_LIMIT)
    offset = min(max(int(arguments.get("offset") or 0), 0), 2**62)
    notes: list[str] = []
    low, high = _number(arguments, "min_value", ""), _number(arguments, "max_value", "")
    with database(context) as connection:
        where, values = _where(connection, arguments, notes)
        if low is not None or high is not None:
            if "unit" not in arguments or arguments["unit"] is None:
                raise ResourceValidationError("A value range needs unit (e.g. \"eV\"; \"\" for dimensionless values)")
            unit = resolve(_text(arguments, "unit", "", 40))
            if unit.dimension:
                matching = ["f.dimension = ?"]
                parameters: list = [unit.dimension]
                convert = unit.to_base
            else:
                matching = ["f.dimension IS NULL AND f.unit_key = ?"]
                parameters = [unit.key]
                convert = lambda value: value  # noqa: E731
                notes.append(f"unit {unit.given!r} is not in the normalisation table; only facts with the same unit "
                             "spelling were compared")
            if low is not None:
                matching.append("COALESCE(f.base_value_max, f.base_value) >= ?" if unit.dimension else "COALESCE(f.value_max, f.value) >= ?")
                parameters.append(convert(low))
            if high is not None:
                matching.append(("f.base_value" if unit.dimension else "f.value") + " <= ?")
                parameters.append(convert(high))
            matching.insert(0, "f.value IS NOT NULL")
            # Facts that pass every other filter but cannot be compared: report, never compare.
            skipped = connection.execute(
                f"SELECT unit, COUNT(*) AS n FROM facts f WHERE {' AND '.join(where or ['1'])} AND"
                f" (f.value IS NULL OR {'f.dimension IS NOT ?' if unit.dimension else 'NOT (f.dimension IS NULL AND f.unit_key = ?)'})"
                " GROUP BY unit ORDER BY n DESC LIMIT 10", (*values, unit.dimension or unit.key)).fetchall()
            if skipped:
                total = sum(row["n"] for row in skipped)
                listing = ", ".join(f"{row['unit'] or '(none)'}: {row['n']}" for row in skipped)
                notes.append(f"{total} matching fact(s) were not compared with the range because their unit is not "
                             f"comparable with {unit.given or 'a dimensionless value'} or they have no number ({listing})")
            where += matching
            values += parameters
        clause = " AND ".join(where) or "1"
        total = connection.execute(f"SELECT COUNT(*) FROM facts f WHERE {clause}", values).fetchone()[0]
        ranged = low is not None or high is not None
        order = ("f.base_value, f.id" if unit.dimension else "f.value, f.id") if ranged else "f.id DESC"
        rows = connection.execute(f"SELECT f.* FROM facts f WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
                                  (*values, limit, offset)).fetchall()
        facts = present(rows, connection)
    result = {"facts": facts, "total": total, "offset": offset, "truncated": offset + len(facts) < total}
    if notes:
        result["notes"] = notes
    return result


def vocabulary(context: NodeResourceContext, arguments: dict) -> dict:
    limit = min(max(int(arguments.get("limit") or 50), 1), 300)
    status = "" if arguments.get("include_retracted") else " WHERE status = 'active'"
    with database(context) as connection:
        counts = connection.execute(
            f"SELECT COUNT(*) AS facts, COUNT(DISTINCT COALESCE(reduced, lower(material))) AS materials,"
            f" COUNT(DISTINCT property) AS properties FROM facts{status}").fetchone()
        retracted = connection.execute("SELECT COUNT(*) FROM facts WHERE status = 'retracted'").fetchone()[0]
        properties = []
        for row in connection.execute(f"SELECT property, COUNT(*) AS n FROM facts{status} GROUP BY property ORDER BY n DESC, property LIMIT ?", (limit,)):
            units = connection.execute(f"SELECT unit, COUNT(*) AS n FROM facts{status or ' WHERE 1'} AND property = ? GROUP BY unit ORDER BY n DESC LIMIT 8",
                                       (row["property"],)).fetchall()
            properties.append({"property": row["property"], "count": row["n"], "units": {u["unit"]: u["n"] for u in units}})
        materials = [{"material": row["material"], "reduced": row["reduced"], "chemsys": row["chemsys"], "count": row["n"]}
                     for row in connection.execute(
                         f"SELECT MIN(material) AS material, reduced, chemsys, COUNT(*) AS n FROM facts{status}"
                         " GROUP BY COALESCE(reduced, lower(material)) ORDER BY n DESC, material LIMIT ?", (limit,))]
        methods = {row["method"]: row["n"] for row in connection.execute(
            f"SELECT method, COUNT(*) AS n FROM facts{status or ' WHERE 1'} AND method != '' GROUP BY lower(method) ORDER BY n DESC LIMIT ?", (min(limit, 50),))}
        condition_keys = {row["key"]: row["n"] for row in connection.execute(
            f"SELECT j.key AS key, COUNT(*) AS n FROM facts, json_each(facts.conditions) j{status} GROUP BY j.key ORDER BY n DESC LIMIT 50")}
    return {"totals": {**dict(counts), "retracted": retracted}, "properties": properties, "materials": materials,
            "methods": methods, "condition_keys": condition_keys,
            "truncated": {"properties": counts["properties"] > len(properties), "materials": counts["materials"] > len(materials)}}


# ---- Agent tools -------------------------------------------------------------------------------

def _prefixed(index: int, error: ResourceValidationError) -> ResourceValidationError:
    text = str(error)
    return ResourceValidationError(f"facts.{index}.{text}" if text.startswith("citations.") else f"facts.{index}: {text}")


async def record_facts(context, capability, arguments: dict):
    """Verify every fact's citations with the Agent's own Paper grants, then write the batch at once."""
    items = arguments.get("facts")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise ResourceValidationError(f"facts must be a list of 1 to {MAX_BATCH} facts; split larger sets into several calls")
    prepared, pages = [], {}  # Facts from one paper usually cite the same pages; read each once.
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ResourceValidationError(f"facts.{index} must be an object. Nothing was written")
        item = {k: v for k, v in item.items() if not k.startswith("_")}
        citations = item.pop("citations", None)
        clean_fact(item, f"facts.{index}.")  # Fail on shape before reading any Paper.
        try:
            sources = await verify_citations(context, capability, citations, pages=pages)
        except ResourceValidationError as error:
            raise ResourceValidationError(f"{_prefixed(index, error)}. Nothing was written; fix this fact and resend the batch") from None
        prepared.append({**item, "_sources": sources})
    return await context.node_resource_action(capability, "record", {"facts": prepared, "note": arguments.get("note") or ""})


FACT_PROPERTIES = {
    "material": {"type": "string", "maxLength": 200, "description": "The material as the paper names it, e.g. 'LiFePO4' or 'carbon-coated LFP'"},
    "formula": {"type": "string", "maxLength": 200, "description": "Chemical formula when material is a name or abbreviation (e.g. LiFePO4); enables composition queries"},
    "property": {"type": "string", "maxLength": 80, "description": "snake_case property key, e.g. band_gap, specific_capacity, ionic_conductivity, lattice_a. Reuse names from list_fact_vocabulary"},
    "value": {"type": "number", "description": "The number, or the lower bound of a range"},
    "value_max": {"type": "number", "description": "Upper bound when the paper gives a range"},
    "value_text": {"type": "string", "maxLength": 200, "description": "Qualitative value when there is no number, e.g. 'metallic', 'stable in air'"},
    "unit": {"type": "string", "maxLength": 40, "description": "Unit exactly as meant, e.g. eV, mAh/g, S/cm, °C, Å; empty for dimensionless"},
    "conditions": {"type": "object", "description": "Measurement conditions, e.g. {\"temperature\": \"25 °C\", \"c_rate\": \"0.1C\", \"cycle\": 100}"},
    "method": {"type": "string", "maxLength": 120, "description": "How it was obtained, e.g. 'DFT-HSE06', 'UV-vis Tauc', 'EIS', 'galvanostatic cycling'"},
    "note": {"type": "string", "maxLength": 500, "description": "Sample details or caveats (synthesis, doping level, phase)"},
}

QUERY_TOOL = (
    "Search the fact table for material property records (material, property, value with unit, conditions, method) "
    "and see each record's sources (Paper id and page, the verified quote, fresh/stale). Filters combine with AND: "
    "material (text in the name, or the same composition when it is a formula), formula (same reduced composition), "
    "chemsys ('Li-Fe-P-O'; chemsys_mode 'within' finds every material made only of those elements), elements "
    "(must all be present), property, a value range with its unit (values in other units of the same dimension are "
    "converted, e.g. meV to eV; incompatible units are left out and counted in notes), method, conditions (exact), "
    "paper (facts citing that Paper id). Retracted facts are hidden unless include_retracted. Call "
    "list_fact_vocabulary first to learn the property and material names in use. Citing a fact elsewhere means "
    "citing its Paper pages; the store does not let you open those Papers.")
QUERY_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "material": {"type": "string", "maxLength": 200, "description": "Text in the material name, or a formula"},
    "formula": {"type": "string", "maxLength": 200, "description": "Exact composition, any spelling (LiFePO4 = FeLiO4P)"},
    "chemsys": {"type": "string", "maxLength": 200, "description": "Element set such as 'Li-Fe-P-O'"},
    "chemsys_mode": {"type": "string", "enum": ["exact", "within"], "description": "exact (default): exactly these elements; within: only elements from this set"},
    "elements": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": "Elements that must all be present"},
    "property": {"type": "string", "maxLength": 80},
    "min_value": {"type": "number"}, "max_value": {"type": "number"},
    "unit": {"type": "string", "maxLength": 40, "description": "Unit of min_value/max_value (required with a range)"},
    "method": {"type": "string", "maxLength": 120, "description": "Text in the method"},
    "conditions": {"type": "object", "description": "Exact condition values, e.g. {\"temperature\": \"300 K\"}"},
    "paper": {"type": "string", "maxLength": 100, "description": "Only facts citing this Paper id"},
    "ids": {"type": "array", "maxItems": 200, "items": {"type": "integer"}, "description": "Only these fact ids"},
    "include_retracted": {"type": "boolean"},
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "Default 20"},
    "offset": {"type": "integer", "minimum": 0}}}

VOCABULARY_TOOL = (
    "Summarise what the fact table holds: totals, property names with their counts and units, materials with "
    "counts, methods and condition keys. Use it before query_facts (to use the stored names) and before "
    "record_facts (to reuse existing property names instead of inventing synonyms).")
VOCABULARY_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "limit": {"type": "integer", "minimum": 1, "maximum": 300, "description": "Rows per list, default 50"},
    "include_retracted": {"type": "boolean"}}}

RECORD_TOOL = (
    "Add 1-50 material property facts to the fact table. Each fact gives material (and formula when the material "
    "is a name such as 'LFP'), property (snake_case, reuse names from list_fact_vocabulary), value (or value + "
    "value_max for a range, or value_text when qualitative), unit as written, conditions and method, and its own "
    "citations: the Paper id, the PDF page and a verbatim quote from that page (copy it from read_paper; a table "
    "row or sentence containing the number is best). Every citation is checked now against a Paper you can read; "
    "if any fact fails, nothing is written and the error names the fact and citation to fix. The result flags "
    "possible duplicates of facts already recorded from the same Paper.")
RECORD_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts"], "properties": {
    "facts": {"type": "array", "minItems": 1, "maxItems": MAX_BATCH, "items": {
        "type": "object", "additionalProperties": False, "required": ["material", "property", "citations"],
        "properties": {**FACT_PROPERTIES, "citations": citations_schema()}}},
    "note": {"type": "string", "maxLength": 500, "description": "Why these facts were recorded (kept in the write log)"}}}

REVISE_TOOL = (
    "Correct one fact: set any of its fields (material, formula, property, value, value_max, value_text, unit, "
    "conditions, method, note). note says why and is logged. Changing anything but the fact's note requires "
    "citations for the corrected content (checked like record_facts); they replace the old citations, which are "
    "listed in the log. Without content changes, citations are added, or replace the old ones with "
    "replace_citations: that is how to refresh a fact whose sources check_knowledge_provenance marked stale, after "
    "re-reading the page. Retracted facts cannot be revised.")
REVISE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["fact", "note"], "properties": {
    "fact": {"type": "integer", "minimum": 1, "description": "Fact id from query_facts"},
    "changes": {"type": "object", "additionalProperties": False, "properties": FACT_PROPERTIES,
                "description": "Only the fields to change"},
    "citations": citations_schema(0),
    "replace_citations": {"type": "boolean", "description": "Replace all existing citations with the given ones"},
    "note": {"type": "string", "minLength": 3, "maxLength": 500, "description": "Why the fact is revised"}}}

RETRACT_TOOL = (
    "Retract facts that are wrong, duplicated or no longer supported (e.g. their source changed and no longer says "
    "so). Retraction hides them from queries but keeps them, with the reason, for audit; include_retracted shows them.")
RETRACT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts", "reason"], "properties": {
    "facts": {"type": "array", "minItems": 1, "maxItems": MAX_BATCH, "items": {"type": "integer"}, "description": "Fact ids"},
    "reason": {"type": "string", "minLength": 3, "maxLength": 500}}}


def register(registration):
    register_store(registration, card="facts", label="Fact table",
        description="Material property records (value, unit, conditions, method) that Agents cite to Paper pages",
        icon="table", color="#5f8fb0", schema=SCHEMA, default_size=(360, 250),
        read_tools=[
            Tool("query", "query_facts", QUERY_TOOL, QUERY_SCHEMA, forward("query"), {"query": query}),
            Tool("vocabulary", "list_fact_vocabulary", VOCABULARY_TOOL, VOCABULARY_SCHEMA, forward("vocabulary"),
                 {"vocabulary": vocabulary}),
        ],
        curate_tools=[
            Tool("record", "record_facts", RECORD_TOOL, RECORD_SCHEMA, record_facts, {"record": record}),
            Tool("revise", "revise_fact", REVISE_TOOL, REVISE_SCHEMA, cited("revise", required=False), {"revise": revise}),
            Tool("retract", "retract_facts", RETRACT_TOOL, RETRACT_SCHEMA, forward("retract"), {"retract": retract}),
        ],
        user_actions={"ui_query": query, "ui_vocabulary": vocabulary, "ui_add": user_add,
                      "ui_revise": user_revise, "ui_retract": retract})
