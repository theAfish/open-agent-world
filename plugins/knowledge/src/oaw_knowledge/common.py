"""What every knowledge store shares: storage, the write log, verified provenance and registration.

A store is written only by Agents (through curate tools) and by the user (through its
workspace). Nothing is synchronised from sources: a record keeps the provenance it was
written with, pinned to the source fingerprint at that time.

Provenance is checked with the writing Agent's own grants. ``verify_citations`` asks the
host for the Agent's live ``library.read`` capability on each cited Paper and reads the
page through it; the plugin itself gains no access. A citation is therefore a claim the
Agent could check when it wrote the record. It is never a grant: reading a store does
not open the cited Paper.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, NodeLifecycleHandler, NodeLifecycleTransaction,
    NodeResourceAction, NodeResourceContext, NodeTypeDefinition, PermissionDeniedError,
    RelationshipDefinition, ResourceValidationError,
)

PREFIX = "knowledge"
FILE_NAME = "knowledge.sqlite3"
PAPER_READ = "library.read"  # The Library's kind for reading a Paper's page text.
MAX_CITATIONS = 10

COMMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    record TEXT NOT NULL,           -- the card's own record key, e.g. 'fact:12'
    paper TEXT NOT NULL,
    page INTEGER NOT NULL,
    quote TEXT,
    level TEXT NOT NULL,            -- 'quote' (text found on the page) or 'page'
    fingerprint TEXT NOT NULL,      -- the Paper's PDF hash + active extraction when verified
    verified_at TEXT NOT NULL,
    verified_by TEXT,
    status TEXT NOT NULL DEFAULT 'fresh',  -- fresh | stale (source changed since)
    checked_at TEXT
);
CREATE INDEX IF NOT EXISTS sources_record ON sources(record);
CREATE INDEX IF NOT EXISTS sources_paper ON sources(paper);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,            -- Agent id, or 'user'
    op TEXT NOT NULL,
    records TEXT NOT NULL,          -- JSON list of record keys
    note TEXT NOT NULL DEFAULT ''
);
"""

CITATION_SCHEMA = {"type": "object", "required": ["paper", "page"], "additionalProperties": False, "properties": {
    "paper": {"type": "string", "maxLength": 100, "description": "Paper id (from list_papers or search_library)"},
    "page": {"type": "integer", "minimum": 1, "description": "PDF page the support is on"},
    "quote": {"type": "string", "minLength": 8, "maxLength": 600,
              "description": "Verbatim text from that page supporting the record; it is checked against the page"}}}


def citations_schema(min_items: int = 1) -> dict:
    return {"type": "array", "minItems": min_items, "maxItems": MAX_CITATIONS, "items": CITATION_SCHEMA,
            "description": "Where this comes from. Each is checked against a Paper page you can read now."}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Storage ----------------------------------------------------------------------------------

class _Connection(sqlite3.Connection):
    """``with connect(...)`` commits (or rolls back) and then closes, unlike a plain sqlite3 connection."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect(context: NodeResourceContext, schema: str = "") -> sqlite3.Connection:
    """Open the store's database, creating the common and card tables if needed. Use it in ``with``."""
    context.storage_path.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(context.storage_path / FILE_NAME, timeout=5, factory=_Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(COMMON_SCHEMA + schema)
    return connection


def actor(context: NodeResourceContext) -> str:
    return context.actor_id or "user"


def log(connection: sqlite3.Connection, context: NodeResourceContext, op: str, records: list[str], note: str = "") -> None:
    connection.execute("INSERT INTO log (at, actor, op, records, note) VALUES (?, ?, ?, ?, ?)",
                       (now(), actor(context), op, json.dumps(records[:1000]), str(note)[:500]))


def add_sources(connection: sqlite3.Connection, context: NodeResourceContext, record: str, sources: list[dict]) -> None:
    """Store provenance that verify_citations produced (passed to the resource action as ``_sources``).

    Only an Agent call carries verified provenance. The desktop can reach the same resource
    action without a capability (actor_id is None); whatever it passes is not stored.
    """
    if context.actor_id is None:
        return
    connection.executemany(
        "INSERT INTO sources (record, paper, page, quote, level, fingerprint, verified_at, verified_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(record, s["paper"], s["page"], s.get("quote"), s["level"], s["fingerprint"], s["verified_at"],
          context.actor_id) for s in sources])


