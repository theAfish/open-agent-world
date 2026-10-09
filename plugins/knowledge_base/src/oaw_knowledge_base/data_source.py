"""Expose published graphs and structured records without coupling the chart Pack to MKB."""
from open_agent_world.plugin_api import NodeDataSource, ResourceValidationError
from . import actions


def resolve(definition, root, depth=0):
    """Resolve local JSON Schema references and nullable field alternatives."""
    if depth > 16 or not isinstance(definition, dict):
        return {}
    seen = set()
    while definition.get("$ref", "").startswith("#/"):
        ref = definition["$ref"]
        if ref in seen:
            return {}
        seen.add(ref)
        target = root
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
        if not isinstance(target, dict):
            return {}
        definition = {**target, **{k: v for k, v in definition.items() if k != "$ref"}}
    choices = definition.get("anyOf", definition.get("oneOf", []))
    if choices:
        definition = next((choice for choice in choices if isinstance(choice, dict) and choice.get("type") != "null"), {})
        if definition.get("$ref"):
            return resolve(definition, root, depth + 1)
    return definition


def schema_types(definition):
    """JSON Schema permits both a type string and a union such as [number, null]."""
    value = definition.get("type", [])
    return {value} if isinstance(value, str) else {
        item for item in value if isinstance(item, str)
    } if isinstance(value, list) else set()


def fields(definition, prefix="", root=None, depth=0):
    root = root or definition
    definition = resolve(definition, root)
    if depth > 8:
        return []
    result = []
    for name, value in definition.get("properties", {}).items():
        value = resolve(value, root)
        path = f"{prefix}.{name}" if prefix else name
        types = schema_types(value)
        if "object" in types or "properties" in value:
            result.extend(fields(value, path, root, depth + 1))
        elif "array" not in types:
            result.append({"name": path, "type": "number" if types & {"integer", "number"} else "string"})
    return result


def datasets(schema):
    kind = actions._schema_kind(schema)
    prefix = "experiments" if kind == "experiment" else "projections"
    definition = schema.definition
    root = {"id": f"{prefix}:{schema.id}", "label": schema.name, "kind": "table", "fields": fields(definition), "aggregates": False}
    result = [root] if root["fields"] else []
    for name, prop in definition.get("properties", {}).items():
        prop = resolve(prop, definition)
        item = resolve(prop.get("items", {}), definition)
        if "array" in schema_types(prop) and ("object" in schema_types(item) or "properties" in item):
            result.append({**root, "id": f"{root['id']}:{name}", "label": f"{schema.name} / {name}", "fields": fields(item, root=definition)})
    return result


def schemas(context):
    kb = actions._client(context)
    result = [{"id": "graph", "label": "Published knowledge graph", "kind": "graph", "fields": []}]
    items = kb.schemas.list(limit=100)
    for schema in items:
        result.extend(datasets(schema))
    return {"schemas": result, "truncated": len(items) >= 100}


def get_value(record, path):
    for part in path.split("."):
        record = record.get(part) if isinstance(record, dict) else None
    return record if isinstance(record, (str, int, float, bool)) or record is None else str(record)


def read(context, query):
    if query.schema_id == "graph":
        return actions._client(context).oaw_graph_store.dataset(
            limit=query.limit, entity_type=query.entity_type, relation_type=query.relation_type)
    if query.aggregate != "none":
        raise ResourceValidationError("This source exposes bounded records; aggregate in a SQL data source")
    kb = actions._client(context)
    parts = query.schema_id.split(":", 2)
    if len(parts) < 2 or parts[0] not in {"projections", "experiments"}:
        raise ResourceValidationError("Unknown knowledge dataset")
    schema = kb.schemas.require(parts[1])
    dataset = next((item for item in datasets(schema) if item["id"] == query.schema_id), None)
    if dataset is None:
        raise ResourceValidationError("Schema dataset no longer exists")
    allowed = {field["name"] for field in dataset["fields"]}
    columns = query.columns or sorted(allowed)[:32]
    if any(name not in allowed for name in [*columns, *filter(None, (query.order_by,))]):
        raise ResourceValidationError("Unknown schema field")
    cap = min(query.limit + 1, 500)
    if parts[0] == "experiments":
        records = [item["data"] for item in kb.oaw_experiments.list(schema_id=parts[1], limit=cap)]
    else:
        records = [item.data for item in kb.projections.list(schema_id=parts[1], newest_only=True, limit=cap)]
    truncated = len(records) >= cap
    if len(parts) == 3:
        expanded = []
        for record in records:
            items = record.get(parts[2], []) if isinstance(record, dict) else []
            if isinstance(items, list):
                expanded.extend(item for item in items[:query.limit + 1] if isinstance(item, dict))
            if len(expanded) > query.limit:
                break
        records = expanded
    if query.order_by:
        def sort_key(item):
            value = get_value(item, query.order_by)
            if value is None:
                return (2, "")
            return (0, value) if isinstance(value, (int, float)) else (1, str(value))
        records.sort(key=sort_key, reverse=query.descending)
    rows = [[get_value(record, name) for name in columns] for record in records[:query.limit]]
    return {"kind": "table", "columns": columns, "rows": rows,
            "truncated": truncated or len(records) > query.limit, "scope": "bounded records"}


DATA_SOURCE = NodeDataSource(schemas, read)
