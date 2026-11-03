"""The compact record schema: each property is ``{"value", "unit", "description"}``.

    {"required": ["batch_no"],
     "properties": {"ionic_conductivity": {"value": "number[]", "unit": "mS/cm",
                                           "description": "离子电导率"}}}

``value`` is the type of the property's value: ``string``, ``number``, ``integer``,
``boolean``, ``number[]``, ``string[]``, or a list of allowed values. A record stores
``{"value", "unit", "description"}`` for each property; ``unit`` here is the unit the
schema expects. ``expand`` turns the compact form into the JSON Schema that validates
records, feeds the model, and lists chart and insight fields. Properties that are
not compact (``value`` is not a type or a list) pass through unchanged.
"""
from __future__ import annotations

SCALARS = ("string", "number", "integer", "boolean")
LISTS = {"number[]": "number", "string[]": "string"}


def _value_schema(spec):
    if isinstance(spec, list):
        return {"enum": [*spec, None]}
    if spec in LISTS:
        return {"type": ["array", "null"], "items": {"type": LISTS[spec]}}
    return {"type": [spec, "null"]}


def is_compact(prop):
    spec = prop.get("value") if isinstance(prop, dict) else None
    return (isinstance(spec, str) and (spec in SCALARS or spec in LISTS)) or isinstance(spec, list)


def _expand_property(prop):
    if not is_compact(prop):
        return prop
    unit = {"type": ["string", "null"]}
    if isinstance(prop.get("unit"), str) and prop["unit"]:
        unit["default"] = prop["unit"]
    expanded = {"type": "object", "additionalProperties": False, "properties": {
        "value": _value_schema(prop["value"]), "unit": unit, "description": {"type": ["string", "null"]}},
        "required": ["value"]}
    if isinstance(prop.get("description"), str) and prop["description"]:
        expanded["description"] = prop["description"]
    return expanded


def expand(definition):
    if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
        return definition
    expanded = {**definition, "type": "object",
                "properties": {key: _expand_property(prop) for key, prop in definition["properties"].items()}}
    if any(is_compact(prop) for prop in definition["properties"].values()):
        expanded.setdefault("additionalProperties", False)  # a record has the schema's properties and no others
    return expanded


def _scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def flatten(data):
    """Top-level scalars plus one level of nested objects as ``parent.child``.

    Lists and deeper nesting are skipped: this names the fields a record exposes, not
    every value in it.
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


def field_names(definition):
    """The flattened field names a schema describes, in declaration order."""
    names = []
    properties = (expand(definition) or {}).get("properties")
    if not isinstance(properties, dict):
        return names
    for key, spec in properties.items():
        nested = spec.get("properties") if isinstance(spec, dict) else None
        if isinstance(nested, dict) and spec.get("type") == "object":
            names.extend(f"{key}.{child}" for child in nested)
        else:
            names.append(key)
    return names
