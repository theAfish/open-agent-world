# Fact table (`knowledge.facts`)

A fact table holds material property records written explicitly by Agents (and by the
local user in the workspace). One record says:

> material **X** has property **P** = **value** (**unit**) under **conditions C**, obtained by
> **method M**, according to **Paper page Y**.

Nothing is synchronised from Papers. A record keeps the citations it was written with, pinned
to the cited Paper's fingerprint (PDF hash + active extraction version) at that moment.

## Connections

Only Agents connect to a fact table, with one of two relationships:

| Relationship | Tools |
| --- | --- |
| `knowledge.facts.read` (Read fact table) | `query_facts`, `list_fact_vocabulary`, `check_knowledge_provenance`, `read_knowledge_log` |
| `knowledge.facts.curate` (Curate fact table) | the Read tools plus `record_facts`, `revise_fact`, `retract_facts` |

The store is the tools' `store` parameter. Reading a fact table never grants access to the
Papers it cites: to open a cited page an Agent needs its own Library/Paper connection.

## Tools

| Tool | What it does |
| --- | --- |
| `query_facts` | Filtered search with each record's sources. Filters (AND): `material` (text in the name, or same composition if it parses as a formula), `formula` (same reduced formula), `chemsys` + `chemsys_mode` (`exact`, or `within` = only elements from the set), `elements` (all must be present), `property`, `min_value`/`max_value` + `unit`, `method` (substring), `conditions` (exact values), `paper` (cites that Paper), `ids`, `include_retracted`, `limit` (≤100, default 20), `offset`. Returns `facts`, `total`, `truncated`, `notes`. |
| `list_fact_vocabulary` | Totals, property names with counts and units, materials with counts, methods and condition keys, so an Agent reuses the names in use instead of guessing. `limit` ≤300 per list. |
| `record_facts` | Adds 1–50 facts. Every fact needs its own `citations` (Paper id, page, verbatim quote). All citations are verified before anything is written; any failure writes nothing and names the fact and citation (`facts.3.citations.0: …`). Returns ids, warnings (e.g. unparseable material, unknown unit, unusual unit for the property) and `possible_duplicates`. |
| `revise_fact` | Changes fields of one active fact; `note` (why) is required and logged with the changed fields. Changing content requires new `citations`, which replace the old ones (see *Revisions* below). Without a content change, citations are added, or replace the old ones with `replace_citations: true` (how a stale fact is refreshed after re-reading the page). |
| `retract_facts` | Soft-deletes 1–50 facts with a required `reason`. Retracted facts are hidden from queries and vocabulary counts unless `include_retracted`, and cannot be revised. |
| `check_knowledge_provenance` | (common) Marks citations whose Paper changed as `stale`; changes nothing else. |
| `read_knowledge_log` | (common) Latest writes: when, actor, op, record keys, note. |

### Fact fields

| Field | Notes |
| --- | --- |
| `material` | Required, as the paper names it (≤200 chars). |
| `formula` | Optional. If absent and `material` parses as a formula (`LiFePO₄`, `Mg3(PO4)2`, `CuSO4·5H2O`), it is used. Otherwise the fact is stored as text only and the result says so; names such as "LFP" are resolved by the Ontology store, not here. A given `formula` that does not parse is an error. |
| `property` | Required; normalised to snake_case (`Band gap` → `band_gap`, a few aliases such as `bandgap`). |
| `value`, `value_max` | Finite numbers; `value_max` makes a range and must be ≥ `value`. A value whose conversion to the base unit overflows is refused. |
| `value_text` | Qualitative value when there is no number (`metallic`). One of `value` / `value_text` is required. |
| `unit` | As written (≤40 chars); normalised for comparison (below). |
| `conditions` | Object of ≤20 scalars (string ≤200 chars, finite number, boolean or null; no objects or lists); keys normalised to snake_case (`{"temperature": "25 °C", "c_rate": "0.1C"}`). |
| `method`, `note` | Free text (≤120 / ≤500). |

## Units