def sources_for(connection: sqlite3.Connection, records: list[str]) -> dict[str, list[dict]]:
    """Provenance per record key, as returned to Agents and the UI."""
    found: dict[str, list[dict]] = {record: [] for record in records}
    for start in range(0, len(records), 500):
        chunk = records[start:start + 500]
        rows = connection.execute(
            f"SELECT record, paper, page, quote, level, status, verified_at FROM sources"
            f" WHERE record IN ({','.join('?' * len(chunk))}) ORDER BY id", chunk)
        for row in rows:
            found[row["record"]].append({"paper": row["paper"], "page": row["page"], "quote": row["quote"],
                "level": row["level"], "status": row["status"], "cite": f"{row['paper']}#p{row['page']}"})
    return found


def trusted_sources(arguments: dict) -> list[dict]:
    """The verified provenance a capability handler attached. add_sources ignores it for user calls."""
    sources = arguments.get("_sources") or []
    if not isinstance(sources, list):
        raise ResourceValidationError("Invalid provenance")
    return sources


# ---- Provenance -------------------------------------------------------------------------------

def _plain(text: str) -> str:
    # PDF text differs from what a model quotes in ligatures, subscripts, hyphenation,
    # spacing and punctuation. Compare letters and digits only.
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", "", text)


async def verify_citations(context, capability, citations: list[dict] | None, *, required: bool = True,
                           pages: dict | None = None) -> list[dict]:
    """Check each citation with the calling Agent's own live Paper grant; return provenance to store.

    Raises ResourceValidationError naming the first citation that fails, so the Agent can fix it.
    A batch tool may pass one ``pages`` dict to all its calls so each page is read once.
    """
    citations = list(citations or [])
    if required and not citations:
        raise ResourceValidationError("Cite at least one Paper page (paper, page and a verbatim quote)")
    if len(citations) > MAX_CITATIONS:
        raise ResourceValidationError(f"At most {MAX_CITATIONS} citations per record")
    verified, pages = [], {} if pages is None else pages
    for index, citation in enumerate(citations):
        paper, page, quote = citation.get("paper"), citation.get("page"), citation.get("quote")
        if not isinstance(paper, str) or not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ResourceValidationError(f"citations.{index}: give a paper id and a page number")
        if (paper, page) not in pages:
            try:
                grant = await context.agent_capability(capability, PAPER_READ, paper)
                pages[paper, page] = await context.node_resource_action(grant, "page_text", {"page": page})
            except PermissionDeniedError:
                raise ResourceValidationError(
                    f"citations.{index}: you cannot read Paper {paper!r}. Cite only Papers connected to you "
                    "(directly or through a library) and read the page before citing it") from None
            except ResourceValidationError as error:
                raise ResourceValidationError(f"citations.{index}: {error}") from None
        text = pages[paper, page]
        level = "page"
        if quote:
            needle = _plain(quote)
            if len(needle) < 6 or needle not in _plain(text["text"]):
                raise ResourceValidationError(
                    f"citations.{index}: the quote is not on page {page} of {paper!r}. Copy the words exactly "
                    "from read_paper for that page, or cite the page it is on")
            level = "quote"
        verified.append({"paper": paper, "page": page, "quote": quote, "level": level,
                         "fingerprint": text["fingerprint"], "verified_at": now()})
    return verified


async def check_provenance(context, capability, arguments: dict) -> dict:
    """Compare each cited Paper's current fingerprint with the one recorded; mark changed ones stale.

    Only definitive results are stored (fresh/stale). A Paper this Agent cannot read, or that
    no longer exists, is reported as unavailable and left unchanged: another reader may see it.
    """
    listing = await context.node_resource_action(capability, "source_papers", {"papers": arguments.get("papers")})
    report, marks = {"fresh": 0, "stale": 0, "unavailable": 0}, []
    details = []
    for item in listing["papers"]:
        try:
            grant = await context.agent_capability(capability, PAPER_READ, item["paper"])
            current = (await context.node_resource_action(grant, "page_text", {"page": 1}))["fingerprint"]
        except (PermissionDeniedError, ResourceValidationError):
            report["unavailable"] += item["records"]
            details.append({"paper": item["paper"], "status": "unavailable", "records": item["records"]})
            continue
        for fingerprint, count in item["fingerprints"].items():
            status = "fresh" if fingerprint == current else "stale"
            report[status] += count
            marks.append({"paper": item["paper"], "fingerprint": fingerprint, "status": status})
        details.append({"paper": item["paper"], "records": item["records"],
                        "status": "stale" if any(m["status"] == "stale" for m in marks if m["paper"] == item["paper"]) else "fresh"})
    if marks:
        await context.node_resource_action(capability, "mark_sources", {"marks": marks})
    return {"summary": report, "papers": details[:200],
            "hint": "Stale records cite a Paper whose PDF or active extraction changed after they were written. "
                    "Re-read the cited page and revise or retract them; nothing is changed automatically."}


