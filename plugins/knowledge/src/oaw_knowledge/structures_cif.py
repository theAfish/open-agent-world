"""CIF reading and structure keys for the structure database, without third-party dependencies.

``analyse(cif)`` reads the first data block and returns the keys the store indexes: cell,
volume, space group, crystal system, composition (``chem`` keys) and site count. With
symmetry operations in the CIF (or space group P1) the asymmetric unit is expanded and
positions are deduplicated modulo 1, so ``nsites`` counts the unit cell; otherwise it counts
the asymmetric unit and ``sites_basis`` says so. There is no space-group operation table
here: symmetry is taken as declared, never detected.

When pymatgen is importable (the ``structures`` extra), ``analyse`` also parses with
``CifParser`` and takes composition, site count and space group (``SpacegroupAnalyzer``)
from the parsed structure; ``match`` uses ``StructureMatcher``.
"""
from __future__ import annotations

import io
import math
import re
from fractions import Fraction

from . import chem

MAX_CIF_CHARS = 200_000
MAX_ATOM_SITES = 5_000
MAX_SYMOPS = 384
MAX_CELL_SITES = 20_000
MAX_IMAGES = 100_000  # listed sites x symmetry operations: bounds the expansion work
MAX_LENGTH = 10_000.0  # angstrom
MAX_PYMATGEN_SITES = 2_000  # larger cells keep the declared keys (parsing is slow under the node lock)
POSITION_TOLERANCE = 2e-3  # fractional; CIFs often round 1/3 to 0.333

try:  # Optional: better parsing, symmetry detection and structure matching.
    from pymatgen.analysis.structure_matcher import StructureMatcher
    from pymatgen.io.cif import CifParser
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    PYMATGEN = True
except Exception:  # noqa: BLE001 - any import failure means the pure path.
    PYMATGEN = False


class CifError(ValueError):
    pass


# ---- Space groups -----------------------------------------------------------------------------

# Short Hermann-Mauguin symbols of the 230 space groups in their standard settings, by number.
_SYMBOLS = """P1 P-1 P2 P2_1 C2 Pm Pc Cm Cc P2/m P2_1/m C2/m P2/c P2_1/c C2/c P222 P222_1 P2_12_12 P2_12_12_1
C222_1 C222 F222 I222 I2_12_12_1 Pmm2 Pmc2_1 Pcc2 Pma2 Pca2_1 Pnc2 Pmn2_1 Pba2 Pna2_1 Pnn2 Cmm2 Cmc2_1 Ccc2
Amm2 Aem2 Ama2 Aea2 Fmm2 Fdd2 Imm2 Iba2 Ima2 Pmmm Pnnn Pccm Pban Pmma Pnna Pmna Pcca Pbam Pccn Pbcm Pnnm Pmmn
Pbcn Pbca Pnma Cmcm Cmce Cmmm Cccm Cmme Ccce Fmmm Fddd Immm Ibam Ibca Imma P4 P4_1 P4_2 P4_3 I4 I4_1 P-4 I-4
P4/m P4_2/m P4/n P4_2/n I4/m I4_1/a P422 P42_12 P4_122 P4_12_12 P4_222 P4_22_12 P4_322 P4_32_12 I422 I4_122
P4mm P4bm P4_2cm P4_2nm P4cc P4nc P4_2mc P4_2bc I4mm I4cm I4_1md I4_1cd P-42m P-42c P-42_1m P-42_1c P-4m2
P-4c2 P-4b2 P-4n2 I-4m2 I-4c2 I-42m I-42d P4/mmm P4/mcc P4/nbm P4/nnc P4/mbm P4/mnc P4/nmm P4/ncc P4_2/mmc
P4_2/mcm P4_2/nbc P4_2/nnm P4_2/mbc P4_2/mnm P4_2/nmc P4_2/ncm I4/mmm I4/mcm I4_1/amd I4_1/acd P3 P3_1 P3_2 R3
P-3 R-3 P312 P321 P3_112 P3_121 P3_212 P3_221 R32 P3m1 P31m P3c1 P31c R3m R3c P-31m P-31c P-3m1 P-3c1 R-3m
R-3c P6 P6_1 P6_5 P6_2 P6_4 P6_3 P-6 P6/m P6_3/m P622 P6_122 P6_522 P6_222 P6_422 P6_322 P6mm P6cc P6_3cm
P6_3mc P-6m2 P-6c2 P-62m P-62c P6/mmm P6/mcc P6_3/mcm P6_3/mmc P23 F23 I23 P2_13 I2_13 Pm-3 Pn-3 Fm-3 Fd-3
Im-3 Pa-3 Ia-3 P432 P4_232 F432 F4_132 I432 P4_332 P4_132 I4_132 P-43m F-43m I-43m P-43n F-43c I-43d Pm-3m
Pn-3n Pm-3n Pn-3m Fm-3m Fm-3c Fd-3m Fd-3c Im-3m Ia-3d""".split()
assert len(_SYMBOLS) == 230
_OLD_NAMES = {"Abm2": 39, "Aba2": 41, "Cmca": 64, "Cmma": 67, "Ccca": 68}


