"""Structure database (knowledge.structures): crystal structures (CIF) keyed by composition, symmetry and cell.

Agents add a CIF with provenance: verified Paper citations and/or an external database
reference (Materials Project, COD, ICSD, ...). External references are recorded as given,
not verified. Keys come from structures_cif.analyse (pure Python; pymatgen when installed).
The CIF text is kept verbatim and returned only on request. Records are ``structure:<id>``.
"""
from __future__ import annotations

import json
import math
import re

from open_agent_world.plugin_api import NodeResourceContext, ResourceValidationError

from . import chem
from . import structures_cif as cif_keys
from .common import (Tool, actor, add_sources, citations_schema, cited, connect, forward, log, now, register_store,
                     sources_for, trusted_sources)

SCHEMA = """
CREATE TABLE IF NOT EXISTS structures (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    cif TEXT NOT NULL,
    formula TEXT NOT NULL,
    reduced TEXT NOT NULL,
    anonymous TEXT NOT NULL,        -- prototype-like formula, e.g. ABC3
    chemsys TEXT NOT NULL,
    nelements INTEGER NOT NULL,
    spacegroup_number INTEGER,
    spacegroup_symbol TEXT,
    crystal_system TEXT,
    a REAL NOT NULL, b REAL NOT NULL, c REAL NOT NULL,
    alpha REAL NOT NULL, beta REAL NOT NULL, gamma REAL NOT NULL,
    volume REAL NOT NULL,
    nsites INTEGER NOT NULL,
    sites_basis TEXT NOT NULL,      -- 'cell' or 'asymmetric unit' (CIF without symmetry operations)
    volume_per_site REAL,
    analysis TEXT NOT NULL,         -- 'cif' (tags as declared) or 'pymatgen'
    external_db TEXT, external_id TEXT, external_url TEXT,
    properties TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active | retracted
    retracted_reason TEXT,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS structures_reduced ON structures(reduced);
CREATE INDEX IF NOT EXISTS structures_chemsys ON structures(chemsys);
CREATE INDEX IF NOT EXISTS structures_prototype ON structures(anonymous, spacegroup_number);
CREATE TABLE IF NOT EXISTS structure_elements (
    structure INTEGER NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
    element TEXT NOT NULL,
    PRIMARY KEY (structure, element)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS structure_elements_element ON structure_elements(element);
"""

MAX_RESULTS = 100
MAX_SIMILAR = 20
MAX_CANDIDATES = 500
MAX_MATCHED = 50  # duplicate candidates read per add
MAX_FITS = 10  # StructureMatcher fits per call: each parses two CIFs under the node lock
MAX_FIT_SITES = 200  # larger cells are compared by cell distance only
MAX_ID = 2 ** 63 - 1
DUPLICATE_DISTANCE = 0.05
MAX_PROPERTIES = 40
_PROPERTY_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,59}")
_COLUMNS = ("id, name, formula, reduced, anonymous, chemsys, nelements, spacegroup_number, spacegroup_symbol, crystal_system,"
            " a, b, c, alpha, beta, gamma, volume, nsites, sites_basis, volume_per_site, analysis, external_db, external_id,"
            " external_url, properties, note, status, retracted_reason, created_at, created_by, updated_at, updated_by")


def _open(context: NodeResourceContext):
    return connect(context, SCHEMA)  # ``with`` commits (or rolls back) and closes


def _key(structure_id: int) -> str:
    return f"structure:{structure_id}"


# ---- Argument checks (Agents' arguments are not schema-validated by the host) ----------------

def _int(arguments: dict, name: str, default: int | None, low: int, high: int) -> int | None:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ResourceValidationError(f"{name} must be an integer from {low} to {high}")
    return value


def _text(arguments: dict, name: str, limit: int, *, required: bool = False) -> str:
    value = arguments.get(name)
    if value is None or value == "":
        if required:
            raise ResourceValidationError(f"{name} is required")
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ResourceValidationError(f"{name} must be text of at most {limit} characters")
    return value.strip()


