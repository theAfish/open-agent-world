"""Deterministic statistics over the published records of one experiment schema.

Records are structured JSON against a shared schema, not free text, so the plugin
can do the arithmetic itself before a model ever sees them: per-field distributions,
outliers and pairwise correlations. The model's job is then to *name and explain*
patterns the numbers already show — "sample B7 is 3.2σ above the batch mean" is a
computed fact it cites, not a guess it makes. The same numbers answer a plain
"what is the mean conductivity" without any model call.

Pure functions, no MKB and no SQL: ``compute`` takes the schema definition and the
records' ``data`` dicts and returns JSON-ready dicts with stable ordering.
"""
from __future__ import annotations

import math
from statistics import mean, median, pstdev

MAX_RECORDS = 500
MAX_FIELDS = 40
MAX_CATEGORIES = 20
OUTLIER_Z = 2.5
MIN_OUTLIER_N = 4
MIN_CORRELATION_N = 5
MIN_CORRELATION_R = 0.6
NUMERIC_SHARE = 0.8


def flatten(data):
    """Top-level scalars plus one level of nested objects as ``parent.child``.

    Lists and deeper nesting are skipped: a per-specimen record's comparable values
    live at the top or one object down (``xrd.phase``, ``conductivity.value``).
    """
    if not isinstance(data, dict):
        return {}
    flat = {}
    for key, value in data.items():
        if isinstance(value, dict):
            for child, inner in value.items():
                if _scalar(inner):
                    flat[f"{key}.{child}"] = inner
        elif _scalar(value):
            flat[key] = value
    return flat


def _scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def field_names(definition):
    """The flattened field names a schema describes, in declaration order."""
    names = []
    properties = (definition or {}).get("properties")
    if not isinstance(properties, dict):
        return names
    for key, spec in properties.items():
        nested = spec.get("properties") if isinstance(spec, dict) else None
        if isinstance(nested, dict) and spec.get("type") == "object":
            names.extend(f"{key}.{child}" for child in nested)
        else:
            names.append(key)
    return names


def _ordered_fields(definition, rows):
    declared = field_names(definition)
    seen = set(declared)
    extra = sorted({key for _, flat in rows for key in flat} - seen)
    return (declared + extra)[:MAX_FIELDS]


def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _round(value, digits=6):
    return round(value, digits) if value is not None else None


