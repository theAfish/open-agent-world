"""The compact `{value, unit, description}` record schema and what it expands to."""
import json
from pathlib import Path

from oaw_knowledge_base import actions, data_source
from oaw_knowledge_base.entry_schema import expand, field_names, flatten

COMPACT = {
    "required": ["batch_no"],
    "properties": {
        "batch_no": {"value": "string", "description": "Batch id"},
        "ionic_conductivity": {"value": "number[]", "unit": "mS/cm", "description": "One or two values"},
        "xrd_result": {"value": ["pass", "fail"]},
    },
}


def test_expand_describes_a_record_of_value_unit_and_description():
    properties = expand(COMPACT)["properties"]
    assert properties["ionic_conductivity"] == {
        "type": "object", "additionalProperties": False, "description": "One or two values", "required": ["value"],
        "properties": {"value": {"type": ["array", "null"], "items": {"type": "number"}},
                       "unit": {"type": ["string", "null"], "default": "mS/cm"},
                       "description": {"type": ["string", "null"]}}}
    assert properties["xrd_result"]["properties"]["value"] == {"enum": ["pass", "fail", None]}
    assert expand(COMPACT)["required"] == ["batch_no"]


def test_properties_that_are_not_compact_pass_through_unchanged():
    plain = {"type": "object", "properties": {"n": {"type": "number"}, "o": {"type": "object", "properties": {"value": {"type": "number"}}}}}
    assert expand(plain) == plain


def test_records_are_validated_against_the_expanded_schema():
    good = {"batch_no": {"value": "A1"}, "ionic_conductivity": {"value": [3.16, 3.91], "unit": "mS/cm"},
            "xrd_result": {"value": "pass", "description": "clean"}}
    assert actions._check(COMPACT, good)["valid"] is True
    bad = {"ionic_conductivity": {"value": "high"}, "xrd_result": {"value": "maybe"}}
    result = actions._check(COMPACT, bad)
    assert result["valid"] is False and len(result["errors"]) >= 2


def test_a_record_may_not_invent_properties_or_write_flat_values():
    invented = {"batch_no": {"value": "A1"}, "batch_id": {"value": "A1"}}
    assert any("batch_id" in error for error in actions._check(COMPACT, invented)["errors"])
    flat = {"batch_no": "A1"}
    assert actions._check(COMPACT, flat)["valid"] is False
    extra_field = {"batch_no": {"value": "A1", "lot": "x"}}
    assert actions._check(COMPACT, extra_field)["valid"] is False


def test_insight_and_chart_fields_come_from_the_expanded_schema():
    assert field_names(COMPACT) == [
        "batch_no.value", "batch_no.unit", "batch_no.description",
        "ionic_conductivity.value", "ionic_conductivity.unit", "ionic_conductivity.description",
        "xrd_result.value", "xrd_result.unit", "xrd_result.description"]
    schema = type("Schema", (), {"id": "s1", "name": "Batch", "definition": COMPACT})
    assert "ionic_conductivity.unit" in [f["name"] for f in data_source.datasets(schema)[0]["fields"]]


def test_flatten_takes_scalars_and_one_level_of_objects_only():
    assert flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}, "f": [1, 2], "g": None}) == {"a": 1, "b.c": 2, "g": None}
    assert flatten("not a dict") == {}


def test_the_shipped_example_is_compact_and_a_definition_without_a_type_is_accepted():
    example = json.loads((Path(__file__).parents[1] / "examples/solid_electrolyte/schema.json").read_text(encoding="utf-8"))
    properties = example["definition"]["properties"]
    assert all(set(prop) <= {"value", "unit", "description"} for prop in properties.values())
    actions._validate_definition(example["definition"])
