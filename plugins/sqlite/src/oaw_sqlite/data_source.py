"""Dataset adapter; SQL identifiers come exclusively from the inspected schema."""
import json

from open_agent_world.plugin_api import NodeDataSource, ResourceValidationError
from . import engine


def schemas(context):
    with engine.connection(context) as db:
        objects = db.execute("SELECT name FROM sqlite_schema WHERE type IN ('table','view') AND name NOT GLOB 'sqlite_*' ORDER BY name LIMIT 501").fetchall()
        datasets = []
        for (name,) in objects[:500]:
            fields = []
            for column in db.execute(f"PRAGMA table_xinfo({engine.identifier(name)})"):
                if column[6] == 1:
                    continue
                kind = "number" if any(t in column[2].upper() for t in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")) else "string"
                fields.append({"name": column[1], "type": kind})
            datasets.append({"id": name, "label": f"main / {name}", "kind": "table", "fields": fields, "aggregates": True})
        return {"schemas": datasets, "truncated": len(objects) > 500}


def read(context, query):
    info = engine.inspect(context, {"table": query.schema_id})
    allowed = {field["name"] for field in info["columns"] if field["hidden"] != 1}
    columns = query.columns or sorted(allowed)[:32]
    requested = [*columns, *filter(None, (query.group_by, query.value, query.order_by))]
    if any(name not in allowed for name in requested):
        raise ResourceValidationError("A selected field no longer exists; choose the schema again")
    quote = engine.identifier
    projection = ", ".join(map(quote, columns))
    suffix = ""
    value_column = "__aggregate_value" if query.group_by == "value" else "value"
    if query.aggregate != "none":
        if not query.group_by:
            raise ResourceValidationError("Choose a grouping field")
        if query.aggregate != "count" and not query.value:
            raise ResourceValidationError("Choose a numeric value field")
        func = {"count": "COUNT", "sum": "SUM", "mean": "AVG", "min": "MIN", "max": "MAX"}[query.aggregate]
        value = "*" if query.aggregate == "count" else quote(query.value)
        projection = f'{quote(query.group_by)}, {func}({value}) AS {quote(value_column)}'
        columns = [query.group_by, value_column]
        suffix = f" GROUP BY {quote(query.group_by)}"
    order = query.order_by or query.group_by
    if order:
        suffix += f" ORDER BY {quote(order)} {'DESC' if query.descending else 'ASC'}"
    sql = f"SELECT {projection} FROM {quote(query.schema_id)}{suffix} LIMIT ?"
    with engine.connection(context) as db:
        db.set_authorizer(engine.Policy(sql, "query", set()).authorize)
        cursor = db.execute(sql, (query.limit + 1,))
        rows, size, truncated = [], 0, False
        for row in cursor:
            values = [engine.cell(value) for value in row]
            size += len(json.dumps(values).encode())
            if len(rows) >= query.limit or size > engine.MAX_RESULT_BYTES:
                truncated = True
                break
            rows.append(values)
    return {"kind": "table", "columns": columns, "rows": rows, "truncated": truncated,
            "scope": "full" if query.aggregate != "none" else "rows", "value_column": value_column}


DATA_SOURCE = NodeDataSource(schemas, read)