def _elements(arguments: dict, name: str) -> list[str]:
    value = arguments.get(name)
    if not value:
        return []
    try:
        return chem.chemsys_of(value if isinstance(value, (str, list)) else str(value)).split("-")
    except (chem.FormulaError, TypeError) as error:
        raise ResourceValidationError(f"{name}: {error}; give element symbols like ['Li', 'O']") from None


def _properties(value, *, allow_null: bool) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ResourceValidationError('properties must be an object such as {"band_gap_eV": 1.1, "magnetic": false}')
    if len(value) > MAX_PROPERTIES:
        raise ResourceValidationError(f"At most {MAX_PROPERTIES} properties per structure")
    for key, item in value.items():
        if not _PROPERTY_KEY.fullmatch(key):
            raise ResourceValidationError(f"Property name {key!r}: use letters, digits, '_', '.' or '-' "
                                          "(max 60), starting with a letter; put the unit in the name, e.g. band_gap_eV")
        if item is None and allow_null:
            continue
        if isinstance(item, float) and not math.isfinite(item):
            raise ResourceValidationError(f"Property {key!r} must be a finite number")
        if not isinstance(item, (int, float, bool, str)) or (isinstance(item, str) and len(item) > 200):
            raise ResourceValidationError(f"Property {key!r} must be a number, true/false or text of at most 200 "
                                          "characters" + (" (null removes it)" if allow_null else ""))
    return value


def _external(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ResourceValidationError('external_ref must be an object: {"database": "COD", "id": "1000041", "url": "https://..."}')
    database, identifier, url = value.get("database"), value.get("id"), value.get("url")
    if not isinstance(database, str) or not 1 <= len(database.strip()) <= 80:
        raise ResourceValidationError('external_ref.database is required, e.g. "Materials Project", "COD", "ICSD", "OQMD"')
    if isinstance(identifier, int) and not isinstance(identifier, bool):
        identifier = str(identifier)
    if not isinstance(identifier, str) or not 1 <= len(identifier.strip()) <= 120:
        raise ResourceValidationError('external_ref.id is required: the entry id in that database, e.g. "mp-149"')
    if url is not None and (not isinstance(url, str) or len(url) > 500 or not re.match(r"https?://\S+$", url)):
        raise ResourceValidationError("external_ref.url must be an http(s) URL of at most 500 characters")
    return {"database": database.strip(), "id": identifier.strip(), "url": url}


def _analyse(cif) -> dict:
    try:
        return cif_keys.analyse(cif)
    except cif_keys.CifError as error:
        raise ResourceValidationError(f"Invalid CIF: {error}") from None
    except chem.FormulaError as error:
        raise ResourceValidationError(f"Invalid CIF composition: {error}") from None


def _spacegroup(value) -> int:
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool):
        if not 1 <= value <= 230:
            raise ResourceValidationError("spacegroup number must be from 1 to 230")
        return value
    number = cif_keys.spacegroup_number(value) if isinstance(value, str) and len(value) < 40 else None
    if number is None:
        raise ResourceValidationError(f"Unknown space group {value!r}: give the number (1-230) or a standard "
                                      "short symbol such as 'Fm-3m', 'P2_1/c' or 'Pnma'")
    return number


# ---- Rows -------------------------------------------------------------------------------------

def _row(row, sources: list[dict] | None = None) -> dict:
    """A structure as returned to Agents and the UI, without the CIF."""
    external = {"database": row["external_db"], "id": row["external_id"], "url": row["external_url"],
                "verified": False} if row["external_db"] else None
    result = {
        "id": row["id"], "record": _key(row["id"]), "name": row["name"], "formula": row["formula"],
        "reduced": row["reduced"], "chemsys": row["chemsys"],
        "spacegroup": {"number": row["spacegroup_number"], "symbol": row["spacegroup_symbol"]},
        "crystal_system": row["crystal_system"],
        "cell": {key: row[key] for key in ("a", "b", "c", "alpha", "beta", "gamma")}, "volume": row["volume"],
        "nsites": row["nsites"], "sites_basis": row["sites_basis"], "volume_per_site": row["volume_per_site"],
        "properties": json.loads(row["properties"]), "note": row["note"], "external_ref": external,
        "status": row["status"], "created_by": row["created_by"], "updated_at": row["updated_at"],
    }
    if row["status"] != "active":
        result["retracted_reason"] = row["retracted_reason"]
    if sources is not None:
        result["sources"] = sources
    return result