def source_papers(schema: str):
    def handler(context: NodeResourceContext, arguments: dict) -> dict:
        papers = arguments.get("papers")
        with connect(context, schema) as connection:
            where, values = "", []
            if papers:
                where, values = f" WHERE paper IN ({','.join('?' * len(papers[:500]))})", papers[:500]
            grouped: dict[str, dict] = {}
            for row in connection.execute(
                    f"SELECT paper, fingerprint, COUNT(*) AS n FROM sources{where} GROUP BY paper, fingerprint", values):
                entry = grouped.setdefault(row["paper"], {"paper": row["paper"], "records": 0, "fingerprints": {}})
                entry["records"] += row["n"]
                entry["fingerprints"][row["fingerprint"]] = row["n"]
        return {"papers": list(grouped.values())}
    return handler


def mark_sources(schema: str):
    def handler(context: NodeResourceContext, arguments: dict) -> dict:
        marks = arguments.get("marks") or []
        with connect(context, schema) as connection:
            for mark in marks:
                if mark.get("status") not in {"fresh", "stale"}:
                    raise ResourceValidationError("Invalid provenance status")
                connection.execute("UPDATE sources SET status = ?, checked_at = ? WHERE paper = ? AND fingerprint = ?",
                                   (mark["status"], now(), mark["paper"], mark["fingerprint"]))
        return {"marked": len(marks)}
    return handler


def read_log(schema: str):
    def handler(context: NodeResourceContext, arguments: dict) -> dict:
        limit = min(max(int(arguments.get("limit") or 50), 1), 500)
        with connect(context, schema) as connection:
            rows = connection.execute("SELECT at, actor, op, records, note FROM log ORDER BY id DESC LIMIT ?", (limit,))
            return {"entries": [{**dict(row), "records": json.loads(row["records"])} for row in rows]}
    return handler


# ---- Lifecycle --------------------------------------------------------------------------------

def remove_files(directory: Path) -> None:
    # Only the store's own files; never a recursive delete.
    for suffix in ("-wal", "-shm", "-journal", ""):
        (directory / (FILE_NAME + suffix)).unlink(missing_ok=True)
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


class _Remove(NodeLifecycleTransaction):
    def __init__(self, directory: Path):
        self.directory = directory

    async def finalize(self):
        # Journaled by the host and retried after restart; the graph commit already happened.
        remove_files(self.directory)


class StoreLifecycle(NodeLifecycleHandler):
    async def prepare_delete(self, context, node):
        return _Remove(context.resources.node_storage_path(node.id))


# ---- Registration -----------------------------------------------------------------------------

