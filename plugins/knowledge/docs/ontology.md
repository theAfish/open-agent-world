# Ontology (`knowledge.ontology`)

An ontology holds entities (materials, phases, properties, methods, …), the names each one
goes by, and typed relations between them. It resolves how papers name things, so "LFP",
"lithium iron phosphate" and "LiFePO₄" all land on one entity. It also records knowledge that
spans papers: taxonomy, which method measures which property, and where papers disagree.

Agents write the ontology explicitly. Nothing is derived or synchronised from Papers.

Like every knowledge store, it connects only to Agents:

| Connection | Tools |
| --- | --- |
| **Read ontology** | `resolve_entity`, `find_entities`, `entity_neighbors`, `list_ontology_vocabulary`, `check_knowledge_provenance`, `read_knowledge_log` |
| **Curate ontology** | everything in Read, plus `add_entity`, `add_aliases`, `relate`, `merge_entities`, `retract_ontology_record`, `cite_ontology_record` |

Reading the ontology never lets an Agent open a cited Paper. To quote a Paper, the Agent needs
its own connection to that Paper or to a library that contains it.

## Model

- **Entity**: `id`, `kind`, `name`, optional `formula` with formula keys (`reduced`, `chemsys`),
  `description`, `status` (`active`, `merged` or `retracted`), `merged_into`, `reason`, and
  who created it and when. Its record key is `entity:<id>`.
- **Alias**: another name for an entity. The entity's name is stored as an alias too. Its
  record key is `alias:<id>`.
- **Relation**: `subject predicate object`, with an optional `note` (conditions, scope, how
  papers disagree) and a `status` (`active`, `merged` or `retracted`). Its record key is
  `relation:<id>`.

### Kinds

The suggested kinds are `material`, `phase`, `property`, `method`, `synthesis_route`,
`application`, `element` and `concept` (mechanisms, phenomena, claims). Any other lowercase
slug (2 to 40 letters, digits or underscores) is accepted, with a warning asking the Agent to
reuse existing kinds. `list_ontology_vocabulary` shows the kinds in use.

### Predicates

| Predicate | Meaning | Expected kinds | Citations |
| --- | --- | --- | --- |
| `is_a` | Taxonomy: subject is a kind or an instance of object | any | optional |
| `part_of` | Taxonomy: subject is a component or constituent of object | any | optional |
| `has_property` | subject shows the property | material/phase → property | required |
| `measured_by` | property is measured by the method | property → method | required |
| `synthesized_by` | material is made by the route | material/phase → synthesis_route | required |
| `used_for` | subject is used for the application | any → application | required |
| `doped_with` | material is doped or substituted with | material/phase → element/material | required |
| `polymorph_of` | same composition, different structure (symmetric) | phase/material | required |
| `derived_from` | subject is obtained from object | any | required |
| `contradicts` | papers disagree; cite both sides (symmetric) | any | required |
| `related_to` | weaker link; explain in the note (symmetric) | any | required |

Expected kinds are hints. A relation that does not match them is still written, and the
result has a `warnings` entry. Custom predicates (lowercase slugs) are allowed when no listed
predicate fits. They always need citations, they are flagged `custom_predicate` in results,
and `list_ontology_vocabulary` lists them under `custom_predicates` so Agents reuse them
instead of inventing synonyms.

## Names and resolution

Names are compared after normalisation: Unicode NFKC (so `LiFePO₄` becomes `LiFePO4`),
casefolding, and removal of whitespace, hyphens, dashes and underscores. `Sol-gel`, `sol gel`
and `SOL_GEL` are the same name. Brackets, commas and dots are kept.

A normalised name is unique within a kind among live entities. Two materials cannot both be
called "LFP", but a material and a phase can. When a name is taken, the error names the entity
that owns it and suggests `add_aliases` or `merge_entities`. Casefolding also means `Co` and
`CO` collide within a kind. Give such entities different kinds (`element` and `material`) or
a more specific alias.