def _with_sources(connection, rows) -> list[dict]:
    rows = list(rows)
    found = sources_for(connection, [_key(row["id"]) for row in rows])
    return [_row(row, found[_key(row["id"])]) for row in rows]


def _get(connection, structure_id) -> dict:
    if isinstance(structure_id, bool) or not isinstance(structure_id, int) or not 1 <= structure_id <= MAX_ID:
        raise ResourceValidationError("id must be a structure id (an integer from find_structures)")
    row = connection.execute(f"SELECT {_COLUMNS}, cif FROM structures WHERE id = ?", (structure_id,)).fetchone()
    if row is None:
        raise ResourceValidationError(f"No structure {structure_id} in this database; find ids with find_structures")
    return row


# ---- Read -------------------------------------------------------------------------------------

def _find(context: NodeResourceContext, arguments: dict) -> dict:
    where, values = [], []

    def condition(sql: str, *parameters):
        where.append(sql)
        values.extend(parameters)

    if arguments.get("formula"):
        keys = chem.try_keys(_text(arguments, "formula", 200))
        if keys is None:
            raise ResourceValidationError(f"formula {arguments['formula']!r} is not a chemical formula; give e.g. "
                                          "'LiFePO4', or search by chemsys or elements")
        condition("s.reduced = ?", keys["reduced"])
    chemsys = _elements(arguments, "chemsys")
    if chemsys:
        mode = arguments.get("chemsys_match") or "exact"
        if mode == "exact":
            condition("s.chemsys = ?", "-".join(chemsys))
        elif mode == "within":  # Every element of the structure is in the given set (subsystems included).
            condition(f"NOT EXISTS (SELECT 1 FROM structure_elements e WHERE e.structure = s.id"
                      f" AND e.element NOT IN ({','.join('?' * len(chemsys))}))", *chemsys)
        else:
            raise ResourceValidationError("chemsys_match must be 'exact' or 'within'")
    for element in _elements(arguments, "elements"):
        condition("EXISTS (SELECT 1 FROM structure_elements e WHERE e.structure = s.id AND e.element = ?)", element)
    for element in _elements(arguments, "exclude_elements"):
        condition("NOT EXISTS (SELECT 1 FROM structure_elements e WHERE e.structure = s.id AND e.element = ?)", element)
    if arguments.get("spacegroup") is not None:
        condition("s.spacegroup_number = ?", _spacegroup(arguments["spacegroup"]))
    if arguments.get("crystal_system"):
        if arguments["crystal_system"] not in cif_keys.CRYSTAL_SYSTEMS:
            raise ResourceValidationError(f"crystal_system must be one of {', '.join(cif_keys.CRYSTAL_SYSTEMS)}")
        condition("s.crystal_system = ?", arguments["crystal_system"])
    for name, operator in (("nsites_min", ">="), ("nsites_max", "<=")):
        bound = _int(arguments, name, None, 1, 1_000_000)
        if bound is not None:
            condition(f"s.nsites {operator} ?", bound)
    text = _text(arguments, "text", 200)
    if text:
        pattern = "%" + re.sub(r"([%_\\])", r"\\\1", text) + "%"
        condition("(s.name LIKE ? ESCAPE '\\' OR s.note LIKE ? ESCAPE '\\' OR s.formula LIKE ? ESCAPE '\\')", *[pattern] * 3)
    prop = _text(arguments, "has_property", 60)
    if prop:
        if not _PROPERTY_KEY.fullmatch(prop):
            raise ResourceValidationError("has_property must be a property name: letters, digits, '_', '.' or '-', "
                                          "starting with a letter (e.g. band_gap_eV)")
        condition("json_type(s.properties, ?) IS NOT NULL", f'$."{prop}"')
    if not arguments.get("include_retracted"):
        condition("s.status = 'active'")
    limit = _int(arguments, "limit", 20, 1, MAX_RESULTS)
    offset = _int(arguments, "offset", 0, 0, 1_000_000)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    with _open(context) as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM structures s{clause}", values).fetchone()[0]
        rows = connection.execute(f"SELECT {_COLUMNS} FROM structures s{clause} ORDER BY s.id LIMIT ? OFFSET ?",
                                  (*values, limit, offset))
        structures = [_brief(item) for item in _with_sources(connection, rows)]
    result = {"total": total, "offset": offset, "structures": structures}
    if offset + len(structures) < total:
        result["next_offset"] = offset + len(structures)
    return result


