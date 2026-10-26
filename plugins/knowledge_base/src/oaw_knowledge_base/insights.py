"""Insights: meta-knowledge extracted from published experiment records.

The third pair of prompt/save actions after knowledge drafts and experiment records, and
the one place where the Experiment workflow reaches the knowledge graph. The flow is
the card's usual one: ``insight_prompt`` hands a model (or a connected Lab assistant)
everything about a **scope** of published records — one experiment schema, several,
or every one that has published records — the schemas, deterministic statistics the
plugin computed over them (and, across schemas, over the specimens they share by
name), and the records themselves as tables; ``insight_save`` stores what came back
as **draft insights** evidence-linked to the records they cite, and a person
publishes each one. Publishing is the only write to the graph: the insight, the
records it cites and the fields it is about become nodes and edges in the
``experiment`` layer of the same ``oaw_kg_*`` tables a published knowledge draft
writes, so the Lab assistant's ``knowledge_graph`` tool and the map work over both.
Records read from literature sources (the Records tab) share the record table, so
insights over them take the same path and land in the same layer.
Unpublishing removes exactly what publishing added.

``insight_review`` has no tool name on any transport: an agent proposes and edits
drafts, a person decides what the base asserts.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import insight_stats
from .actions import (
    MAX_MARKDOWN_CHARS, Request, _actor, _base, _bounded, _client, _schema_kind, _scope,
)
from .errors import KnowledgeError
from .graph_store import EXPERIMENT_LAYER
from .insight_store import KINDS, ROLES

RECORD_LIMIT = insight_stats.MAX_RECORDS
# Reference nodes exist only to be cited; see ``_prune`` for when they go.
REFERENCE_TYPES = ("experiment_record", "field")
Kind = Literal["anomaly", "rule", "trend", "correlation", "threshold", "note"]

INSIGHT_INSTRUCTION = (
    "You are reading every published record of one or more experiment schemas, together "
    "with statistics the system computed over them: per schema, each field's distribution, "
    "outliers beyond |z| > 2.5 and pairwise correlations with |r| >= 0.6; and, when several "
    "schemas are present, correlations between fields of different schemas over the specimens "
    "both describe (records matched by name). Propose insights a researcher would want "
    "recorded about this set as a whole. Each insight has a kind — anomaly (one record stands "
    "out), rule (a condition that holds across records), trend (a value moves with another or "
    "over the sequence), correlation (two fields vary together), threshold (a value past which "
    "something changes) or note (anything else worth keeping) — a short title, a one- or "
    "two-sentence statement, the field names it is about (as \"<schema name>.<field>\" when "
    "more than one schema is present), and evidence: the record_ids it rests on, each with a "
    "role (supports, contradicts, observed_in). Ground every number in the statistics or the "
    "tables; never invent a value. Prefer a few well-supported insights over many weak ones, "
    "and skip anything already published. Reply with exactly this JSON shape: {\"insights\": "
    "[{\"kind\": <kind>, \"title\": <short title>, \"statement\": <statement>, \"fields\": "
    "[<field names>], \"confidence\": <0..1 or null>, \"properties\": {<the metric values "
    "behind the statement>}, \"evidence\": [{\"record_id\": <id from a table>, \"role\": "
    "<role>}], \"note\": <optional: what was unclear>}]}.")


# ---------------------------------------------------------------- scope


class Scoped(Request):
    """The schemas an insight call is about: one, several, or — neither given —
    every schema of ``schema_kind`` (experiment unless said otherwise) with
    published records in the group scope."""
    schema_id: str | None = None
    schema_ids: list[str] = Field(default_factory=list, max_length=50)
    group_id: str | None = None
    all_groups: bool = False
    # Which section's schemas an unnamed scope takes: the Literature Records tab
    # passes "literature"; the Experiment Insights tab and agents get "experiment".
    schema_kind: Literal["literature", "experiment"] = "experiment"


def _published_records(kb, schema, scope):
    return kb.oaw_experiments.list(group_id=str(scope) if scope else None,
                                   schema_id=str(schema.id), status="published", limit=RECORD_LIMIT)


def _resolve(kb, request, group):
    """``(scope, [(schema, published records), ...])`` for a request, in a stable order.

    Named schemas may be of either kind and may be empty of records; an unnamed
    scope takes every schema of the request's kind that has at least one.
    """
    scope = _scope(group, request.all_groups)
    wanted = list(dict.fromkeys(request.schema_ids + ([request.schema_id] if request.schema_id else [])))
    if wanted:
        schemas = [kb.schemas.require(identifier) for identifier in wanted]
    else:
        schemas = [item for item in kb.schemas.list(limit=200) if _schema_kind(item) == request.schema_kind]
    groups = [(schema, _published_records(kb, schema, scope)) for schema in schemas]
    if not wanted:
        groups = [item for item in groups if item[1]]
    groups.sort(key=lambda item: (item[0].name.casefold(), str(item[0].id)))
    return scope, groups


def _known_fields(groups):
    """Every field name an insight over these schemas may claim to be about.

    ``"<schema name>.<field>"`` always, so a cross-schema insight can say which
    schema's conductivity it means; a bare name when it resolves to exactly one
    schema (see ``_resolve_field``). Names that exist in the data but not the
    definition count too, prefixed — or bare for a single schema.
    """
    schemas = [schema for schema, _ in groups]
    names = set()
    for schema, records in groups:
        fields = set(insight_stats.field_names(schema.definition))
        for record in records:
            fields.update(insight_stats.flatten(record.get("data")))
        names.update(f"{schema.name}.{field}" for field in fields)
        names.update(field for field in fields if _resolve_field(field, schemas) is not None)
    return names


def _resolve_field(name, schemas):
    """``(schema, field)`` a stored field name refers to, or ``None``.

    A ``"<schema name>.<field>"`` prefix wins; a bare name resolves when exactly
    one of the insight's schemas declares it.
    """
    for schema in schemas:
        prefix = f"{schema.name}."
        if name.startswith(prefix):
            return schema, name[len(prefix):]
    owners = [schema for schema in schemas if name in insight_stats.field_names(schema.definition)]
    if len(owners) == 1:
        return owners[0], name
    if len(schemas) == 1:
        return schemas[0], name
    return None


def _statistics(groups):
    per_schema = [{"schema_id": str(schema.id), "schema_name": schema.name,
                   "record_count": len(records),
                   "statistics": insight_stats.compute(schema.definition, records)}
                  for schema, records in groups]
    cross = insight_stats.cross_schema([
        {"schema_id": str(schema.id), "schema_name": schema.name,
         "definition": schema.definition, "records": records} for schema, records in groups])
    return per_schema, cross


def _schema_json(schema):
    return {"schema_id": str(schema.id), "schema_name": schema.name, "schema_version": schema.version,
            "definition": schema.definition, "field_descriptions": schema.field_descriptions}


def _insight_json(kb, insight, *, evidence=False):
    payload = dict(insight)
    if evidence:
        links = []
        for link in kb.oaw_insights.evidence_for(insight["id"]):
            record = kb.oaw_experiments.get(link["record_id"])
            links.append({"record_id": link["record_id"], "role": link["role"],
                          "name": record["name"] if record else None,
                          "schema_id": record["schema_id"] if record else None,
                          # A cited record that was unpublished or deleted since: the
                          # insight still stands as written, but the person should know.
                          "stale": record is None or record["status"] != "published"})
        payload["evidence"] = links
    return payload


def _in_scope(items, scope):
    """One group's insights plus those extracted across every group, or everything."""
    if scope is None:
        return items
    return [item for item in items if item["group_id"] in (str(scope), None)]


