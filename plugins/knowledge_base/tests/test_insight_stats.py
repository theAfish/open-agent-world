"""insight_stats.py in isolation: flattening, distributions, outliers, correlations, table."""
from oaw_knowledge_base import insight_stats

DEFINITION = {
    "type": "object",
    "properties": {
        "sample": {"type": "string"},
        "temperature": {"type": "number"},
        "conductivity": {"type": "object", "properties": {"value": {"type": "number"},
                                                          "unit": {"type": "string"}}},
        "phase": {"type": "string"},
        "notes": {"type": "array"},
    },
}


def record(index, temperature, conductivity, phase="argyrodite", **extra):
    return {"id": f"r{index}", "name": f"S{index}",
            "data": {"sample": f"S{index}", "temperature": temperature,
                     "conductivity": {"value": conductivity, "unit": "mS/cm"},
                     "phase": phase, "notes": ["x"], **extra}}


def test_flatten_takes_scalars_and_one_level_of_objects_only():
    flat = insight_stats.flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}, "f": [1, 2], "g": None})
    assert flat == {"a": 1, "b.c": 2, "g": None}
    assert insight_stats.flatten("not a dict") == {}


def test_field_names_follow_the_schema_and_descend_into_objects():
    assert insight_stats.field_names(DEFINITION) == [
        "sample", "temperature", "conductivity.value", "conductivity.unit", "phase", "notes"]


def test_numeric_fields_get_distributions_and_outliers():
    records = [record(i, 500 + i, 1.0 + i * 0.01) for i in range(9)]
    records.append(record(9, 509, 9.0))  # one wild conductivity
    stats = insight_stats.compute(DEFINITION, records)
    by_name = {item["field"]: item for item in stats["fields"]}

    temperature = by_name["temperature"]
    assert temperature["kind"] == "numeric"
    assert temperature["count"] == 10
    assert temperature["min"] == 500 and temperature["max"] == 509
    assert temperature["outliers"] == []

    conductivity = by_name["conductivity.value"]
    assert [item["record_id"] for item in conductivity["outliers"]] == ["r9"]
    assert conductivity["outliers"][0]["z"] > insight_stats.OUTLIER_Z
    assert conductivity["outliers"][0]["name"] == "S9"

    assert by_name["conductivity.unit"] == {"field": "conductivity.unit", "kind": "categorical",
                                            "count": 10, "counts": {"mS/cm": 10}}
    assert by_name["notes"]["kind"] == "empty"  # lists are never flattened
    assert stats["records"] == 10 and stats["truncated"] is False


def test_outliers_need_enough_points_and_numeric_strings_count():
    few = [record(i, 500, 1.0) for i in range(3)] + [record(3, 500, 100.0)]
    stats = insight_stats.compute(DEFINITION, few)
    conductivity = next(item for item in stats["fields"] if item["field"] == "conductivity.value")
    assert conductivity["outliers"] == []  # n < MIN_OUTLIER_N

    strings = [record(i, str(500 + i), f"{1.0 + i:.1f}") for i in range(5)]
    strings.append(record(5, "n/a", "1.2"))
    stats = insight_stats.compute(DEFINITION, strings)
    by_name = {item["field"]: item for item in stats["fields"]}
    assert by_name["temperature"]["kind"] == "numeric" and by_name["temperature"]["count"] == 5
    assert by_name["conductivity.value"]["count"] == 6


def test_correlations_are_reported_above_the_threshold_only():
    linear = [record(i, 500 + 10 * i, 1.0 + 0.1 * i) for i in range(8)]
    stats = insight_stats.compute(DEFINITION, linear)
    assert stats["correlations"] == [{"a": "temperature", "b": "conductivity.value", "r": 1.0, "n": 8}]

    noisy = [record(i, 500 + 10 * i, [1.0, 3.0, 1.5, 2.9, 1.1, 2.8, 1.3, 3.1][i]) for i in range(8)]
    stats = insight_stats.compute(DEFINITION, noisy)
    assert stats["correlations"] == []


def test_extra_undeclared_fields_are_listed_after_declared_ones_and_bounded():
    records = [record(i, 500, 1.0, zeta=i) for i in range(4)]
    stats = insight_stats.compute(DEFINITION, records)
    assert [item["field"] for item in stats["fields"]][-1] == "zeta"

    many = [{"id": f"r{i}", "name": "x", "data": {f"f{j:02d}": j for j in range(60)}}
            for i in range(3)]
    assert len(insight_stats.compute({}, many)["fields"]) == insight_stats.MAX_FIELDS
    assert insight_stats.compute({}, [record(0, 1, 1)] * 600)["truncated"] is True


def test_render_table_puts_record_ids_first_and_escapes_cells():
    records = [record(0, 500, 1.0, phase="a|b\nc")]
    table = insight_stats.render_table(DEFINITION, records)
    lines = table.strip().splitlines()
    assert lines[0].startswith("| record_id | name | sample | temperature | conductivity.value")
    assert lines[2].startswith("| r0 | S0 | S0 | 500 | 1.0 | mS/cm | a\\|b c |")


def test_cross_schema_matches_records_by_name_and_correlates_across_schemas_only():
    synthesis = {"schema_id": "s1", "schema_name": "Synthesis", "definition": {},
                 "records": [{"id": f"a{i}", "name": f"S{i}", "data": {"temperature": 500 + 10 * i, "time": 2}} for i in range(6)]}
    xrd = {"schema_id": "s2", "schema_name": "XRD", "definition": {},
           "records": [{"id": f"b{i}", "name": f"s{i}", "data": {"crystallinity": 0.5 + 0.05 * i}} for i in range(5)]
           + [{"id": "b9", "name": "unmatched", "data": {"crystallinity": 0.1}}]}
    cross = insight_stats.cross_schema([synthesis, xrd])
    assert cross["schemas"] == ["Synthesis", "XRD"]
    assert cross["matched"] == 5 and cross["matched_names"] == ["s0", "s1", "s2", "s3", "s4"]
    assert cross["correlations"] == [{"a": "Synthesis.temperature", "b": "XRD.crystallinity", "r": 1.0, "n": 5}]
    # Within-schema pairs (temperature/time) and a single schema never appear here.
    assert insight_stats.cross_schema([synthesis]) is None