def _brief(structure: dict) -> dict:
    """Search results carry a provenance summary; get_structure has the full citations."""
    sources = structure.pop("sources")
    structure["provenance"] = {"citations": len(sources), "stale": sum(s["status"] == "stale" for s in sources),
                               "external_ref": structure.pop("external_ref")}
    return structure


def _detail(context: NodeResourceContext, arguments: dict) -> dict:
    with _open(context) as connection:
        row = _get(connection, arguments.get("id"))
        result = _with_sources(connection, [row])[0]
    if arguments.get("include_cif"):
        result["cif"] = row["cif"]
    else:
        result["cif_chars"] = len(row["cif"])
    return result


def _candidates(connection, query: dict, exclude: int | None, include_retracted: bool) -> list:
    status = "" if include_retracted else " AND status = 'active'"
    clauses, values = ["reduced = ?"], [query["reduced"]]
    if query["spacegroup_number"]:
        clauses.append("(anonymous = ? AND spacegroup_number = ?)")
        values += [query["anonymous"], query["spacegroup_number"]]
    return connection.execute(
        f"SELECT {_COLUMNS}, cif FROM structures WHERE ({' OR '.join(clauses)}) AND id IS NOT ?{status}"
        f" ORDER BY id LIMIT {MAX_CANDIDATES}", (*values, exclude)).fetchall()


def _rank(query: dict, query_cif: str, rows: list) -> tuple[str, list[tuple]]:
    """(method, [(order, row, kind, distance, matcher result)]) best first."""
    ranked = []
    for row in rows:
        same_formula = row["reduced"] == query["reduced"]
        same_group = bool(query["spacegroup_number"]) and row["spacegroup_number"] == query["spacegroup_number"]
        kind = ("same formula and space group" if same_formula and same_group else
                "same formula" if same_formula else "same prototype")
        ranked.append([("same formula and space group", "same formula", "same prototype").index(kind), row, kind,
                       cif_keys.cell_distance(query, dict(row)), None])
    method = ("pure-python: candidates share the reduced formula or the prototype (anonymous formula + space group); "
              "ranked by that, then by cell distance (volume per site, and cell shape within one space group)")
    ranked.sort(key=lambda item: (item[0], item[3]))
    if cif_keys.PYMATGEN:
        fits = 0
        for item in ranked:
            if fits == MAX_FITS:
                break
            if _fittable(query, item[1]):
                item[4] = cif_keys.match(query_cif, item[1]["cif"], anonymous_match=item[2] == "same prototype")
                fits += 1
        if fits:
            method = (f"pymatgen StructureMatcher on the {fits} closest candidates with at most {MAX_FIT_SITES} sites "
                      "(fit for the same formula, fit_anonymous for prototypes); matches first, the rest by "
                      "pure-python cell distance")
            ranked.sort(key=lambda item: (not (item[4] or {}).get("match"), item[0], item[3]))
        elif ranked:
            method += f" (pymatgen not used: no candidate pair has at most {MAX_FIT_SITES} sites)"
    return method, ranked


