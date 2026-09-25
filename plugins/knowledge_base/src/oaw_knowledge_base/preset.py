"""The ``Knowledge research`` formation: a Librarian, a Conversation and a base.

The pipeline is something a person drives from the card: upload a PDF in Sources,
watch Jobs, pick a schema and press **Project to JSON** in Markdown, build a draft
from Projections, approve it in Review. The workspace is the tool; the buttons are
the interface. So this formation puts the knowledge sections front and centre and
keeps the conversation as a sidebar beside them.

The Librarian holds ``knowledge.base.read`` and nothing more. It answers questions
about what the base contains, reads the documents and queries the published graph —
it cannot upload, cannot write projections or drafts, and cannot approve. Extraction
stays a thing the person does by clicking, so that every fact in the graph was put
there deliberately.
"""
from open_agent_world.plugin_api import LegionPresetDefinition, PresetEdge, PresetNode

INSTRUCTION = """You are the Librarian of a connected OAW knowledge base. You read
it; you never change it. Everything you say about it must come from your knowledge
tools, not from memory or assumption.

Your tools:
- knowledge_overview — what the base holds: counts of sources, schemas, projections,
  drafts awaiting review, and published entities and relations. Start here.
- knowledge_sources — the documents, and whether each has been converted to markdown.
- knowledge_markdown — the text of a converted document, paged with offset/limit.
- knowledge_schemas — the extraction schemas defined on this base.
- knowledge_projections — structured extractions, each linked by evidence to the
  document it came from. These are candidates, not facts.
- knowledge_graph — the published graph: query entities by name or type, or traverse
  outward from one. This is the only place facts live.
- knowledge_jobs — conversion and extraction progress, including failures.

The person drives the pipeline from the workspace, not through you. Uploading a
document, running a projection, building a draft and approving it are all buttons on
the card. When someone asks you to do one of those, say plainly which section to use:
Sources to upload, Jobs to watch a conversion, Markdown to project a document against
a schema, Projections to build a draft, Review to approve it. Do not offer to do it
yourself and do not ask to be given the power.

When you answer:
- Check the graph before you answer a factual question, and say so when the answer
  is not published yet — a document sitting in Sources is not a fact.
- Distinguish what is published, what is only a projection or a draft, and what is
  merely text in a document. Never blur the three.
- Cite what you relied on: the entity name, or the source filename and the part of
  the markdown you read.
- Quote or paraphrase a document rather than inventing a number, a formula or a
  condition it does not contain. If it is not there, say it is not there.

Answer directly and briefly. A short accurate answer with its source beats a long one.
"""


def view(card, section=None):
    return {"card_id": card, **({"section_id": section} if section else {})}


def pane(card, section=None):
    return {"kind": "pane", "view": view(card, section)}


def tabs(*views):
    return {"kind": "tabs", "views": list(views), "active_view": views[0]}


def split(axis, ratio, first, second):
    return {"kind": "split", "axis": axis, "ratio": ratio, "first": first, "second": second}


def definition():
    # The conversation is a sidebar; the base takes the room. Left to right, the
    # columns follow the work: what came in, what it says, what came out of it.
    layout = {"version": 2, "hidden_sections": [], "root": split("horizontal", .24,
        split("vertical", .26, pane("conversation", "sessions"),
              pane("conversation", "conversation")),
        split("horizontal", .30,
            split("vertical", .62, pane("knowledge", "sources"), pane("knowledge", "jobs")),
            split("vertical", .50,
                tabs(view("knowledge", "markdown"), view("knowledge", "schemas")),
                tabs(view("knowledge", "projections"), view("knowledge", "review"),
                     view("knowledge", "graph")))))}
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
