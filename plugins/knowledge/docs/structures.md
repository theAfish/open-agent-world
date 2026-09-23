# Structure database (`knowledge.structures`)

A store of crystal structures (CIF) that Agents write explicitly, keyed by composition,
symmetry and cell so they can be queried by chemistry and space group and compared for
similarity. Like every knowledge store it connects only to Agents (**Read** and **Curate**)
and is never synchronised from a source.

## Tools

Read (and Curate):

| Tool | What it does |
| --- | --- |
| `find_structures` | Filter by `formula` (reduced-formula match: `Na4Cl4` finds NaCl), `chemsys` with `chemsys_match` `exact` (default) or `within` (every element of the structure is in the set, so subsystems match), `elements` (all present), `exclude_elements`, `spacegroup` (number or standard symbol), `crystal_system`, `nsites_min`/`nsites_max`, `text` (name, note, formula), `has_property`, `include_retracted`; paged with `limit` (max 100) and `offset`. Results carry no CIF and a provenance summary (citation count, stale count, external reference). |
| `get_structure` | One structure with all citations (and stale marks) and the external reference. `include_cif: true` returns the CIF text; otherwise only its length. |
| `find_similar_structures` | By stored `id` or by a `cif`; returns ranked candidates and the `method` used (see below). |
| `check_knowledge_provenance`, `read_knowledge_log` | Common to all stores. |

Curate only:

| Tool | What it does |
| --- | --- |
| `add_structure` | `cif` (≤ 200,000 characters, first data block), `name`, `citations` and/or `external_ref`, `properties`, `note`. Returns the stored keys, warnings and `possible_duplicates`. |
| `annotate_structure` | Merge `properties` (a `null` value removes one), replace `note`, add `citations`. The CIF never changes: add a corrected structure and retract the old one. |
| `retract_structure` | Soft delete with a `reason`. The record stays for audit and is hidden unless `include_retracted`. |

The workspace uses the user-only actions `ui_search`, `ui_detail`, `ui_similar`,
`ui_add`, `ui_retract`, `ui_summary` (and the common `ui_log`); Agents cannot reach them.

## Provenance

An Agent-added structure needs at least one of:

* **Paper citations**: `{paper, page, quote?}` checked with the Agent's own live
  `library.read` grant; a quote must appear on that page. Pinned to the Paper's
  fingerprint, so `check_knowledge_provenance` can mark them stale later.
* **An external reference**: `{database, id, url?}`, e.g. `{"database": "COD", "id": "9008678"}`
  or Materials Project, ICSD, OQMD, an OPTIMADE provider. It is **recorded as given, not
  verified**, and is returned with `"verified": false`; the workspace labels it the same way.

If neither is given, or a citation fails, or the CIF is invalid, nothing is written.
Structures the user adds in the workspace carry no citations (an external reference can
be named). Record keys are `structure:<id>`. Citations are never an access grant.

## Schema

`structures`: `id, name, cif, formula, reduced, anonymous, chemsys, nelements,
spacegroup_number, spacegroup_symbol, crystal_system, a, b, c, alpha, beta, gamma, volume,
nsites, sites_basis, volume_per_site, analysis, external_db, external_id, external_url,
properties (JSON), note, status, retracted_reason, created_at, created_by, updated_at, updated_by`.
`structure_elements(structure, element)` indexes elements for the element and `within` filters.

* `reduced`/`chemsys` use the shared formula keys (`chem.py`), e.g. `ClNa`, `Cl-Na`.
* `anonymous` is the prototype-like formula (amounts ascending: NaCl `AB`, SrTiO3 `ABC3`).
* `sites_basis` is `cell` when the cell was expanded, `asymmetric unit` otherwise.
* `analysis` is `cif` (tags as declared) or `pymatgen`.
* `properties`: at most 40 keys (`[A-Za-z][A-Za-z0-9_.-]{0,59}`, unit in the name, e.g.
  `band_gap_eV`); values are numbers, booleans or text ≤ 200 characters.