def _fittable(query, row) -> bool:
    return 0 < (query["nsites"] or 0) <= MAX_FIT_SITES and 0 < row["nsites"] <= MAX_FIT_SITES


def _similar(context: NodeResourceContext, arguments: dict) -> dict:
    limit = _int(arguments, "limit", 5, 1, MAX_SIMILAR)
    has_id, has_cif = arguments.get("id") is not None, bool(arguments.get("cif"))
    if has_id == has_cif:
        raise ResourceValidationError("Give exactly one of id (a stored structure) or cif (CIF text to compare)")
    with _open(context) as connection:
        if has_id:
            row = _get(connection, arguments["id"])
            query, query_cif, exclude = dict(row), row["cif"], row["id"]
        else:
            query = _analyse(arguments["cif"])
            query_cif, exclude = arguments["cif"], None
        rows = _candidates(connection, query, exclude, bool(arguments.get("include_retracted")))
        method, ranked = _rank(query, query_cif, rows)
        chosen = ranked[:limit]
        found = sources_for(connection, [_key(item[1]["id"]) for item in chosen])
    results = []
    for _, row, kind, distance, matched in chosen:
        entry = _brief(_row(row, found[_key(row["id"])]))
        entry.update(kind=kind, cell_distance=distance)
        if matched is not None:
            entry["structure_match"] = matched
        results.append(entry)
    return {"query": {key: query[key] for key in ("reduced", "anonymous", "spacegroup_number", "volume_per_site")},
            "method": method, "results": results, "candidates": len(rows),
            "hint": "cell_distance is dimensionless (0 = identical cell); below ~0.05 is likely the same structure."
                    if results else "No stored structure shares this formula or prototype."}


# ---- Curate -----------------------------------------------------------------------------------

def _duplicates(connection, keys: dict, cif: str) -> list[dict]:
    """Stored structures that look like the same material: same formula and space group, close cell (or matched)."""
    if not keys["spacegroup_number"]:
        return []
    rows = connection.execute(f"SELECT {_COLUMNS}, cif FROM structures WHERE reduced = ? AND spacegroup_number = ?"
                              f" AND status = 'active' ORDER BY id LIMIT {MAX_MATCHED}",
                              (keys["reduced"], keys["spacegroup_number"])).fetchall()
    found, fits = [], 0
    for distance, row in sorted(((cif_keys.cell_distance(keys, dict(row)), row) for row in rows), key=lambda pair: pair[0]):
        matched = None
        if cif_keys.PYMATGEN and fits < MAX_FITS and _fittable(keys, row):
            matched, fits = cif_keys.match(cif, row["cif"]), fits + 1
        if (matched["match"] if matched else distance <= DUPLICATE_DISTANCE):
            found.append({"id": row["id"], "name": row["name"], "cell_distance": distance})
    return sorted(found, key=lambda item: item["id"])


