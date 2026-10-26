"""knowledge_prompt.py: concept-only ids, free predicates, aliases and reply parsing."""
import json

import pytest

from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.knowledge_prompt import (
    KNOWLEDGE_INSTRUCTION, SUGGESTED_PREDICATES, build_messages, canonical_predicate, normalize, parse_graph,
)

PROMPT = {"source_id": "s1", "filename": "paper.pdf", "instruction": KNOWLEDGE_INSTRUCTION,
          "suggested_predicates": list(SUGGESTED_PREDICATES),
          "existing": [{"name": "Si3N4", "aliases": ["silicon nitride"]}],
          "published": [{"name": "Sintering", "aliases": []}],
          "existing_predicates": [{"predicate": "improves", "count": 3}],
          "heading_path": "2 Methods", "markdown": "# Sintering of Si3N4"}


def test_messages_carry_the_suggestions_the_known_names_and_the_whole_document():
    messages = build_messages(PROMPT, markdown="full text")
    assert messages[0]["role"] == "system" and "related_to, part_of, causes" in messages[0]["content"]
    user = messages[1]["content"]
    assert "Section: 2 Methods" in user
    assert "- Si3N4 (also: silicon nitride)" in user and "- Sintering [published]" in user
    assert "improves (3)" in user
    assert user.endswith("full text")
    assert build_messages(PROMPT)[1]["content"].endswith("# Sintering of Si3N4")


@pytest.mark.parametrize("raw, expected", [
    ("is caused by", "caused_by"), ("Improves the", "improves"), ("Is A", "is_a"),
    ("has property", "has_property"), ("was measured by the", "measured_by"), ("related to", "related_to"),
    ("", "related_to"), ("the", "related_to"), ("x" * 100, "x" * 64),
])
def test_canonical_predicate_is_lexical_and_keeps_copula_predicates(raw, expected):
    assert canonical_predicate(raw) == expected


def test_parse_normalises_ids_predicates_aliases_and_drops_dangling_relations():
    entities, relations = parse_graph(json.dumps({
        "entities": [{"name": " Si3N4 ", "aliases": ["silicon nitride", "Si3N4"], "description": "a ceramic"},
                     {"name": "Sintering", "type": "method"}, {"name": "si3n4", "aliases": ["SN"]},
                     {"name": ""}],
        "relations": [{"source": "silicon nitride", "target": "Sintering", "predicate": "is processed by",
                       "description": "Si3N4 powder is densified by pressureless sintering.",
                       "evidence_level": "2", "snippet": "Si3N4 was sintered"},
                      {"source": "Si3N4", "target": "Nowhere", "type": "related_to"},
                      {"source": "Sintering", "target": "Sintering", "type": "part_of"}]}))
    assert [item["id"] for item in entities] == ["concept:si3n4", "concept:sintering"]
    assert entities[0]["name"] == "Si3N4" and entities[0]["description"] == "a ceramic"
    assert entities[0]["aliases"] == ["silicon nitride", "SN"] and "type" not in entities[1]
    assert relations == [{"id": "concept:si3n4|processed_by|concept:sintering",
                          "source": "concept:si3n4", "target": "concept:sintering", "predicate": "processed_by",
                          "description": "Si3N4 powder is densified by pressureless sintering.",
                          "evidence_level": 2, "snippet": "Si3N4 was sintered", "source_ids": []}]


def test_an_alias_never_resolves_an_endpoint_away_from_a_concept_of_its_own():
    entities, relations = normalize(
        [{"name": "LLZO", "aliases": ["Li7La3Zr2O12"]}, {"name": "Li7La3Zr2O12"}, {"name": "conductivity"}],
        [{"source": "Li7La3Zr2O12", "target": "conductivity", "predicate": "has"}])
    # The alias stays listed — the review step merges such pairs — but the name wins.
    assert [item["aliases"] for item in entities] == [["Li7La3Zr2O12"], [], []]
    assert relations[0]["source"] == "concept:li7la3zr2o12"


def test_duplicate_relations_keep_the_stronger_evidence_and_the_first_snippet():
    _, relations = normalize(
        [{"name": "A"}, {"name": "B"}],
        [{"source": "A", "target": "B", "predicate": "causes", "evidence_level": 4, "snippet": "first"},
         {"source": "A", "target": "B", "predicate": "Causes", "evidence_level": 1},
         {"source": "A", "target": "B", "predicate": "causes", "evidence_level": "bad", "snippet": "other"}])
    assert len(relations) == 1
    assert relations[0]["evidence_level"] == 1 and relations[0]["snippet"] == "first"


def test_normalize_accepts_legacy_ids_as_relation_endpoints_and_keeps_evidence_for_round_trips():
    entities, relations = normalize(
        [{"id": "concept:method:a", "type": "method", "name": "A", "source_ids": ["s1"]},
         {"name": "B", "source_ids": ["s2"]}],
        [{"source": "concept:method:a", "target": "concept:b", "type": "causes", "source_ids": ["s1"]}])
    # A re-keyed entity keeps the relations that still name its old id.
    assert entities[0]["id"] == "concept:a" and entities[0]["source_ids"] == ["s1"]
    assert relations[0]["id"] == "concept:a|causes|concept:b" and relations[0]["source_ids"] == ["s1"]


@pytest.mark.parametrize("answer", ["not json", "{}", '{"entities": "x"}', '{"entities": []}',
                                    '{"entities": [1]}'])
def test_malformed_replies_are_refused(answer):
    with pytest.raises(KnowledgeError):
        parse_graph(answer)