The unit is stored as given, plus a canonical spelling and, when known, its dimension and
the value converted to the dimension's base unit. Canonicalisation understands Unicode
(`μ`, `Å`, `℃`, `⁻¹`), spaces and dots between factors, negative exponents and slashes, so
`mAh/g`, `mA h g⁻¹` and `mA·h/g` are one unit.

Unit prefixes are case-sensitive (`MeV` ≠ `meV`, `Mbar` ≠ `mbar`, `MPa` ≠ `mPa`); a spelling
that is not in the table is an unknown unit, never a guess. Known dimensions (base unit): energy (eV; meV, keV, Ry, Ha), energy per atom (eV/atom),
molar energy (kJ/mol; J/mol, kcal/mol), temperature (K; °C, °F), specific capacity (mAh/g;
Ah/kg, Ah/g), areal capacity (mAh/cm²), conductivity (S/cm; S/m, mS/cm, µS/cm), resistivity
(Ω·cm), pressure (GPa; MPa, kPa, Pa, bar, atm, Torr), length (Å; nm, pm, µm, bohr), volume
(Å³), density (g/cm³), voltage (V), current density (mA/cm²), specific current (mA/g),
gravimetric/volumetric energy (Wh/kg, Wh/L), power (W/kg), diffusivity (cm²/s), mobility,
thermal conductivity, Seebeck coefficient, surface area, time, frequency, wavenumber, µB, %.

A value range query requires `unit`. Facts in the same dimension are compared in the base
unit (500 meV matches 0.4–0.6 eV). Facts in another dimension, in an unknown unit or without
a number are never compared: the result's `notes` counts them per unit. A range query in an
unknown unit compares only facts with the same canonical spelling. Energy per particle and
molar energy are deliberately separate dimensions.

## Provenance

* Citations are checked with the writing Agent's own live grant
  (`context.agent_capability(capability, "library.read", paper)` and the Paper's `page_text`
  action). A quote must appear on that page (letters and digits compared, ignoring case,
  spacing and punctuation); a citation without a quote is stored at `page` level.
* Each record's sources are returned with `status` `fresh` or `stale`. Staleness is set only by
  `check_knowledge_provenance`; revising or retracting is always an explicit write.
* Facts the user enters in the workspace carry no citations and show as entered by the user.

## Revisions

Citations support the content they were verified with, never a later version of it. Content
fields are `material`, `formula`, `property`, `value`, `value_max`, `value_text`, `unit`,
`conditions` and `method` (the fact's `note` is a caveat and is not content).

* **Agent** (`revise_fact`): changing any content field requires citations for the revised
  fact; they replace all old citations. Without them the call fails and nothing changes.
  Old citations are listed in the log entry (`removed citation(s) paper#p2`), not kept as support.
* **User** (workspace): a content change removes the fact's citations, since the user cannot
  cite; the fact then shows as user-entered, and the log names the removed citations.
* Revising only `note`, or only adding citations, keeps the existing citations.

## Duplicates

`record_facts` flags (does not refuse) a fact whose material (reduced formula, or the text when
there is none), property and value (compared in the base unit) equal an earlier active fact
citing one of the same Papers. Retract the new one if it is the same measurement.

## Storage and limits

SQLite (`knowledge.sqlite3` in the card's storage) with tables `facts`, `fact_elements`
(element lookup for `chemsys`/`elements` filters), and the common `sources` and `log`.
Fact ids are 1..2⁶³−1. Batches ≤50 facts, ≤10 citations per fact, query pages ≤100, vocabulary lists ≤300.
Deleting the card removes its database; cited Papers are not affected.

## Workspace

A filterable table (material, property, value and unit, conditions, method, sources with stale
markers), a record detail with its sources, revise and retract forms, an "Add fact" form for
user entries, a vocabulary side panel (click to filter) and a Log tab. The preview shows the
number of facts, materials and properties. Workspace actions (`ui_query`, `ui_vocabulary`,
`ui_add`, `ui_revise`, `ui_retract`, `ui_log`) are unscoped and unreachable for Agents; they
ignore any provenance passed to them.