def _insert(context: NodeResourceContext, arguments: dict, sources: list[dict], *, agent: bool) -> dict:
    external = _external(arguments.get("external_ref")) or {}
    if agent and not sources and not external:
        raise ResourceValidationError("Give provenance: citations (Paper pages you can read) and/or external_ref "
                                      '({"database": "COD", "id": "1000041"}) for where this CIF comes from')
    cif = arguments.get("cif")
    keys = _analyse(cif)
    properties = _properties(arguments.get("properties"), allow_null=False)
    note = _text(arguments, "note", 2000)
    name = _text(arguments, "name", 200) or " ".join(filter(None, [keys["formula"], keys["spacegroup_symbol"]]))
    who, at = actor(context), now()
    with _open(context) as connection:
        duplicates = _duplicates(connection, keys, cif)
        cursor = connection.execute(
            "INSERT INTO structures (name, cif, formula, reduced, anonymous, chemsys, nelements, spacegroup_number,"
            " spacegroup_symbol, crystal_system, a, b, c, alpha, beta, gamma, volume, nsites, sites_basis, volume_per_site,"
            " analysis, external_db, external_id, external_url, properties, note, created_at, created_by, updated_at, updated_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, cif, keys["formula"], keys["reduced"], keys["anonymous"], keys["chemsys"], keys["nelements"],
             keys["spacegroup_number"], keys["spacegroup_symbol"], keys["crystal_system"], keys["a"], keys["b"], keys["c"],
             keys["alpha"], keys["beta"], keys["gamma"], keys["volume"], keys["nsites"], keys["sites_basis"],
             keys["volume_per_site"], keys["analysis"], external.get("database"), external.get("id"), external.get("url"),
             json.dumps(properties), note, at, who, at, who))
        structure_id = cursor.lastrowid
        connection.executemany("INSERT INTO structure_elements (structure, element) VALUES (?, ?)",
                               [(structure_id, element) for element in keys["elements"]])
        add_sources(connection, context, _key(structure_id), sources)
        log(connection, context, "add", [_key(structure_id)], note or name)
        structure = _with_sources(connection, [_get(connection, structure_id)])[0]
    warnings = list(keys["warnings"])
    if duplicates:
        warnings.append(f"Possible duplicate of structure {', '.join(str(d['id']) for d in duplicates)} (same formula and "
                        "space group, close cell). It was added; retract one if they are the same entry")
    return {"id": structure_id, "record": _key(structure_id), "structure": structure, "warnings": warnings,
            "possible_duplicates": duplicates}


def _add(context: NodeResourceContext, arguments: dict) -> dict:
    return _insert(context, arguments, trusted_sources(arguments), agent=True)


def _annotate(context: NodeResourceContext, arguments: dict) -> dict:
    sources = trusted_sources(arguments)
    updates = _properties(arguments.get("properties"), allow_null=True)
    note = arguments.get("note")
    if note is not None:
        note = _text(arguments, "note", 2000)
    if not updates and note is None and not sources:
        raise ResourceValidationError("Nothing to change: give properties to merge, a note, and/or citations")
    with _open(context) as connection:
        row = _get(connection, arguments.get("id"))
        if row["status"] != "active":
            raise ResourceValidationError(f"Structure {row['id']} is retracted; add a corrected structure instead")
        properties = {**json.loads(row["properties"]), **updates}
        properties = {key: value for key, value in properties.items() if value is not None}
        if len(properties) > MAX_PROPERTIES:
            raise ResourceValidationError(f"At most {MAX_PROPERTIES} properties per structure; remove some with null")
        connection.execute("UPDATE structures SET properties = ?, note = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                           (json.dumps(properties), row["note"] if note is None else note, now(), actor(context), row["id"]))
        add_sources(connection, context, _key(row["id"]), sources)
        changed = ([f"properties {', '.join(sorted(updates))}"] if updates else []) + (["note"] if note is not None else []) \
            + ([f"{len(sources)} citations"] if sources else [])
        log(connection, context, "annotate", [_key(row["id"])], "; ".join(changed))
        return {"structure": _with_sources(connection, [_get(connection, row["id"])])[0]}


def _retract(context: NodeResourceContext, arguments: dict) -> dict:
    reason = _text(arguments, "reason", 500)
    if len(reason) < 3:
        raise ResourceValidationError("reason is required: say why this structure is withdrawn (kept for audit)")
    with _open(context) as connection:
        row = _get(connection, arguments.get("id"))
        if row["status"] != "active":
            raise ResourceValidationError(f"Structure {row['id']} is already retracted")
        connection.execute("UPDATE structures SET status = 'retracted', retracted_reason = ?, updated_at = ?, updated_by = ?"
                           " WHERE id = ?", (reason, now(), actor(context), row["id"]))
        log(connection, context, "retract", [_key(row["id"])], reason)
    return {"id": row["id"], "record": _key(row["id"]), "status": "retracted"}


# ---- Workspace (local user) -------------------------------------------------------------------

def _user_add(context: NodeResourceContext, arguments: dict) -> dict:
    # The user's own entries carry no citations; an external reference is recorded if given.
    return _insert(context, {k: v for k, v in arguments.items() if not k.startswith("_")}, [], agent=False)


def _summary(context: NodeResourceContext, arguments: dict) -> dict:
    with _open(context) as connection:
        counts = dict(connection.execute("SELECT status, COUNT(*) FROM structures GROUP BY status").fetchall())
        systems = connection.execute("SELECT chemsys, COUNT(*) AS n FROM structures WHERE status = 'active'"
                                     " GROUP BY chemsys ORDER BY n DESC, chemsys LIMIT 8").fetchall()
        crystal = connection.execute("SELECT crystal_system, COUNT(*) AS n FROM structures WHERE status = 'active'"
                                     " GROUP BY crystal_system ORDER BY n DESC").fetchall()
    return {"active": counts.get("active", 0), "retracted": counts.get("retracted", 0),
            "chemsys": [{"chemsys": row["chemsys"], "count": row["n"]} for row in systems],
            "crystal_systems": [{"crystal_system": row["crystal_system"] or "unknown", "count": row["n"]} for row in crystal],
            "pymatgen": cif_keys.PYMATGEN}


# ---- Tools ------------------------------------------------------------------------------------

_ELEMENT_LIST = {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 3}}
_EXTERNAL = {"type": "object", "required": ["database", "id"], "additionalProperties": False, "properties": {
    "database": {"type": "string", "maxLength": 80, "description": "e.g. Materials Project, COD, ICSD, OQMD, OPTIMADE provider"},
    "id": {"type": "string", "maxLength": 120, "description": "Entry id in that database, e.g. mp-149 or 1000041"},
    "url": {"type": "string", "maxLength": 500}},
    "description": "Where the CIF was obtained. Recorded as given; it is not verified."}