def _compact(symbol: str) -> str:
    symbol = re.sub(r"[:(].*$", "", symbol)  # origin/axis choice suffixes: "Fd-3m:2", "R-3m:H"
    symbol = re.sub(r"[\s_]", "", symbol).casefold()
    full = re.fullmatch(r"([pcifabr])1(.+)1", symbol)  # monoclinic full symbols: "P 1 21/c 1"
    return full.group(1) + full.group(2) if full and full.group(2) not in {"", "-"} else symbol


_BY_SYMBOL = {_compact(symbol): number for number, symbol in enumerate(_SYMBOLS, start=1)}
_BY_SYMBOL.update({_compact(symbol): number for symbol, number in _OLD_NAMES.items()})


def spacegroup_number(symbol: str) -> int | None:
    """Number of a space-group symbol in its standard setting ("Fm-3m", "F m 3 m", "P 1 21/c 1")."""
    key = _compact(symbol)
    if key in _BY_SYMBOL:
        return _BY_SYMBOL[key]
    return _BY_SYMBOL.get(re.sub(r"([mnadb])3", r"\1-3", key))  # old cubic notation: Fm3m, Pa3, Ia3d


def spacegroup_symbol(number: int) -> str:
    return _SYMBOLS[number - 1]


def crystal_system(number: int | None) -> str | None:
    if not number:
        return None
    for last, name in ((2, "triclinic"), (15, "monoclinic"), (74, "orthorhombic"), (142, "tetragonal"),
                       (167, "trigonal"), (194, "hexagonal"), (230, "cubic")):
        if number <= last:
            return name
    return None


CRYSTAL_SYSTEMS = ("triclinic", "monoclinic", "orthorhombic", "tetragonal", "trigonal", "hexagonal", "cubic")


# ---- CIF syntax -------------------------------------------------------------------------------

_TOKEN = re.compile(r"""\s*(?:(\#.*)|'((?:[^']|'(?=\S))*)'(?=\s|$)|"((?:[^"]|"(?=\S))*)"(?=\s|$)|(\S+))""")


