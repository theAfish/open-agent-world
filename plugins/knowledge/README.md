# Knowledge

Structured data that Agents write explicitly, each record with provenance the writing
Agent could verify. Four cards in the **Data** deck:

| Card | Holds | Details |
| --- | --- | --- |
| Fact table (`knowledge.facts`) | Material property records: material, property, value, unit, conditions, method | [docs/facts.md](docs/facts.md) |
| Vector store (`knowledge.vectors`) | Passages with embeddings for semantic and hybrid search | [docs/vectors.md](docs/vectors.md) |
| Ontology (`knowledge.ontology`) | Entities with aliases, and typed relations between them | [docs/ontology.md](docs/ontology.md) |
| Structure database (`knowledge.structures`) | Crystal structures (CIF) with composition, symmetry and cell keys | [docs/structures.md](docs/structures.md) |

## Model: written, not derived

A store is not a view of other cards and is never synchronised from them. Content
enters only when an Agent calls a curate tool (or the user edits it in the workspace),
and every write is logged with its actor, operation, records and note. Knowledge that
combines several Papers is authored work, so it has an author, not a source of truth.

Stores connect only to Agents:

| Connection | Grants |
| --- | --- |
| Read *store* | The store's query tools, `check_knowledge_provenance`, `read_knowledge_log` |
| Curate *store* | Read, plus the store's write tools |

There is no Paper-to-store edge. The relation between a record and a Paper is its
provenance, which is historical and grants nothing.

## Provenance

Agent writes cite Paper pages: `{"paper": <id>, "page": <n>, "quote": <verbatim text>}`.
Each citation is checked when it is written, with the **writing Agent's own grants**:
the host resolves that Agent's live `library.read` capability on the Paper (Plugin API
1.26 `agent_capability`) and the plugin reads the page through it. A Paper the Agent
cannot read, and one that does not exist, fail the same way. A quote must be on that
page (compared on letters and digits, so ligatures, subscripts and line breaks do not
matter); a citation without a quote is recorded as page-level. A write fails as a whole
if any citation fails.

The citation stores the Paper's fingerprint (PDF hash and active extraction version).
`check_knowledge_provenance` compares it with the Paper now and marks records **stale**
when the PDF or the active extraction changed. It changes nothing else: an Agent or the
user revises or retracts stale records explicitly. Papers the checking Agent cannot read
are reported as unavailable and left unchanged, because another reader may still see them.

What this does and does not guarantee:

- Information flows through Agents. An Agent that can read a Paper and curate a store can
  copy what it read into the store, just as it could into a Text card. The canvas shows
  that path (Paper → Agent → store); the write log shows who used it.
- Reading a store never opens the cited Paper. `cite` values are locators; following
  one needs a connection to that Paper.
- Verification proves the quoted words are on the cited page, not that the record
  interprets them correctly.
- Records the user enters in the workspace carry no citations and are shown as such.

## Lifecycle

Each store keeps one SQLite database in its node storage. Deleting the card deletes it
(journaled, retried after restart); cited Papers are not affected. Deleting a Paper does
not change records that cite it; their citations become unavailable to check. Templates
copy the card, not its records.