`resolve_entity` ranks candidates and says why each one matched:

| Match | Score | When |
| --- | --- | --- |
| `name` / `alias` | 1.0 | the normalised text equals a name or alias |
| `formula` | 0.95 | the text parses as a formula with the same reduced composition (`FeLiPO4` finds `LiFePO4`) |
| `prefix` | 0.7 | a name starts with the text |
| `chemsys` | 0.5 | same chemical system, different composition (`Li2FeP2O7` suggests `LiFePO4`) |
| `contains` | 0.5 | a name contains the text (3 or more characters) |
| `fuzzy` | ≤ 0.6 | a close spelling (similarity ≥ 0.75). This runs only when there are fewer than `limit` candidates. It runs under the store's lock, so it is bounded: at most 20,000 names of similar length (±40%) are scanned, only names sharing a three-character sequence with the text are compared, names are compared on their first 64 characters, and at most 2,000 full comparisons are made |

Abbreviations such as "LFP" are not formulas. They resolve only through aliases, which is why
aliases matter.

## Provenance policy

Citations follow the shared rules. Each citation is `{paper, page, quote?}`. It is checked with
the writing Agent's own live `library.read` grant, and the quote must appear on that page. The
citation is pinned to the Paper's fingerprint (PDF hash plus the active extraction version).

- **Relations** must cite at least one Paper page. The exceptions are `is_a` and `part_of`,
  where citations are optional. Uncited relations are returned with `unsourced: true`, and the
  workspace flags them.
- **Entities** (`add_entity`) and **aliases** (`add_aliases`) may cite pages. This is optional
  but recommended. The best citation is the page where the name or abbreviation is introduced.
  Entity citations are stored on `entity:<id>`. Alias citations are stored on each new
  `alias:<id>`.
- If any citation fails, nothing is written.
- Names the user adds in the workspace carry no citations.

`check_knowledge_provenance` marks citations stale when a cited Paper changed. Nothing is
revised automatically. To add evidence to a record that is still right (another paper, a
source for an uncited taxonomy relation, or a re-read of a stale page), use
`cite_ontology_record`; earlier citations are kept. To correct a record, retract it and write
a corrected one with fresh citations.

## Tools

- **`resolve_entity`** `{text, kind?, limit?}` takes free text and returns ranked
  `candidates`. Each has a summary (`id`, `kind`, `name`, `formula`, `reduced`, `chemsys`, up
  to 10 `aliases`, `alias_count`), a `score`, and `matched` (up to 4 reasons). `limit` defaults
  to 10 and can be at most 50. Call it before `add_entity` and `relate`.
- **`find_entities`** `{kind?, text?, formula?, chemsys?, elements?, include_retracted?, limit?, offset?}`
  filters entities. `text` searches names, aliases and descriptions. `formula` matches by
  reduced composition. `chemsys` is an exact element set (`Fe-Li-O-P`, in any order). With
  `elements`, every listed element must be present. Results are paged (`limit` ≤ 200) and
  return `total` and `more`. Each entity has its count of active `relations`.
- **`entity_neighbors`** `{entity, predicates?, direction?, depth?, limit?}` returns:
  - the entity in full: all aliases with their sources, the entity's own sources, and the
    entities `merged_from` it
  - its active relations with sources and `unsourced` flags
  
  `direction` is `out` (the entity is the subject), `in` or `both`. `depth` is 1 or 2, and
  each relation carries its `hop`. At most `limit` relations are returned (default 50, at
  most 200). `truncated` says when the limit cut the result. A merged entity's id is followed
  to the kept entity, and the result then has `redirected_from`.
- **`list_ontology_vocabulary`** returns:
  - totals
  - suggested kinds and kinds in use, with counts
  - every predicate with its meaning, expected kinds, citation rule, symmetry, and use counts
    (`relations`, `unsourced`)
  - custom predicates in use