def _tokens(text: str):
    """(value, quoted, line) tokens of a CIF; semicolon text fields are one quoted token."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(";"):
            start, parts = index + 1, [line[1:]]
            index += 1
            while index < len(lines) and not lines[index].startswith(";"):
                parts.append(lines[index])
                index += 1
            if index == len(lines):
                raise CifError(f"Line {start}: text field opened with ';' is never closed (a line starting with ';' ends it)")
            yield "\n".join(parts).strip(), True, start
            line = lines[index][1:]
        position = 0
        while position < len(line):
            match = _TOKEN.match(line, position)
            if not match or match.end() == position:
                raise CifError(f"Line {index + 1}: unterminated quoted value")
            position = match.end()
            comment, single, double, bare = match.groups()
            if comment is not None:
                break
            if single is not None or double is not None:
                yield single if single is not None else double, True, index + 1
            elif bare is not None:
                if bare[0] in "'\"":
                    raise CifError(f"Line {index + 1}: unterminated quoted value {bare!r}")
                yield bare, False, index + 1
        index += 1


def _keyword(value: str) -> str | None:
    lower = value.casefold()
    for keyword in ("data_", "loop_", "save_", "global_", "stop_"):
        if lower.startswith(keyword):
            return keyword
    return None


def read_block(text: str) -> tuple[str, dict[str, str | None], list[tuple[list[str], list[list[str | None]]]]]:
    """Name, tags and loops of the first data block. Unknown ('?') and inapplicable ('.') values are None."""
    tokens = list(_tokens(text))
    position, name = 0, None
    while position < len(tokens):
        value, quoted, _ = tokens[position]
        position += 1
        if not quoted and _keyword(value) == "data_":
            name = value[5:]
            break
    if name is None:
        raise CifError("No data block: a CIF must contain a 'data_<name>' line before its tags")

    def item(token):
        value, quoted, _ = token
        return None if not quoted and value in {"?", "."} else value

    def ends_values(token):
        value, quoted, _ = token
        return not quoted and (value.startswith("_") or _keyword(value) is not None)

    tags: dict[str, str | None] = {}
    loops = []
    while position < len(tokens):
        value, quoted, line = tokens[position]
        keyword = None if quoted else _keyword(value)
        if keyword == "data_":
            break  # Only the first data block.
        if keyword == "loop_":
            position += 1
            names = []
            while position < len(tokens) and not tokens[position][1] and tokens[position][0].startswith("_"):
                names.append(tokens[position][0].casefold())
                position += 1
            if not names:
                raise CifError(f"Line {line}: loop_ without tag names")
            values = []
            while position < len(tokens) and not ends_values(tokens[position]):
                values.append(item(tokens[position]))
                position += 1
            if len(values) % len(names):
                raise CifError(f"Line {line}: loop with {names[0]} has {len(values)} values for {len(names)} "
                               "columns; every row needs one value per tag (use ? for unknown)")
            loops.append((names, [values[start:start + len(names)] for start in range(0, len(values), len(names))]))
        elif not quoted and value.startswith("_"):
            if position + 1 >= len(tokens) or ends_values(tokens[position + 1]):
                raise CifError(f"Line {line}: tag {value} has no value")
            tags[value.casefold()] = item(tokens[position + 1])
            position += 2
        elif keyword in {"save_", "global_", "stop_"}:
            position += 1
        else:
            raise CifError(f"Line {line}: value {value!r} without a tag")
    return name, tags, loops


_NUMBER = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(?:\(\d+\))?$")


def number(value: str | None) -> float | None:
    """A CIF number, ignoring a standard uncertainty ("5.4310(2)"); simple fractions ("1/4") too."""
    if value is None:
        return None
    value = value.strip()
    if len(value) > 64:
        return None
    match = _NUMBER.match(value)
    if match:
        result = float(match.group(1))
        return result if math.isfinite(result) else None  # 1e999 is not a usable number
    fraction = re.fullmatch(r"([+-]?\d{1,12})/(\d{1,12})", value)
    if fraction and int(fraction.group(2)):
        return int(fraction.group(1)) / int(fraction.group(2))
    return None


# ---- Symmetry operations ----------------------------------------------------------------------

def parse_symop(text: str) -> tuple[tuple[tuple[float, float, float], ...], tuple[float, float, float]]:
    """'-x+1/2, y, z' -> rotation rows and translation."""
    parts = re.sub(r"\s", "", text.casefold()).split(",")
    if len(parts) != 3 or not all(parts):
        raise CifError(f"Cannot read symmetry operation {text!r}: expected three comma-separated terms like 'x,-y,z+1/2'")
    rows, shift = [], []
    for part in parts:
        row, translation = [0.0, 0.0, 0.0], 0.0
        terms = re.findall(r"[+-]?[^+-]+", part)
        if "".join(terms) != part:
            raise CifError(f"Cannot read symmetry operation {text!r}")
        for term in terms:
            axis = re.search(r"[xyz]", term)
            if axis:
                factor = term[:axis.start()].rstrip("*") + term[axis.end():].lstrip("*")
                factor = {"": 1.0, "+": 1.0, "-": -1.0}.get(factor)
                if factor is None:
                    factor = number(term.replace(axis.group(), "").replace("*", "") or "1")
                if factor is None:
                    raise CifError(f"Cannot read symmetry operation {text!r}")
                row["xyz".index(axis.group())] += factor
            else:
                value = number(term)
                if value is None:
                    raise CifError(f"Cannot read symmetry operation {text!r}")
                translation += value
        rows.append(tuple(row))
        shift.append(translation)
    return tuple(rows), tuple(shift)


IDENTITY = (((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))


def _apply(op, point):
    rows, shift = op
    return tuple((sum(r * p for r, p in zip(row, point)) + t) % 1.0 for row, t in zip(rows, shift))


def _same(p, q, tolerance=POSITION_TOLERANCE) -> bool:
    return all(abs((a - b) - round(a - b)) < tolerance for a, b in zip(p, q))


_BUCKETS = round(1 / POSITION_TOLERANCE)
_NEIGHBOURS = [(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)]


class _Positions:
    """Distinct positions modulo 1 within POSITION_TOLERANCE, found in constant time.

    Two positions within the tolerance lie in the same or adjacent grid cells, so each
    position is registered in its 27 neighbouring cells and a lookup reads one cell.
    """

    def __init__(self):
        self.grid: dict[tuple, list[dict]] = {}
        self.entries: list[dict] = []

    def find_or_add(self, point) -> tuple[dict, bool]:
        cell = tuple(int(value * _BUCKETS) % _BUCKETS for value in point)
        for entry in self.grid.get(cell, ()):
            if _same(point, entry["xyz"]):
                return entry, False
        entry = {"xyz": point, "species": {}, "site": None}
        self.entries.append(entry)
        for offset in _NEIGHBOURS:
            key = tuple((c + o) % _BUCKETS for c, o in zip(cell, offset))
            self.grid.setdefault(key, []).append(entry)
        return entry, True


# ---- Analysis ---------------------------------------------------------------------------------

def _first(tags: dict, *names: str) -> str | None:
    for name in names:
        if tags.get(name) is not None:
            return tags[name]
    return None


def _column(loops, *names):
    for columns, rows in loops:
        for name in names:
            if name in columns:
                return columns, rows
    return None, None


def _element(symbol: str | None) -> str | None:
    match = re.match(r"([A-Z][a-z]?)", symbol or "")
    if not match:
        return None
    if match.group(1) in chem.ELEMENTS:
        return match.group(1)
    return match.group(1)[0] if match.group(1)[0] in chem.ELEMENTS else None


def volume(a, b, c, alpha, beta, gamma) -> float:
    ca, cb, cg = (math.cos(math.radians(angle)) for angle in (alpha, beta, gamma))
    value = 1 - ca * ca - cb * cb - cg * cg + 2 * ca * cb * cg
    return a * b * c * math.sqrt(value) if value > 0 else 0.0


def anonymous(amounts: dict[str, Fraction]) -> str:
    """Prototype-like formula: element names replaced by letters in order of increasing amount (SrTiO3 -> ABC3)."""
    reduced_amounts = chem.parse(chem.reduced(amounts))
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ordered = sorted(reduced_amounts.values())
    return "".join(f"{letters[i] if i < 26 else '?'}{chem._amount(value)}" for i, value in enumerate(ordered))


def analyse(cif: str) -> dict:
    """Keys of the first data block of ``cif``; raises CifError with a message saying what to fix."""
    if not isinstance(cif, str) or not cif.strip():
        raise CifError("cif is empty: pass the CIF file content as text")
    if len(cif) > MAX_CIF_CHARS:
        raise CifError(f"CIF is {len(cif)} characters; the limit is {MAX_CIF_CHARS}. Store one structure per CIF")
    block, tags, loops = read_block(cif)
    cell, warnings = {}, []
    for key, tag in (("a", "_cell_length_a"), ("b", "_cell_length_b"), ("c", "_cell_length_c"),
                     ("alpha", "_cell_angle_alpha"), ("beta", "_cell_angle_beta"), ("gamma", "_cell_angle_gamma")):
        value = number(tags.get(tag))
        if value is None and key in {"alpha", "beta", "gamma"} and tags.get(tag) is None:
            value = 90.0  # The CIF core default for an absent angle.
            warnings.append(f"{tag} is absent; 90 degrees assumed")
        if value is None:
            raise CifError(f"{tag} is missing or not a number; give the cell lengths a, b and c in angstrom")
        cell[key] = value
    if not all(0 < cell[k] <= MAX_LENGTH for k in "abc") or not all(0 < cell[k] < 180 for k in ("alpha", "beta", "gamma")):
        raise CifError(f"Cell lengths must be between 0 and {MAX_LENGTH:g} angstrom and angles between 0 and 180 degrees")
    cell_volume = volume(**cell)
    if not math.isfinite(cell_volume) or cell_volume <= 1e-6:
        raise CifError("The cell angles do not form a valid cell (zero or imaginary volume)")

    declared = number(_first(tags, "_space_group_it_number", "_symmetry_int_tables_number"))
    symbol = _first(tags, "_space_group_name_h-m_alt", "_symmetry_space_group_name_h-m", "_space_group_name_h-m")
    sg_number = int(declared) if declared and declared == int(declared) and 1 <= declared <= 230 else None
    if sg_number is None and symbol:
        sg_number = spacegroup_number(symbol)
    sg_symbol = spacegroup_symbol(sg_number) if sg_number else (re.sub(r"\s+", "", symbol) if symbol else None)

    columns, rows = _column(loops, "_atom_site_fract_x")
    if columns is None:
        if _column(loops, "_atom_site_cartn_x")[0]:
            raise CifError("Atom sites are given in Cartesian coordinates only; give _atom_site_fract_x/y/z")
        raise CifError("No atom sites: the CIF needs a loop_ with _atom_site_fract_x, _atom_site_fract_y, "
                       "_atom_site_fract_z and _atom_site_type_symbol or _atom_site_label")
    if len(rows) > MAX_ATOM_SITES:
        raise CifError(f"{len(rows)} atom sites; the limit is {MAX_ATOM_SITES}")
    col = {name: index for index, name in enumerate(columns)}
    for needed in ("_atom_site_fract_y", "_atom_site_fract_z"):
        if needed not in col:
            raise CifError(f"The atom site loop has no {needed}")
    if "_atom_site_type_symbol" not in col and "_atom_site_label" not in col:
        raise CifError("The atom site loop needs _atom_site_type_symbol or _atom_site_label to know each element")
    sites = []
    for row in rows:
        label = row[col["_atom_site_label"]] if "_atom_site_label" in col else None
        symbol_text = row[col["_atom_site_type_symbol"]] if "_atom_site_type_symbol" in col else None
        element = _element(symbol_text) or _element(label)
        if element is None:
            raise CifError(f"Atom site {label or symbol_text!r}: cannot tell its element; start the type symbol "
                           "or label with an element symbol (e.g. 'Fe2+', 'O1')")
        position = tuple(number(row[col[name]]) for name in ("_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z"))
        if any(value is None for value in position):
            raise CifError(f"Atom site {label or element!r}: fractional coordinates must be numbers")
        raw = row[col["_atom_site_occupancy"]] if "_atom_site_occupancy" in col else None
        occupancy = 1.0 if raw is None else number(raw)
        if occupancy is None:
            raise CifError(f"Atom site {label or element!r}: occupancy {raw!r} is not a number (use ? if unknown)")
        if not 0 < occupancy <= 1.0001:
            raise CifError(f"Atom site {label or element!r}: occupancy must be in (0, 1]")
        sites.append({"label": label or element, "element": element, "xyz": position, "occupancy": min(occupancy, 1.0)})

    ops_columns, op_rows = _column(loops, "_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz")
    ops = []
    if ops_columns is not None:
        index = next(i for i, name in enumerate(ops_columns)
                     if name in {"_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz"})
        if len(op_rows) > MAX_SYMOPS:
            raise CifError(f"{len(op_rows)} symmetry operations; the limit is {MAX_SYMOPS}")
        ops = [parse_symop(row[index]) for row in op_rows if row[index]]
    elif tags.get("_symmetry_equiv_pos_as_xyz") or tags.get("_space_group_symop_operation_xyz"):
        ops = [parse_symop(_first(tags, "_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz"))]
    if not ops and sg_number == 1:
        ops = [IDENTITY]

    images = len(sites) * max(len(ops), 1)
    if images > MAX_IMAGES:
        raise CifError(f"{len(sites)} listed sites x {len(ops)} symmetry operations = {images} images; the limit is "
                       f"{MAX_IMAGES}. List each site once (the asymmetric unit) and each operation once")
    # Expand the asymmetric unit when the operations are known; positions shared by
    # several sites (substitutional disorder) are one site with mixed occupancy.
    positions = _Positions()
    for index, site in enumerate(sites):
        for op in ops or [IDENTITY]:
            entry, _ = positions.find_or_add(_apply(op, site["xyz"]))
            if entry["site"] == index:
                continue  # The same image again within this site's orbit.
            entry["site"] = index
            entry["species"][site["element"]] = entry["species"].get(site["element"], 0.0) + site["occupancy"]
            if len(positions.entries) > MAX_CELL_SITES:
                raise CifError(f"More than {MAX_CELL_SITES} sites in the cell; the limit is {MAX_CELL_SITES}")
    positions = positions.entries
    amounts: dict[str, Fraction] = {}
    for entry in positions:
        for element, occupancy in entry["species"].items():
            amounts[element] = amounts.get(element, Fraction(0)) + Fraction(occupancy).limit_denominator(1000)
    site_keys = chem.keys(chem.reduced(amounts))

    formula_sum = tags.get("_chemical_formula_sum")
    declared_keys = chem.try_keys(formula_sum) if formula_sum else None
    if formula_sum and declared_keys is None:
        warnings.append(f"_chemical_formula_sum {formula_sum!r} is not a formula; the composition comes from the sites")
    if declared_keys and ops and declared_keys["reduced"] != site_keys["reduced"]:
        warnings.append(f"_chemical_formula_sum ({declared_keys['reduced']}) differs from the site composition "
                        f"({site_keys['reduced']}); the declared formula is used")
    keys = declared_keys or site_keys
    formula = re.sub(r"\s+", "", formula_sum) if declared_keys else _cell_formula(amounts)
    if not ops:
        warnings.append("No symmetry operations in the CIF: nsites counts the asymmetric unit, not the cell")
    nsites = len(positions)
    result = {
        "block": block, "formula": formula, **keys, "nelements": len(keys["elements"]),
        "anonymous": anonymous(chem.parse(keys["reduced"])),
        "spacegroup_number": sg_number, "spacegroup_symbol": sg_symbol, "crystal_system": crystal_system(sg_number),
        **{key: round(value, 6) for key, value in cell.items()}, "volume": round(cell_volume, 4),
        "nsites": nsites, "sites_basis": "cell" if ops else "asymmetric unit",
        "volume_per_site": round(cell_volume / nsites, 4) if ops and nsites else None,
        "analysis": "cif", "warnings": warnings,
    }
    if PYMATGEN and images <= MAX_PYMATGEN_SITES * 4 and nsites <= MAX_PYMATGEN_SITES:
        _pymatgen_keys(cif, result)
    elif PYMATGEN:
        warnings.append(f"Over {MAX_PYMATGEN_SITES} sites: keys come from CIF tags, not pymatgen")
    return result


def _cell_formula(amounts: dict[str, Fraction]) -> str:
    return "".join(f"{element}{chem._amount(amounts[element])}" for element in chem._order(amounts))


# ---- pymatgen (optional) ----------------------------------------------------------------------

def structure(cif: str):
    """The first structure in ``cif`` as a pymatgen Structure (pymatgen required)."""
    parser = CifParser(io.StringIO(cif))
    parse = getattr(parser, "parse_structures", None)
    structures = parse(primitive=False) if parse else parser.get_structures(primitive=False)
    if not structures:
        raise CifError("pymatgen found no structure in the CIF")
    return structures[0]


def _pymatgen_keys(cif: str, result: dict) -> None:
    """Overwrite declared keys with what pymatgen parses and spglib detects; keep the pure keys on failure."""
    try:
        parsed = structure(cif)
        amounts = {str(element): Fraction(amount).limit_denominator(1000)
                   for element, amount in parsed.composition.get_el_amt_dict().items()}
        keys = chem.keys(chem.reduced(amounts))
        analyzer = SpacegroupAnalyzer(parsed, symprec=0.01)
        number_ = analyzer.get_space_group_number()
        result.update(keys, nelements=len(keys["elements"]), anonymous=anonymous(amounts), formula=_cell_formula(amounts),
                      spacegroup_number=number_, spacegroup_symbol=analyzer.get_space_group_symbol(),
                      crystal_system=crystal_system(number_), nsites=len(parsed), sites_basis="cell",
                      volume_per_site=round(parsed.volume / len(parsed), 4), analysis="pymatgen")
        result["warnings"] = [w for w in result["warnings"] if "asymmetric unit" not in w]
    except Exception as error:  # noqa: BLE001 - pymatgen is a better reader, not a gate.
        result["warnings"].append(f"pymatgen could not analyse this CIF ({str(error)[:200]}); keys come from CIF tags")


def match(cif_a: str, cif_b: str, *, anonymous_match: bool = False) -> dict | None:
    """StructureMatcher result for two CIFs, or None without pymatgen or when either fails to parse."""
    if not PYMATGEN:
        return None
    try:
        first, second = structure(cif_a), structure(cif_b)
        matcher = StructureMatcher()
        fits = matcher.fit_anonymous(first, second) if anonymous_match else matcher.fit(first, second)
        rms = None
        if fits and not anonymous_match:
            distance = matcher.get_rms_dist(first, second)
            rms = round(distance[0], 4) if distance else None
        return {"match": bool(fits), "rms": rms}
    except Exception:  # noqa: BLE001
        return None


# ---- Pure-Python similarity -------------------------------------------------------------------

def cell_distance(first: dict, second: dict) -> float:
    """Dimensionless distance between two cells: relative volume per site, plus shape when the space group is shared.

    Volume per site is independent of the cell choice; shape (lengths scaled by V^(1/3) and angles)
    is compared only within one space group, where the conventional settings agree.
    """
    terms = []
    if first.get("volume_per_site") and second.get("volume_per_site"):
        terms.append(abs(math.log(first["volume_per_site"] / second["volume_per_site"])))
    if first.get("spacegroup_number") and first.get("spacegroup_number") == second.get("spacegroup_number"):
        scale_a, scale_b = first["volume"] ** (1 / 3), second["volume"] ** (1 / 3)
        lengths = sum(abs(first[k] / scale_a - second[k] / scale_b) for k in "abc") / 3
        angles = sum(abs(first[k] - second[k]) for k in ("alpha", "beta", "gamma")) / 180
        terms.append(lengths + angles)
    if not terms:
        terms.append(abs(math.log(first["volume"] / second["volume"])))
    return round(sum(terms), 5)