_PROPERTIES = {"type": "object", "maxProperties": MAX_PROPERTIES, "description": 'Annotations: numbers, true/false or short text, with the unit in the name, '
               'e.g. {"band_gap_eV": 1.1, "magnetic": false}'}
_ID = {"type": "integer", "minimum": 1, "description": "Structure id from find_structures"}
_CIF = {"type": "string", "maxLength": cif_keys.MAX_CIF_CHARS, "description": "CIF text; only the first data block is read"}

FIND = Tool("find", "find_structures",
    "Search this structure database by composition, symmetry and size. formula matches the reduced formula "
    "(NaCl = Na4Cl4). chemsys 'Li-Fe-O' matches exactly that element set; with chemsys_match 'within' it matches "
    "every structure whose elements are all in the set (Li2O, Fe2O3, LiFeO2, ...). elements must all be present; "
    "exclude_elements must be absent. spacegroup takes a number or a standard symbol (Fm-3m). Results are "
    "paged, without CIF text; use get_structure for a CIF and its full citations.",
    {"type": "object", "additionalProperties": False, "properties": {
        "formula": {"type": "string", "maxLength": 200}, "chemsys": {"type": "string", "maxLength": 200},
        "chemsys_match": {"type": "string", "enum": ["exact", "within"]},
        "elements": _ELEMENT_LIST, "exclude_elements": _ELEMENT_LIST,
        "spacegroup": {"type": "string", "maxLength": 40, "description": "Number 1-230 or standard symbol, e.g. '225' or 'Fm-3m'"},
        "crystal_system": {"type": "string", "enum": list(cif_keys.CRYSTAL_SYSTEMS)},
        "nsites_min": {"type": "integer", "minimum": 1}, "nsites_max": {"type": "integer", "minimum": 1},
        "text": {"type": "string", "maxLength": 200, "description": "Substring of the name, note or formula"},
        "has_property": {"type": "string", "maxLength": 60, "description": "Only structures annotated with this property"},
        "include_retracted": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS}, "offset": {"type": "integer", "minimum": 0}}},
    forward("find"), {"find": _find})