- **`add_entity`** `{kind, name, aliases?, formula?, description?, allow_same_formula?, citations?}`
  adds an entity. It refuses:
  - a name or alias that already names an entity of the same kind
  - a second entity of that kind with the same reduced formula, unless `allow_same_formula`
    is true (for example a distinct polymorph)

  A formula that cannot be parsed is kept as text without keys, and a warning explains it.
  Materials and phases without a formula also get a warning.
- **`add_aliases`** `{entity, aliases, citations?}` adds up to 50 names. Names the entity
  already has are reported in `already_known`. A name owned by another entity of the same kind
  is refused, and nothing from the call is written.
- **`relate`** `{subject, predicate, object, note?, citations?}` states a relation, following
  the citation policy above. It refuses:
  - self-relations
  - a relation that is already active (for symmetric predicates, in either direction); add
    evidence to it with `cite_ontology_record` instead
  - retracted entities

  If `subject` or `object` refers to a merged entity, the relation is written on the kept
  entity.
- **`merge_entities`** `{keep, merge, reason?}` merges up to 20 entities of the same kind
  into `keep`:
  - Their aliases move to `keep`, so old names resolve to it.
  - Their active relations move to `keep`.
  - A relation that becomes identical to an existing active one collapses. It gets status
    `merged` with `merged_into`, and its citations and note move to the surviving relation.
  - A relation that becomes a self-relation is retracted, with the reason recorded.
  - The merged entities keep their own citations and stay on record as `merged`, with
    `merged_into`. Entities merged into them earlier are re-pointed to `keep`, so every
    merged id is one hop from an active entity.
  - `keep` takes a formula or description it lacked from the merged entities.
  - Everything is logged as one `merge` entry.
- **`retract_ontology_record`** `{record, reason}` soft-deletes `entity:<id>`,
  `relation:<id>` or `alias:<id>`, with a reason. Retracting an entity also retracts its
  active relations and frees its names. You cannot retract an entity's own name alias:
  retract the entity instead. Retracted records stay in the store with their reason, and
  searches hide them unless `include_retracted` is set.

- **`cite_ontology_record`** `{record, citations, note?}` adds verified citations (at least
  one) to an active `entity:<id>`, `relation:<id>` or `alias:<id>`. Citations are checked like
  any other. If one fails, nothing is written. The note is logged.

Every write is recorded in the log with the actor (the Agent id, or `user`), the operation
(`add_entity`, `add_aliases`, `relate`, `merge`, `retract` or `cite`), the record keys and a note.
`read_knowledge_log` returns it.

## Workspace

The workspace has two tabs:

- **Entities**: search by name, alias or formula, and filter by kind. For the selected entity
  it shows:
  - its aliases with their sources
  - its formula keys and description
  - its outgoing and incoming relations with sources, with uncited ones flagged `unsourced`

  The user can add an alias, and can retract an entity, alias or relation after giving a
  reason.
- **Log**: the write log.

The preview shows entity and relation totals and entity counts by kind.

The workspace uses unscoped actions that Agents cannot reach: `ui_search`, `ui_entity`,
`ui_vocabulary`, `ui_add_alias`, `ui_retract` and the shared `ui_log`. These actions ignore
any provenance a caller tries to attach.

## Storage

The ontology uses the shared `knowledge.sqlite3` in the card's storage. On top of the common
`sources` and `log` tables, it adds three tables:

- `entities`
- `aliases`, with a partial unique index on `(normalized, kind)` over active aliases
- `relations`

## Limits

| What | Limit |
| --- | --- |
| Name or alias | 200 characters |
| Aliases per call | 50 |
| Description | 2,000 characters |
| Relation note | 1,000 characters |
| Retraction reason | 500 characters |
| Citations per record | 10 |
| `resolve_entity` candidates | 50 |
| `find_entities` page | 200 |
| `entity_neighbors` relations | 200 (depth 2 at most) |
| Entities per merge | 20 |
| Entity and record ids | 1 to 2⁶³−1 |
| Workspace relation list | 200 per direction |
