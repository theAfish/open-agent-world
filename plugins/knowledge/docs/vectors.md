# Vector store (`knowledge.vectors`)

A vector store holds passages an Agent chose to keep for retrieval: the page text of
Papers it ingested, and its own cited notes. Search is lexical (BM25), dense (embedding
cosine) or hybrid (both, fused). The Literature library already searches its own members
with BM25. A vector store is different in two ways: an Agent decides what goes in, and it can
hold Papers from several libraries.

Like every knowledge store, it connects only to Agents:

| Connection | Tools |
| --- | --- |
| **Read vector store** | `semantic_search`, `vector_store_status`, `check_knowledge_provenance`, `read_knowledge_log` |
| **Curate vector store** | everything in Read, plus `ingest_papers`, `add_passage`, `remove_passages`, `reembed_passages` |

Reading the store never lets an Agent open a cited Paper. To quote a Paper, the Agent needs
its own connection to that Paper or to a library that contains it.

## Tools

- **`semantic_search`** `{query, mode?, papers?, kind?, limit?, max_per_paper?}` searches the
  passages. `mode` is `hybrid` (the default when vectors of the configured model exist),
  `dense` or `lexical`. `papers` keeps only passages from those Papers or notes that cite them.
  `kind` is `page` or `agent_note`. `limit` defaults to 8 and can be at most 50.
  `max_per_paper` caps how many results come from one Paper. Each result carries:
  - `passage` (id), `kind`, `paper`, `page`, `heading`, and `cite` (`<paper>#p<page>`)
  - `text`, the whole passage (at most 1600 characters)
  - `score` and a `scores` breakdown: `lexical_rank`/`bm25` and `dense_rank`/`cosine`
  - `stale`, which is true when a cited Paper changed since the passage was written
  - for notes, `sources` (every citation)

  The result's `mode` says which method actually ran. If an Agent asks for `hybrid` or `dense`
  and there is no embedding service, no vector of the configured model, or the query cannot be
  embedded, the store runs a lexical search. The result then says `mode: "lexical"` and gives
  a `warning` that explains why.
- **`vector_store_status`** shows:
  - the passage count, split into page passages and notes, and how many have vectors
  - a row for each Paper: page passages, notes, pages covered, passages with vectors, stale
    records
  - the vectors of each model and whether that model is searched
  - the search mode and the embedding endpoint
  - the last 10 embedding jobs
  - hints about what to do next