# ---------------------------------------------------------------- prompt


class InsightPrompt(Scoped):
    limit: int = Field(default=60_000, ge=500, le=MAX_MARKDOWN_CHARS)
    offset: int = Field(default=0, ge=0)


def insight_prompt(context, arguments):
    """Everything needed to propose insights over a scope of published records: each
    schema with its statistics, the cross-schema statistics, the titles already
    published (so they are not proposed again) and the records as one markdown table
    per schema, windowed like the other prompts. With exactly one schema the
    top-level ``schema_id``/``definition``/``statistics`` are set too."""
    request = InsightPrompt.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    scope, groups = _resolve(kb, request, group)
    if not any(records for _, records in groups):
        raise KnowledgeError("No published records in this scope yet; publish some records "
                             "before extracting insights")
    text = "\n".join(
        f"# Schema: {schema.name} (schema_id={schema.id}, {len(records)} published records)\n\n"
        + insight_stats.render_table(schema.definition, records)
        for schema, records in groups if records)
    window = text[request.offset:request.offset + request.limit]
    per_schema, cross = _statistics(groups)
    schema_ids = sorted(str(schema.id) for schema, _ in groups)
    published = _in_scope(kb.oaw_insights.list(status="published", limit=500), scope)
    published = [item for item in published if set(item["schema_ids"]) & set(schema_ids)]
    payload = {
        "schemas": [{**_schema_json(schema), "record_count": len(records)} for schema, records in groups],
        "schema_ids": schema_ids, "statistics_by_schema": per_schema, "cross_schema": cross,
        "group_id": str(scope) if scope else None, "kinds": list(KINDS), "roles": list(ROLES),
        "instruction": INSIGHT_INSTRUCTION,
        "existing": [{"id": item["id"], "kind": item["kind"], "title": item["title"],
                      "schema_ids": item["schema_ids"]} for item in published],
        "record_count": sum(len(records) for _, records in groups),
        "total_characters": len(text), "offset": request.offset, "markdown": window,
        "has_more": request.offset + len(window) < len(text),
    }
    if len(groups) == 1:
        payload.update(_schema_json(groups[0][0]))
        payload["statistics"] = per_schema[0]["statistics"]
    return _bounded(payload)