def compute(definition, records):
    """Statistics over ``records`` (dicts with ``id``, ``name`` and ``data``).

    Returns ``{"records": n, "truncated": bool, "fields": [...], "correlations": [...]}``
    where each field is either numeric (``count, mean, std, min, max, median,
    outliers``) or categorical (``counts``); fields with no usable values are listed
    with ``kind="empty"`` so a caller can tell "absent" from "all missing".
    """
    truncated = len(records) > MAX_RECORDS
    rows = [(record, flatten(record.get("data"))) for record in records[:MAX_RECORDS]]
    fields = []
    numeric_series = {}
    for name in _ordered_fields(definition, rows):
        present = [(record, flat[name]) for record, flat in rows
                   if name in flat and flat[name] is not None and flat[name] != ""]
        if not present:
            fields.append({"field": name, "kind": "empty", "count": 0})
            continue
        numbers = [(record, _number(value)) for record, value in present]
        parsed = [(record, value) for record, value in numbers if value is not None]
        if parsed and len(parsed) / len(present) >= NUMERIC_SHARE:
            values = [value for _, value in parsed]
            std = pstdev(values) if len(values) > 1 else 0.0
            avg = mean(values)
            outliers = []
            if len(values) >= MIN_OUTLIER_N and std > 0:
                for record, value in parsed:
                    z = (value - avg) / std
                    if abs(z) > OUTLIER_Z:
                        outliers.append({"record_id": record.get("id"), "name": record.get("name"),
                                         "value": value, "z": _round(z, 2)})
            outliers.sort(key=lambda item: -abs(item["z"]))
            fields.append({"field": name, "kind": "numeric", "count": len(values),
                           "mean": _round(avg), "std": _round(std), "min": min(values),
                           "max": max(values), "median": _round(median(values)),
                           "outliers": outliers})
            numeric_series[name] = {record.get("id"): value for record, value in parsed}
            continue
        counts = {}
        for _, value in present:
            counts[str(value)] = counts.get(str(value), 0) + 1
        if len(counts) <= MAX_CATEGORIES:
            fields.append({"field": name, "kind": "categorical", "count": len(present),
                           "counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))})
        else:
            fields.append({"field": name, "kind": "text", "count": len(present),
                           "distinct": len(counts)})

    correlations = []
    names = list(numeric_series)
    for index, a in enumerate(names):
        for b in names[index + 1:]:
            shared = [key for key in numeric_series[a] if key in numeric_series[b]]
            if len(shared) < MIN_CORRELATION_N:
                continue
            r = _pearson([numeric_series[a][key] for key in shared],
                         [numeric_series[b][key] for key in shared])
            if r is not None and abs(r) >= MIN_CORRELATION_R:
                correlations.append({"a": a, "b": b, "r": _round(r, 3), "n": len(shared)})
    correlations.sort(key=lambda item: (-abs(item["r"]), item["a"], item["b"]))
    return {"records": len(rows), "truncated": truncated, "fields": fields,
            "correlations": correlations}


def render_table(definition, records, *, max_cell=80):
    """The records as one markdown table with a ``record_id`` column, for a prompt.

    Columns follow the schema's declared order so the table reads like the form a
    person reviews; a long cell is cut so one note cannot swamp the window.
    """
    rows = [(record, flatten(record.get("data"))) for record in records[:MAX_RECORDS]]
    columns = _ordered_fields(definition, rows)
    lines = ["| record_id | name | " + " | ".join(columns) + " |",
             "|---|---|" + "---|" * len(columns)]
    for record, flat in rows:
        cells = [str(record.get("id", "")), _cell(record.get("name"), max_cell)]
        cells.extend(_cell(flat.get(column), max_cell) for column in columns)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _cell(value, max_cell):
    if value is None:
        return ""
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= max_cell else text[:max_cell - 1] + "…"


def _key(name):
    return str(name or "").strip().casefold()


def cross_schema(groups):
    """Correlations between fields of *different* schemas, over specimens both describe.

    ``groups`` are ``{"schema_id", "schema_name", "definition", "records"}``. Records
    of different schemas are matched by name — the sample id a person gave each —
    so a synthesis record and the XRD record of the same specimen line up as one
    row, and a numeric field of one can be correlated with a numeric field of the
    other. Field names come back prefixed ``"<schema name>.<field>"``. Within-schema
    correlations are not repeated here; ``compute`` already reports them.
    """
    if len(groups) < 2:
        return None
    joined = {}
    series = {}
    for group in groups:
        prefix = group["schema_name"]
        for record in group["records"][:MAX_RECORDS]:
            key = _key(record.get("name"))
            if not key:
                continue
            joined.setdefault(key, set()).add(group["schema_id"])
            for field, value in flatten(record.get("data")).items():
                number = _number(value)
                if number is None:
                    continue
                series.setdefault((prefix, f"{prefix}.{field}"), {})[key] = number
    matched = [key for key, schemas in joined.items() if len(schemas) >= 2]
    correlations = []
    names = sorted(series)
    for index, (prefix_a, a) in enumerate(names):
        for prefix_b, b in names[index + 1:]:
            if prefix_a == prefix_b:
                continue
            shared = [key for key in series[(prefix_a, a)] if key in series[(prefix_b, b)]]
            if len(shared) < MIN_CORRELATION_N:
                continue
            r = _pearson([series[(prefix_a, a)][key] for key in shared],
                         [series[(prefix_b, b)][key] for key in shared])
            if r is not None and abs(r) >= MIN_CORRELATION_R:
                correlations.append({"a": a, "b": b, "r": _round(r, 3), "n": len(shared)})
    correlations.sort(key=lambda item: (-abs(item["r"]), item["a"], item["b"]))
    return {"schemas": [group["schema_name"] for group in groups], "matched": len(matched),
            "matched_names": sorted(matched)[:50], "correlations": correlations}
