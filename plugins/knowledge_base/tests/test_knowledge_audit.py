"""knowledge_audit.py: deterministic findings, the reply's typed operations, and applying them."""
import json

import pytest

from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.knowledge_audit import (
    AUDIT_INSTRUCTION, MAX_REASON_CHARS, OPERATIONS, apply_operations, audit_statistics, build_messages,
    concept_clusters, parse_operations,
)
from oaw_knowledge_base.knowledge_prompt import normalize


def draft(entities, relations, title="t"):
    entities, relations = normalize(entities, relations)
    return {"title": title, "entities": entities, "relations": relations}


SAMPLE = draft(
    [{"name": "Li7La3Zr2O12", "aliases": ["garnet"], "description": "a garnet electrolyte", "source_ids": ["s1"]},
     {"name": "LLZO", "source_ids": ["s2"]}, {"name": "ionic conductivity"}, {"name": "Ionic Conductivities"},
     {"name": "Sintering temperature"}, {"name": "Figure 3"}, {"name": "grain size"}],
    [{"source": "Li7La3Zr2O12", "target": "ionic conductivity", "predicate": "has property", "evidence_level": 2},
     {"source": "LLZO", "target": "Ionic Conductivities", "predicate": "exhibits", "snippet": "LLZO exhibits"},
     {"source": "Sintering temperature", "target": "ionic conductivity", "predicate": "improves"},
     {"source": "Sintering temperature", "target": "grain size", "predicate": "increases"},
     {"source": "grain size", "target": "Sintering temperature", "predicate": "depends on"},
     {"source": "Sintering temperature", "target": "ionic conductivity", "predicate": "degrades", "evidence_level": 4},
     {"source": "grain size", "target": "Li7La3Zr2O12", "predicate": "associated with",
      "description": "Larger grains in LLZO pellets lower the grain-boundary resistance."}])


def test_statistics_find_clusters_isolated_mirrored_and_contradicting_elements():
    stats = audit_statistics(SAMPLE, published_matches=[{"draft_name": "LLZO", "published_name": "Li7La3Zr2O12"}])
    assert stats["concepts"] == 7 and stats["relations"] == 7
    assert stats["predicates"][0] == {"predicate": "associated_with", "count": 1}  # ties sort by name
    assert {item["predicate"] for item in stats["predicates"]} == {"has_property", "exhibits", "improves", "increases",
                                                                   "depends_on", "degrades", "associated_with"}
    assert stats["vague"] == [{"source": "grain size", "predicate": "associated_with", "target": "Li7La3Zr2O12",
                               "description": "Larger grains in LLZO pellets lower the grain-boundary resistance."}]
    assert stats["concept_clusters"] == [["Ionic Conductivities", "ionic conductivity"]]
    assert stats["isolated"] == ["Figure 3"]
    assert stats["mirrored"] == [{"a": "Sintering temperature", "b": "grain size",
                                  "predicates": ["Sintering temperature increases grain size",
                                                 "grain size depends_on Sintering temperature"]}]
    assert stats["contradictions"] == [{"a": "Sintering temperature", "b": "ionic conductivity",
                                        "predicates": ["improves", "degrades"]}]
    assert stats["published_matches"][0]["published_name"] == "Li7La3Zr2O12"


def test_clusters_join_acronyms_plurals_and_declared_aliases():
    entities, _ = normalize([{"name": "X-ray diffraction"}, {"name": "XRD"}, {"name": "solid electrolytes"},
                             {"name": "Solid electrolyte"}, {"name": "LLZO", "aliases": ["Li7La3Zr2O12"]},
                             {"name": "Li7La3Zr2O12"}, {"name": "unrelated"}], [])
    assert concept_clusters(entities) == [["LLZO", "Li7La3Zr2O12"], ["Solid electrolyte", "solid electrolytes"],
                                          ["X-ray diffraction", "XRD"]]


def test_messages_carry_the_findings_the_concepts_and_the_quoted_relations():
    prompt = {"instruction": AUDIT_INSTRUCTION, "statistics": audit_statistics(SAMPLE), "draft": SAMPLE}
    messages = build_messages(prompt)
    assert messages[0]["content"] == AUDIT_INSTRUCTION
    assert "Draft: t" in messages[1]["content"] and '"isolated": [\n  "Figure 3"' in messages[1]["content"]
    assert "- Li7La3Zr2O12 (also: garnet) — a garnet electrolyte [1 source(s)]" in messages[1]["content"]
    assert '- LLZO exhibits Ionic Conductivities [evidence 3] "LLZO exhibits"' in messages[1]["content"]
    assert "associated_with Li7La3Zr2O12 [evidence 3] — Larger grains in LLZO pellets" in messages[1]["content"]