## CIF reading without pymatgen

`structures_cif.py` reads the first `data_` block of CIF 1.1 syntax (quoted values,
semicolon text fields, comments, loops) and takes:

* the cell from `_cell_length_*` / `_cell_angle_*` (uncertainties such as `5.4310(2)` are
  dropped; an absent angle is 90° with a warning), and the volume from the cell;
* the space group from `_space_group_IT_number` / `_symmetry_Int_Tables_number`, or the
  symbol from `_space_group_name_H-M_alt` / `_symmetry_space_group_name_H-M`, mapped
  through a table of the 230 standard short symbols (also spaced, full monoclinic
  `P 1 21/c 1`, old cubic `Fm3m`, and `:1`/`:H` suffixes). Non-standard settings (`P21/n`,
  `Pbnm`) keep their symbol but need the number tag. The crystal system follows the number;
* sites from the `_atom_site_` loop (`type_symbol` or `label` → element, fractional
  coordinates, occupancy, default 1). Cartesian-only sites are rejected;
* symmetry operations from `_space_group_symop_operation_xyz` or
  `_symmetry_equiv_pos_as_xyz`. With them (or in P1) every site is expanded and positions
  are deduplicated modulo 1 (tolerance 0.002 in fractional coordinates); several species on
  one position count as one site. **Without operations `nsites` counts the asymmetric
  unit**, `volume_per_site` is left empty and a warning says so;
* the formula from `_chemical_formula_sum` if it parses, else the site composition. A
  disagreement between them is reported as a warning.

Invalid CIFs are rejected with the reason: no data block, missing or non-numeric cell
length, impossible cell, no fractional atom sites, unknown element, loop rows of the wrong
width, unreadable symmetry operation, unterminated quotes or text fields, size limits
(200,000 characters, 5,000 listed sites, 384 operations, 100,000 site images = listed sites x
operations, 20,000 cell sites), cell lengths over 10,000 Å, and non-finite numbers (`1e999`, `nan`)
in the cell, coordinates or occupancies.

The pure path **does not detect symmetry**: the space group is taken as declared. It
does not reduce to the primitive cell or standardise settings.

## pymatgen (optional)

Install the extra (`pip install 'oaw-knowledge[structures]'`). When pymatgen imports:

* `CifParser` parses the structure (with its own space-group tables, so CIFs without
  operations are expanded too), and composition, `nsites` and the space group come from
  the parsed structure and `SpacegroupAnalyzer` (symprec 0.01). If pymatgen fails on a CIF
  the declared keys are kept with a warning; the pure reader still validates every CIF.
* `find_similar_structures` and the duplicate check use `StructureMatcher` (`fit` for the
  same formula, `fit_anonymous` for a prototype) on at most the 10 closest candidates, and only
  when both cells have at most 200 sites; the rest are ranked by cell distance and `method` says so.
  CIFs over 2,000 sites keep the declared keys (pymatgen parsing is skipped).

## Similarity and duplicates

Candidates share the reduced formula (the same compound, possibly another polymorph) or
the prototype (anonymous formula **and** space group, e.g. rock salt `AB` in Fm-3m). They
rank as *same formula and space group*, then *same formula*, then *same prototype*; within a
kind by `cell_distance`: `|ln(Vsite₁/Vsite₂)|` plus, within one space group, the mean
difference of cell lengths scaled by `V^(1/3)` and the angle difference / 180. 0 means
identical; below about 0.05 is likely the same structure. With pymatgen, matched
candidates come first. The result's `method` says which was used.

`add_structure` warns (and still adds) when an active structure has the same reduced
formula and space group and a cell distance ≤ 0.05 (or matches with pymatgen).

## Limits

CIF input ≤ 200,000 characters; `find_structures` ≤ 100 per page; similar ≤ 20 results
from ≤ 500 candidates; ≤ 10 StructureMatcher fits per call; ids 1 to 2^63-1; CIF text only on request.
All work runs under the host's node lock, so every step is bounded.