Handler = Callable[[Any, Any, dict], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    """One Agent tool of a store. ``kind`` is the suffix after 'knowledge.<card>.'."""
    kind: str
    tool_name: str
    description: str
    input_schema: dict
    handler: Handler
    # Resource actions this tool calls through node_resource_action; they get the tool's kind.
    actions: dict[str, Callable[[NodeResourceContext, dict], dict]] = field(default_factory=dict)


PROVENANCE_TOOL = ("Check the provenance of records in this knowledge store: compares every cited Paper's current "
    "PDF and active extraction with the version the record was verified against. Records citing a changed "
    "Paper are marked stale; nothing else changes. Optionally limit to some Paper ids.")
LOG_TOOL = "List the latest writes to this knowledge store: when, by whom (Agent id or user), what and why."


def register_store(registration, *, card: str, label: str, description: str, icon: str, color: str,
                   schema: str, read_tools: list[Tool], curate_tools: list[Tool],
                   user_actions: dict[str, Callable[[NodeResourceContext, dict], dict]] | None = None,
                   default_size=(340, 250), deletion_warning: str | None = None, config_model=None) -> None:
    """Register a store node type with Read and Curate connections from Agents.

    Every store gets check_knowledge_provenance and read_knowledge_log on Read. Tool names
    are shared across stores (identical contracts); the target parameter picks the store.
    """
    type_id = f"{PREFIX}.{card}"
    common = [
        Tool("provenance", "check_knowledge_provenance", PROVENANCE_TOOL,
             {"type": "object", "additionalProperties": False, "properties": {
                 "papers": {"type": "array", "maxItems": 500, "items": {"type": "string"}}}},
             check_provenance, {"source_papers": source_papers(schema), "mark_sources": mark_sources(schema)}),
        Tool("log", "read_knowledge_log", LOG_TOOL,
             {"type": "object", "additionalProperties": False, "properties": {
                 "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
             _log_tool, {"log": read_log(schema)}),
    ]
    actions: dict[str, NodeResourceAction] = {}
    for tool in (*common, *read_tools, *curate_tools):
        kind = f"{type_id}.{tool.kind}"
        registration.register_capability(CapabilityDefinition(kind=kind, tool_name=tool.tool_name,
            description=tool.description, input_schema=tool.input_schema, target_parameter="store"), tool.handler)
        for name, handler in tool.actions.items():
            if name in actions:
                raise ValueError(f"{type_id}: resource action {name!r} is used by two tools")
            actions[name] = NodeResourceAction(handler, capability_kind=kind)
    # Unscoped actions serve the workspace (the local user); Agents cannot reach them.
    actions["ui_log"] = NodeResourceAction(read_log(schema))
    for name, handler in (user_actions or {}).items():
        if name in actions:
            raise ValueError(f"{type_id}: resource action {name!r} is registered twice")
        actions[name] = NodeResourceAction(handler)
    from pydantic import BaseModel

    class StoreConfig(BaseModel):
        description: str = ""

    registration.register_node_type(NodeTypeDefinition(
        id=type_id, label=label, description=description, icon=icon, color=color,
        deck_id="data", deck_label="Data", deck_icon="database",
        default_name=label, default_size=default_size, default_status="available",
        statuses=frozenset({"available"}), config_model=config_model or StoreConfig,
        traits=frozenset({"knowledge.store"}), templateable=True, lifecycle=StoreLifecycle(),
        resource_actions=actions,
        deletion_warning=deletion_warning or f"Deleting this {label.lower()} permanently removes its records, provenance and write log. Cited Papers are not affected.",
        frontend={"preview": f"{card}-preview", "body": card, "workspace": card},
        surfaces={"preview": True, "inspector": True, "workspace": True}))
    reads = tuple(CapabilityGrantDefinition(kind=f"{type_id}.{tool.kind}") for tool in (*common, *read_tools))
    curates = tuple(CapabilityGrantDefinition(kind=f"{type_id}.{tool.kind}") for tool in curate_tools)
    registration.register_relationship(RelationshipDefinition(id=f"{type_id}.read", label=f"Read {label.lower()}",
        short_label="read", description="Query this store and see each record's provenance. Cited Papers need their own connection.",
        source_traits=frozenset({"core.agent"}), target_types=frozenset({type_id}), capabilities=reads, templateable=True))
    registration.register_relationship(RelationshipDefinition(id=f"{type_id}.curate", label=f"Curate {label.lower()}",
        short_label="curate", description="Read, add and revise records. Every write is logged and must cite Papers this Agent can read.",
        source_traits=frozenset({"core.agent"}), target_types=frozenset({type_id}),
        capabilities=(*reads, *curates), templateable=True))


async def _log_tool(context, capability, arguments):
    return await context.node_resource_action(capability, "log", arguments)


def forward(action: str) -> Handler:
    """A tool handler that only passes its arguments to one resource action (no citations)."""
    async def handler(context, capability, arguments):
        arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
        return await context.node_resource_action(capability, action, arguments)
    return handler


def cited(action: str, *, field_name: str = "citations", required: bool = True) -> Handler:
    """A tool handler that verifies ``arguments[field_name]`` and passes verified provenance as ``_sources``."""
    async def handler(context, capability, arguments):
        arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
        sources = await verify_citations(context, capability, arguments.pop(field_name, None), required=required)
        return await context.node_resource_action(capability, action, {**arguments, "_sources": sources})
    return handler
