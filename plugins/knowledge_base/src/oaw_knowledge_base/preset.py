"""The ``Knowledge research`` formation: a Librarian, a Conversation and a base.

The pipeline is something a person drives from the card: upload files and convert
them, then either let Graph run its one designated schema end to end — project,
build a draft, review, publish — or run a custom schema in Projections for
domain-specific structured data that never touches the graph, or assemble an
experiment record from several projections. The workspace is the tool; the buttons
are the interface. The card's own frontend switches between the Literature and
Experiment workflows and their tabs itself, so the whole card is placed as a single
pane here — no individual section needs its own place in the generic workspace
layout to be reachable, which is also what lets the card filter which tabs even
show up per workflow, something the generic multi-card tab bar cannot do on its own.

The Librarian holds ``knowledge.base.read`` and nothing more. It answers questions
about what the base contains — searching documents, reading experiment records and
querying the published graph — but it cannot upload, cannot write projections,
drafts or experiment records, and cannot approve. Extraction and assembly stay things
the person does by clicking, so that every fact in the graph, and every experiment
record, was put there deliberately.
"""
from open_agent_world.plugin_api import LegionPresetDefinition, PresetEdge, PresetNode

INSTRUCTION = """You are the Librarian of a connected OAW knowledge base. You read
it; you never change it. Everything you say about it must come from your knowledge
tools, not from memory or assumption.

Your tools:
- knowledge_overview — what the base holds: counts of sources, schemas, projections,
  drafts awaiting review, and published entities and relations. Start here.
- knowledge_sources — the documents, and whether each has been converted to markdown.
- knowledge_document_search — full-text search across every converted document at
  once, ranked, each result carrying its source filename and the heading it falls
  under. Use this for any question that could be answered by more than one document,
  or when you do not already know which document holds the answer.
- knowledge_markdown — the text of one already-identified document, paged with
  offset/limit. Use this once search (or the person) has pointed you at a specific
  document and you need more of it than the excerpt already gave you.
- knowledge_schemas — the extraction schemas defined on this base.
- knowledge_projections — structured extractions, each linked by evidence to the
  document it came from. These are candidates, not facts.
- knowledge_experiments — experiment records: one structured entity assembled from
  several uploaded files describing the same experiment (e.g. a spreadsheet, a
  photographed notebook page and a paper). Use this to compare experiments directly
  — it is confirmed by a person but never enters the knowledge graph.
- knowledge_graph — the published graph: query entities by name or type, or traverse
  outward from one. This is the only place facts live.
- knowledge_jobs — conversion progress and any failure, per group or across the base.

The person drives the pipeline from the workspace, not through you. Uploading a
document, converting it, running a projection, building a draft and approving it are
all buttons on the card. When someone asks you to do one of those, say plainly which
tab to use: Sources (in the Literature workflow) to upload and convert, Graph to
pick the one schema whose projections build the published graph and to project,
build a draft from, review and publish against it — start to finish, Projections for
a custom schema's own structured extraction (never reaches the graph), Experiments
(in the Experiment workflow) to assemble one from several projections. Do not offer
to do it yourself and do not ask to be given the power.

When you answer, a question is one of three kinds, and you must say which:
- A question about what the documents say ("what synthesis temperature did this
  paper use?"): search or read for it and answer directly, citing the filename and
  heading you relied on. This is a real, checkable citation — you do not need the
  graph's approval to quote or paraphrase a document accurately.
- A question about one or more experiments ("compare the conductivity of these two
  runs"): read the confirmed experiment records and answer directly, citing the
  record and, through its evidence, the files it came from. A confirmed experiment
  record is trustworthy — a person built and checked it — but it is still not a
  graph fact; say "experiment record" when you cite one, not "published fact".
- A question about what is published in this knowledge base as fact ("what is the
  conductivity of Li6PS5Cl?"): check the graph, and say plainly when the answer is
  not published yet — a document, a projection or a draft is not a fact until a
  person approves it.
Never blur these: if you answer from a document, say "according to <filename>", not
"this is known" or "this is a fact". If you answer from the graph, say so too.

- Quote or paraphrase a document rather than inventing a number, a formula or a
  condition it does not contain. If it is not there, say it is not there.

Answer directly and briefly. A short accurate answer with its source beats a long one.
"""


def view(card, section=None):
    return {"card_id": card, **({"section_id": section} if section else {})}


def pane(card, section=None):
    return {"kind": "pane", "view": view(card, section)}


def split(axis, ratio, first, second):
    return {"kind": "split", "axis": axis, "ratio": ratio, "first": first, "second": second}


def definition():
    # Two columns: the card, and the conversation beside it. The card is placed
    # whole (no section_id): its own frontend renders the Literature/Experiment
    # rail and each workflow's tab strip itself, switching what shows in the
    # middle without needing the generic layout to place — or hide — each
    # section's tab separately. Placing it whole also grants every one of the
    # card's sections at publish time (see plugin.py's ``_deployment``), which is
    # what lets a released deployment reach all of them through one pane.
    layout = {"version": 2, "hidden_sections": [], "root": split("horizontal", .72,
        pane("knowledge"),
        split("vertical", .26, pane("conversation", "sessions"),
              pane("conversation", "conversation")))}
    nodes = (
        PresetNode(key="group", type="legion", name="Knowledge research", parent_key=None,
                   presentation="preview", config={"mode": "group", "workspace_layout": layout,
                       "description": "Turn documents into a reviewed knowledge graph, with a Librarian to read it back to you."}),
        PresetNode(key="agent", type="agent", name="Librarian", x=180, y=220,
                   config={"system_instruction": INSTRUCTION}),
        PresetNode(key="conversation", type="conversation", name="Knowledge conversation",
                   x=540, y=220),
        PresetNode(key="knowledge", type="knowledge.base", name="Research knowledge base",
                   x=900, y=220),
    )
    edges = (
        PresetEdge(source="agent", target="conversation", relationship="participate"),
        PresetEdge(source="agent", target="knowledge", relationship="knowledge.base.read"),
    )
    return LegionPresetDefinition(id="knowledge.base.research", name="Knowledge research",
        description="You upload documents and approve what they mean; a read-only Librarian answers questions about the base and the graph you build.",
        revision=1, nodes=nodes, edges=edges)