- **`ingest_papers`** `{papers: [1–50 ids], force?, note?}` adds Papers. For each Paper, the
  handler takes the Agent's own `library.read` grant
  (`context.agent_capability(capability, "library.read", paper)`) and reads every page through
  the Library's `page_text` action. The page text goes straight into the store and never
  passes through the model. What happens next:
  - The text is cut into passages of about 1000–1500 characters. Cuts fall at sentence ends,
    consecutive passages overlap by about 150 characters, and each passage is tagged with its
    page.
  - The new passages replace that Paper's earlier page passages. If the fingerprint and the
    passage count are unchanged, the Paper is reported `unchanged` and skipped, unless
    `force` is set.
  - Each passage stores one source: its page, level `quote` (the first 160 characters of
    the passage, which is the page's own text), and the Paper's fingerprint (PDF hash plus
    active extraction).
  - If an embedding service is configured, a background job computes the vectors and the
    call returns that job at once. Passages can be searched lexically straight away.

  Failures are reported per Paper, and nothing from a failed Paper is stored. A Paper fails
  when the Agent cannot read it, when it has no PDF or no text layer, when it has more than
  1000 pages, when it is deleted or changes while being read, or when adding it would pass the passage limit.
- **`add_passage`** `{text (20–4000 chars), title?, citations, note?}` adds an Agent note,
  such as a summary or synthesis. At least one citation is required. Each citation is checked
  with the Agent's own Paper grant, and any quote must appear on the cited page
  (`common.verify_citations`). The note's `paper`/`page` columns hold its first citation, and
  every citation is stored as a source. The title is searched too and is embedded together
  with the text.
- **`remove_passages`** `{passages?, papers?, note?}` removes passages by id, removes all
  ingested page passages of the given Papers, or both. Notes are removed only by id. Their
  sources and vectors are removed with them.
- **`reembed_passages`** `{drop_other_models?}` starts a job that embeds every passage that has
  no vector from the configured model. Use it after a failed or interrupted job, or after the
  model changed. `drop_other_models` deletes vectors from any other model. It is refused while
  another embedding job is running.

The workspace (the local user) uses three unscoped actions that Agents cannot reach:
`ui_status`, `ui_search` and `ui_remove`. The common `ui_log` action shows the write log.
Removals by the user are logged with actor `user`.

## Configuration

The embedding service is configured through environment variables on the host. They are
read on every call.

| Variable | Meaning |
| --- | --- |
| `OAW_EMBEDDING_URL` | An OpenAI-compatible server. A bare base URL (`http://host:8000`) gets `/v1/embeddings` appended, and a URL ending in `/v1` gets `/embeddings` appended. A URL that already ends in `/embeddings` is used as is. |
| `OAW_EMBEDDING_MODEL` | The model name sent in each request and recorded with every vector. Both the URL and the model must be set. |
| `OAW_EMBEDDING_API_KEY` | Optional. Sent as `Authorization: Bearer …`. |
| `OAW_EMBEDDING_BATCH` | Texts per request. The default is 64 and the maximum is 512. |
| `OAW_EMBEDDING_TIMEOUT` | Seconds per request. The default is 60. |

Requests are `POST {"model", "input": [texts]}` and must answer with
`{"data": [{"index", "embedding"}]}`. Vectors are normalised to unit length and stored as
float32 blobs.

When no service is configured, the store runs in **lexical mode**: ingesting, notes, removal and
BM25 search all work, and every status and search result says `mode: "lexical"`.

## Models

Each vector records its model and dimension. Search compares only vectors of the configured
model that have the query's dimension, so vectors from different models are never mixed.
After a model change, `vector_store_status` lists the old vectors as `searched: false`, and
search stays lexical until some passages have vectors of the new model.
`reembed_passages` (with `drop_other_models: true` to reclaim space) moves the store to the
new model.

## Search

- **Lexical:** SQLite FTS5 over the passage text and heading (`porter unicode61
  remove_diacritics 2`). The query's words are OR-ed together (at most 32 words) and ranked
  by BM25. The top 200 are kept as candidates.
- **Dense:** brute-force cosine (a dot product of unit vectors, using `math.sumprod`) over
  every vector that passes the filters. The top 200 are kept.
- **Hybrid:** Reciprocal Rank Fusion of both lists, `score = Σ 1/(60 + rank)`.

The per-Paper cap is applied after fusion. If the query vector's dimension differs from the
stored vectors of the configured model, for example because the model's output size changed,
the search runs lexically and a warning says so.

Agents embed their query in the tool handler, outside the host's node lock. The workspace
search cannot do that, because user actions are synchronous and hold the global node lock.
So it embeds its query under a hard overall deadline of 3 seconds (`QUERY_DEADLINE`, with a
1-second connect timeout). The request runs in a daemon thread and the action waits no longer
than the deadline. If the deadline passes, the search runs lexically with a warning. The
trade-off is that a slow embedding service gives the user lexical results but never stalls the
world for more than about 3 seconds.

## Jobs

Only the embedding calls run in the host background job (`NodeResourceContext.background`).
The job's commit writes vectors only for passages that still exist. Passage ids are
`AUTOINCREMENT` and never reused, so a late job cannot attach a vector to a new passage. If a
batch fails, the vectors that already finished are kept, the job is marked `failed` with the
error, and `reembed_passages` finishes the rest.

No network call ever runs under the node lock. On a host without background jobs
(`context.background is None`), the passages are stored and searchable lexically, and the job is
recorded as `failed` with a message saying that `reembed_passages` finishes once background jobs
are available.

Jobs do not survive a restart. On the next action, a job left `running` that this process did
not start (or whose commit was lost) is marked `interrupted`.

## Provenance

Nothing changes automatically when a Paper changes. `check_knowledge_provenance` compares each
cited Paper's current fingerprint with the recorded one and marks changed sources stale.
Search results then show `stale: true`, and status counts stale records for each Paper. To
bring a Paper up to date, a curating Agent re-ingests it with `ingest_papers`, which replaces
its page passages because the fingerprint differs, or removes the passages.

## Limits

- At most 20,000 passages per store (`MAX_PASSAGES`). Brute-force cosine over that many
  1536-dimension vectors takes a fraction of a second.
- At most 50 Papers per `ingest_papers` call and 1000 pages per Paper.
- At most 50 search results, and 1600 characters of text per result.
- Notes are 20–4000 characters with at most 10 citations.
- Storage is `knowledge.sqlite3` in the node's storage directory. Tables: `passages`,
  `passages_fts`, `embeddings` and `jobs`, plus the common `sources` and `log`.