# ---------------------------------------------------------------- save


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str
    role: Literal["supports", "contradicts", "observed_in"] = "supports"


class InsightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    title: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1, max_length=4000)
    fields: list[str] = Field(default_factory=list, max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(min_length=1, max_length=RECORD_LIMIT)
    note: str | None = Field(default=None, max_length=4000)


class InsightSave(Scoped):
    insights: list[InsightInput] = Field(min_length=1, max_length=200)
    # A re-extraction replaces this scope's earlier drafts; published insights stay.
    replace_drafts: bool = False
    run_id: str | None = Field(default=None, max_length=64)


def _check_evidence(evidence, published_ids, title):
    missing = [link.record_id for link in evidence if link.record_id not in published_ids]
    if missing:
        raise KnowledgeError(
            f'Insight "{title}" cites record_ids that are not published records of these '
            f'schemas in this scope: {", ".join(missing[:5])}')


def insight_save(context, arguments):
    """Store proposed insights as drafts, each evidence-linked to published records.

    Evidence is checked strictly — an insight that cites a record outside the scope's
    schemas and group is refused, because the link is what makes it checkable.
    Fields the schemas and the records do not know are dropped and reported, not
    refused: they are descriptive, and a misspelt one should not lose the batch.
    """
    request = InsightSave.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    scope, groups = _resolve(kb, request, group)
    if not groups:
        raise KnowledgeError("No experiment schema with published records in this scope")
    schema_ids = sorted(str(schema.id) for schema, _ in groups)
    published_ids = {record["id"] for _, records in groups for record in records}
    known = _known_fields(groups)
    for item in request.insights:
        _check_evidence(item.evidence, published_ids, item.title)

    if request.replace_drafts:
        kb.oaw_insights.delete_drafts(schema_ids=schema_ids, group_id=scope)
    saved, warnings = [], []
    for item in request.insights:
        fields = [name for name in item.fields if name in known]
        dropped = [name for name in item.fields if name not in known]
        if dropped:
            warnings.append(f'"{item.title}": unknown fields dropped: {", ".join(dropped)}')
        insight = kb.oaw_insights.create(
            schema_ids=schema_ids, group_id=scope, kind=item.kind, title=item.title,
            statement=item.statement, fields=fields, confidence=item.confidence,
            properties=item.properties, note=item.note, run_id=request.run_id,
            created_by=_actor(context))
        kb.oaw_insights.set_evidence(insight["id"], [link.model_dump() for link in item.evidence])
        saved.append(_insight_json(kb, insight, evidence=True))
    return _bounded({"insights": saved, "schema_ids": schema_ids, "warnings": warnings})


# ---------------------------------------------------------------- read


class Insights(Scoped):
    operation: Literal["list", "get", "stats"] = "list"
    insight_id: str | None = None
    kind: Kind | None = None
    status: Literal["draft", "published"] | None = None
    # Free text matched against title, statement, fields and note.
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


def insights(context, arguments):
    """List or read insights, or compute the statistics behind them. Read-only:
    editing a draft is insight_update; publishing one is a person's click."""
    request = Insights.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    if request.operation == "list":
        scope = _scope(group, request.all_groups)
        wanted = set(request.schema_ids) | ({request.schema_id} if request.schema_id else set())
        items = kb.oaw_insights.list(
            schema_id=request.schema_id if len(wanted) == 1 else None, kind=request.kind,
            status=request.status, text=request.query, limit=500)
        if len(wanted) > 1:
            items = [item for item in items if set(item["schema_ids"]) & wanted]
        items = _in_scope(items, scope)[:request.limit]
        return _bounded({"insights": [_insight_json(kb, item, evidence=True) for item in items]})
    if request.operation == "stats":
        scope, groups = _resolve(kb, request, group)
        per_schema, cross = _statistics(groups)
        payload = {"schema_ids": sorted(str(schema.id) for schema, _ in groups),
                   "group_id": str(scope) if scope else None,
                   "statistics_by_schema": per_schema, "cross_schema": cross}
        if len(groups) == 1:
            payload.update({"schema_id": str(groups[0][0].id), "schema_name": groups[0][0].name,
                            "statistics": per_schema[0]["statistics"]})
        return _bounded(payload)
    if not request.insight_id:
        raise KnowledgeError("insight_id is required")
    insight = kb.oaw_insights.require(request.insight_id)
    return _bounded({"insight": _insight_json(kb, insight, evidence=True)})


# ---------------------------------------------------------------- update


class InsightUpdate(Request):
    insight_id: str
    kind: Kind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    fields: list[str] | None = Field(default=None, max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)
    clear_confidence: bool = False
    evidence: list[EvidenceInput] | None = Field(default=None, min_length=1, max_length=RECORD_LIMIT)
    note: str | None = Field(default=None, max_length=4000)
    expected_revision: int | None = None


def _insight_groups(kb, insight):
    schemas = [kb.schemas.require(identifier) for identifier in insight["schema_ids"]]
    return [(schema, _published_records(kb, schema, insight["group_id"])) for schema in schemas]


def insight_update(context, arguments):
    """Edit one draft insight — by hand in the form, or by an agent asked to in
    conversation. A published insight is in the graph and is not edited in place:
    unpublish it first."""
    request = InsightUpdate.model_validate(arguments)
    kb = _client(context)
    current = kb.oaw_insights.require(request.insight_id)
    if current["status"] == "published":
        raise KnowledgeError("This insight is published; unpublish it before editing")
    groups = _insight_groups(kb, current)
    fields = request.fields
    if fields is not None:
        known = _known_fields(groups)
        fields = [name for name in fields if name in known]
    if request.evidence is not None:
        _check_evidence(request.evidence,
                        {record["id"] for _, records in groups for record in records}, current["title"])
    insight = kb.oaw_insights.update(
        request.insight_id, kind=request.kind, title=request.title, statement=request.statement,
        fields=fields, note=request.note, expected_revision=request.expected_revision,
        confidence=None if request.clear_confidence else (
            request.confidence if request.confidence is not None else ...))
    if request.evidence is not None:
        kb.oaw_insights.set_evidence(insight["id"], [link.model_dump() for link in request.evidence])
    return _bounded({"insight": _insight_json(kb, insight, evidence=True)})


# ---------------------------------------------------------------- review


class InsightReview(Request):
    operation: Literal["publish", "unpublish", "delete"]
    insight_id: str | None = None
    # Several at once — "Publish all" — under one confirmation.
    insight_ids: list[str] = Field(default_factory=list, max_length=200)
    expected_revision: int | None = None


def _entity_key(prefix, value):
    """The uuid ``kb.graph.extract`` derives from a non-UUID string id, so the
    elements publishing created can be found again without storing their ids."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:{value}")


def _graph_payload(kb, insight, schemas, evidence):
    """The insight, the fields it is about and the records it cites, as the
    ``{entities, relations}`` shape ``kb.graph.extract`` upserts.

    Ids are stable strings, so republishing the same insight — or a second insight
    citing the same record — upserts the node instead of duplicating it.
    """
    insight_key = f"insight:{insight['id']}"
    reserved = {"id", "type", "name", "label", "category", "properties", "insight_id",
                "statement", "confidence", "schema_id", "schema_ids", "fields"}
    extra = {key: value for key, value in insight["properties"].items() if key not in reserved}
    entities = [{"id": insight_key, "type": insight["kind"], "name": insight["title"],
                 "insight_id": insight["id"], "statement": insight["statement"],
                 "confidence": insight["confidence"], "schema_id": insight["schema_id"],
                 "schema_ids": insight["schema_ids"], "fields": insight["fields"], **extra}]
    relations = []
    seen = set()
    for name in insight["fields"]:
        resolved = _resolve_field(name, schemas)
        if resolved is None:
            continue
        schema, field = resolved
        key = f"field:{schema.id}:{field}"
        if key in seen:
            continue
        seen.add(key)
        entities.append({"id": key, "type": "field", "name": f"{schema.name} · {field}",
                         "schema_id": str(schema.id), "field": field})
        relations.append({"source": insight_key, "target": key, "type": "involves"})
    for link in evidence:
        record = kb.oaw_experiments.require(link["record_id"])
        key = f"record:{record['id']}"
        entities.append({"id": key, "type": "experiment_record", "name": record["name"],
                         "record_id": record["id"], "schema_id": record["schema_id"],
                         "bundle_id": record["bundle_id"], "group_id": record["group_id"]})
        relations.append({"source": insight_key, "target": key,
                          "type": {"supports": "supported_by", "contradicts": "contradicted_by",
                                   "observed_in": "observed_in"}[link["role"]]})
    return {"entities": entities, "relations": relations}


def _prune(store):
    """Drop record and field nodes no published insight cites any more."""
    orphans = store.orphans(layer=EXPERIMENT_LAYER, types=REFERENCE_TYPES)
    store.remove(orphans)
    return len(orphans)


def insight_review(context, arguments):
    """Publish, unpublish or delete insights: the person's judgment on what the
    extraction proposed. Publishing asks for confirmation and is the only thing that
    writes the Experiment workflow into the knowledge graph; unpublishing removes
    exactly what it wrote."""
    request = InsightReview.model_validate(arguments)
    identifiers = list(dict.fromkeys(request.insight_ids + ([request.insight_id] if request.insight_id else [])))
    if not identifiers:
        raise KnowledgeError("insight_id or insight_ids is required")
    if request.expected_revision is not None and len(identifiers) > 1:
        raise KnowledgeError("expected_revision applies to a single insight_id")
    kb = _client(context)
    store, graph = kb.oaw_insights, kb.oaw_graph_store
    current = [store.require(identifier) for identifier in identifiers]

    if request.operation == "delete":
        for insight in current:
            if insight["status"] == "published":
                raise KnowledgeError(f'Unpublish "{insight["title"]}" before deleting it')
        for insight in current:
            store.delete(insight["id"])
        return _bounded({"deleted": identifiers})

    if request.operation == "unpublish":
        changed = []
        for insight in current:
            if insight["status"] != "published":
                raise KnowledgeError(f'"{insight["title"]}" is not published')
        for insight in current:
            graph.remove([_entity_key("insight", insight["id"])])
            changed.append(store.update(insight["id"], status="draft",
                                        expected_revision=request.expected_revision))
        pruned = _prune(graph)
        return _bounded({"insights": [_insight_json(kb, item, evidence=True) for item in changed],
                         "graph": {"removed": len(changed), "pruned": pruned}})

    evidence = {}
    for insight in current:
        if insight["status"] == "published":
            raise KnowledgeError(f'"{insight["title"]}" is already published')
        links = store.evidence_for(insight["id"])
        stale = [link["record_id"] for link in links
                 if (kb.oaw_experiments.get(link["record_id"]) or {}).get("status") != "published"]
        if not links or stale:
            raise KnowledgeError(f'"{insight["title"]}" cites no published record; fix its '
                                 "evidence before publishing")
        evidence[insight["id"]] = links
    if not context.confirmed:
        titles = ", ".join(f'"{insight["title"]}"' for insight in current[:3])
        more = f" and {len(current) - 3} more" if len(current) > 3 else ""
        return {"status": "confirmation_required",
                "reasons": ["Publishing writes this insight, the records it cites and the fields "
                            "it is about into the knowledge graph as what this knowledge base "
                            "asserts about its experiments."],
                "message": f"Publish {titles}{more}?"}
    published, entities, relations = [], 0, 0
    for insight in current:
        schemas = [kb.schemas.require(identifier) for identifier in insight["schema_ids"]]
        result = kb.graph.extract(_graph_payload(kb, insight, schemas, evidence[insight["id"]]),
                                  extractor=lambda payload: payload)
        graph.tag([entity.id for entity in result.entities],
                  [relation.id for relation in result.relations],
                  group_id=insight["group_id"], layer=EXPERIMENT_LAYER)
        entities += len(result.entities)
        relations += len(result.relations)
        published.append(store.update(insight["id"], status="published",
                                      expected_revision=request.expected_revision))
    return _bounded({"insights": [_insight_json(kb, item, evidence=True) for item in published],
                     "graph": {"entities": entities, "relations": relations}})