GET = Tool("get", "get_structure",
    "Get one structure: cell, space group, composition, properties, external reference and each Paper citation "
    "(with stale marks). Set include_cif to receive the CIF text (can be long). Citations are provenance only: "
    "to read a cited Paper you need your own connection to it.",
    {"type": "object", "required": ["id"], "additionalProperties": False, "properties": {
        "id": _ID, "include_cif": {"type": "boolean"}}},
    forward("get"), {"get": _detail})
SIMILAR = Tool("similar", "find_similar_structures",
    "Find stored structures similar to a stored one (id) or to a CIF you pass (cif). Candidates share the reduced "
    "formula (same compound, possibly another polymorph) or the prototype (anonymous formula + space group, e.g. "
    "rock salt AB in Fm-3m). The result says which method ranked them: pure-python cell distance, or pymatgen "
    "StructureMatcher when installed.",
    {"type": "object", "additionalProperties": False, "properties": {
        "id": _ID, "cif": _CIF, "include_retracted": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SIMILAR}}},
    forward("similar"), {"similar": _similar})


async def _add_tool(context, capability, arguments):
    # Fail fast before checking citations when there is no provenance at all.
    if not arguments.get("citations") and not arguments.get("external_ref"):
        raise ResourceValidationError("Give provenance: citations (Paper pages you can read) and/or external_ref "
                                      '({"database": "COD", "id": "1000041"}) for where this CIF comes from')
    return await cited("add", required=False)(context, capability, arguments)


ADD = Tool("add", "add_structure",
    "Add a crystal structure from CIF text (first data block, at most 200,000 characters). Provenance is required: "
    "citations of Paper pages you can read now (a quote is checked against the page), and/or external_ref for the "
    "database entry the CIF came from (recorded, not verified). Composition, space group, cell and site count are "
    "read from the CIF. Returns warnings, including likely duplicates (same formula and space group, close cell); "
    "the structure is added anyway.",
    {"type": "object", "required": ["cif"], "additionalProperties": False, "properties": {
        "cif": _CIF, "name": {"type": "string", "maxLength": 200, "description": "Defaults to formula and space group"},
        "citations": citations_schema(0), "external_ref": _EXTERNAL, "properties": _PROPERTIES,
        "note": {"type": "string", "maxLength": 2000, "description": "Why it was added, conditions, caveats"}}},
    _add_tool, {"add": _add})
ANNOTATE = Tool("annotate", "annotate_structure",
    "Revise a structure's annotations: merge properties (a null value removes one), replace the note, and/or "
    "add citations (checked like add_structure). The CIF itself never changes: to correct it, add the corrected "
    "structure and retract this one.",
    {"type": "object", "required": ["id"], "additionalProperties": False, "properties": {
        "id": _ID, "properties": _PROPERTIES, "note": {"type": "string", "maxLength": 2000},
        "citations": citations_schema(0)}},
    cited("annotate", required=False), {"annotate": _annotate})
RETRACT = Tool("retract", "retract_structure",
    "Withdraw a structure with a reason (e.g. duplicate, wrong CIF, superseded). It is kept for audit and hidden "
    "from searches unless include_retracted is set.",
    {"type": "object", "required": ["id", "reason"], "additionalProperties": False, "properties": {
        "id": _ID, "reason": {"type": "string", "minLength": 3, "maxLength": 500}}},
    forward("retract"), {"retract": _retract})


def register(registration):
    register_store(registration, card="structures", label="Structure database",
                   description="Crystal structures (CIF) with composition, symmetry and cell keys, written by Agents with provenance",
                   icon="atom", color="#5f8fb0", schema=SCHEMA, read_tools=[FIND, GET, SIMILAR],
                   curate_tools=[ADD, ANNOTATE, RETRACT],
                   user_actions={"ui_search": _find, "ui_detail": _detail, "ui_similar": _similar,
                                 "ui_add": _user_add, "ui_retract": _retract, "ui_summary": _summary},
                   default_size=(340, 250))