def test_every_operation_the_parser_accepts_the_apply_actions_accept_too():
    from oaw_knowledge_base.knowledge import AuditOperation
    for op in OPERATIONS:
        assert AuditOperation.model_validate({"op": op}).op == op


def test_long_reasons_are_cut_to_one_line_rather_than_refused():
    long = "because " * 200
    [operation] = parse_operations(json.dumps([{"op": "delete_concept", "name": "x", "reason": long}]))
    assert len(operation["reason"]) == MAX_REASON_CHARS and operation["reason"].endswith("…")
    [flag] = parse_operations(json.dumps([{"op": "flag", "message": "line one\n\n  line two"}]))
    assert flag["message"] == "line one line two"


def test_parse_accepts_a_list_or_an_object_and_refuses_unknown_operations():
    assert parse_operations('{"operations": []}') == []
    assert parse_operations(json.dumps([{"op": "flag", "message": "check", "reason": None}])) == [
        {"op": "flag", "message": "check"}]
    for answer in ["nope", "{}", '{"operations": [{"op": "explode"}]}', '{"operations": [1]}']:
        with pytest.raises(KnowledgeError):
            parse_operations(answer)


def test_apply_merges_renames_aliases_deletes_and_skips_what_the_draft_lacks():
    entities, relations, applied, skipped = apply_operations(SAMPLE, [
        {"op": "merge_concepts", "keep": "Li7La3Zr2O12", "drop": ["LLZO", "nobody"], "reason": "acronym"},
        {"op": "merge_concepts", "keep": "ionic conductivity", "drop": ["Ionic Conductivities"]},
        {"op": "rename_predicate", "from": "exhibits", "to": "has property"},
        {"op": "rename_predicate", "from": "missing", "to": "x"},
        {"op": "add_alias", "name": "grain size", "alias": "grain diameter"},
        {"op": "add_alias", "name": "grain size", "alias": "Sintering temperature"},
        {"op": "delete_relation", "source": "Sintering temperature", "predicate": "degrades", "target": "ionic conductivity"},
        {"op": "set_evidence", "source": "Sintering temperature", "predicate": "improves", "target": "ionic conductivity", "level": 1},
        {"op": "delete_concept", "name": "Figure 3", "reason": "metadata"},
        {"op": "delete_concept", "name": "ghost"},
        {"op": "set_predicate", "source": "grain size", "predicate": "associated with", "target": "Li7La3Zr2O12",
         "to": "lowers grain-boundary resistance of", "description": "Larger LLZO grains lower grain-boundary resistance."},
        {"op": "set_predicate", "source": "grain size", "predicate": "lowers_grain_boundary_resistance_of",
         "target": "Li7La3Zr2O12", "to": "lowers grain boundary resistance of"},
        {"op": "flag", "message": "check the mirrored pair"},
    ])
    by_name = {item["name"]: item for item in entities}
    assert set(by_name) == {"Li7La3Zr2O12", "ionic conductivity", "Sintering temperature", "grain size"}
    assert by_name["Li7La3Zr2O12"]["aliases"] == ["garnet", "LLZO"]
    assert sorted(by_name["Li7La3Zr2O12"]["source_ids"]) == ["s1", "s2"]
    assert by_name["ionic conductivity"]["aliases"] == ["Ionic Conductivities"]
    assert by_name["grain size"]["aliases"] == ["grain diameter"]
    triples = {(item["source"], item["predicate"], item["target"], item["evidence_level"]) for item in relations}
    # The two merged pairs' relations collapse onto one has_property edge with the stronger evidence.
    assert triples == {("concept:li7la3zr2o12", "has_property", "concept:ionic_conductivity", 2),
                       ("concept:sintering_temperature", "improves", "concept:ionic_conductivity", 1),
                       ("concept:sintering_temperature", "increases", "concept:grain_size", 3),
                       ("concept:grain_size", "depends_on", "concept:sintering_temperature", 3),
                       ("concept:grain_size", "lowers_grain_boundary_resistance_of", "concept:li7la3zr2o12", 3)}
    retyped = next(item for item in relations if item["predicate"] == "lowers_grain_boundary_resistance_of")
    assert retyped["description"] == "Larger LLZO grains lower grain-boundary resistance."
    assert [item["op"] for item in applied] == ["merge_concepts", "merge_concepts", "rename_predicate", "add_alias",
                                                "delete_relation", "set_evidence", "delete_concept", "set_predicate", "flag"]
    assert applied[0]["merged"] == ["LLZO"] and applied[2]["count"] == 1
    assert applied[7]["to"] == "lowers_grain_boundary_resistance_of"
    assert [(item["op"], item["skipped"]) for item in skipped] == [
        ("rename_predicate", "no relation uses that predicate"),
        ("add_alias", "no such concept, or the alias names another concept"),
        ("delete_concept", "no such concept in the draft"),
        ("set_predicate", "the predicate is unchanged")]
