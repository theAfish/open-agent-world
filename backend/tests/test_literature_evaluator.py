"""Pure literature rubric checks; adapters below are local fixtures, never APIs."""
import asyncio
from copy import deepcopy
import json
import math

import pytest

from oaw_literature.evaluator import (RUBRIC_VERSION, deterministic_priority, evaluate_literature,
    make_literature_rubric, validate_distribution)
from oaw_literature.snapshots import validate_snapshot
from backend.tests.test_literature_snapshots import fixture, route, scope


def response_for(payload, *, probabilities=None, model=None):
    probabilities = probabilities or {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0}
    return {"model": model or payload["model"], "answers": {name: {
        "type": "score", "score": sum(int(key) * value for key, value in probabilities.items()),
        "confidence": .8, "probabilities": dict(probabilities),
        "legend": {str(index): text for index, text in enumerate(question["criteria"])}}
        for name, question in payload["questions"].items()}, "usage": {"input_tokens": 100, "output_tokens": 20}}


def test_rubric_is_independent_and_weights_are_explicit_policy():
    rubric = make_literature_rubric()
    assert rubric["version"] == RUBRIC_VERSION
    assert set(rubric["questions"]) == {"relevance", "evidence_gap", "testability", "cost"}
    assert math.isclose(sum(rubric["weights"].values()), 1)
    assert all(question["type"] == "score" and len(question["criteria"]) == 4 for question in rubric["questions"].values())
    assert "xrd" not in json.dumps(rubric).lower()
    rubric["questions"]["cost"]["criteria"][0] = "modified by caller"
    assert make_literature_rubric()["questions"]["cost"]["criteria"][0] != "modified by caller"


@pytest.mark.parametrize("values", [
    {"0": .5, "1": .5}, {"0": .25, "1": .25, "2": .25, "3": .25, "4": 0},
    {"0": .5, "1": .5, "2": 0, "3": True}, {"0": .5, "1": .5, "2": 0, "3": float("nan")},
    {"0": .5, "1": .5, "2": 0, "3": -.1}, {"0": .2, "1": .2, "2": .2, "3": .2},
    {0: .25, 1: .25, 2: .25, 3: .25},
])
def test_distributions_reject_missing_extra_nonfinite_and_invalid_mass(values):
    with pytest.raises(ValueError):
        validate_distribution(values)


def test_quantized_raw_distribution_is_preserved_without_renormalizing():
    probabilities = {"0": .0, "1": .33, "2": .33, "3": .33}
    result = validate_distribution(probabilities)
    assert result.probabilities == probabilities
    assert result.probability_sum == pytest.approx(.99)
    assert result.weighted_score == pytest.approx(1.98)
    assert result.normalized_score == pytest.approx(.66)


def test_rules_explain_scope_gap_cost_and_never_become_model_scores():
    proposed = route(evidence_state="conflicted")
    result = deterministic_priority(proposed, scope())
    assert 0 <= result.score <= 100
    assert result.levels["evidence_gap"] == 3
    assert result.levels["cost"] == 0 and "unknown" in result.explanations["cost"]
    assert result.interpretation == "deterministic_policy_score_not_probability"
    wrong_topic = deterministic_priority(route(query="medieval ceremonial pottery"), scope())
    assert wrong_topic.levels["relevance"] == 0
    with pytest.raises(ValueError, match="scope revisions"):
        deterministic_priority(route(scope_revision=2), scope())
    with pytest.raises(ValueError, match="max_searches"):
        deterministic_priority(route(budget={"max_searches": 11, "max_papers": 1}), scope())


@pytest.mark.asyncio
async def test_unavailable_scorer_keeps_honest_rule_baseline_without_network():
    calls = []
    async def adapter(payload):
        calls.append(payload)
        return response_for(payload)
    missing_model = await evaluate_literature(route(), scope(), adapter=adapter)
    assert missing_model["status"] == "unavailable" and missing_model["reason_code"] == "model_not_configured"
    missing_adapter = await evaluate_literature(route(), scope(), model="selected-model")
    assert missing_adapter["reason_code"] == "score_adapter_unavailable"
    assert calls == []
    assert missing_adapter["rule_priority"] and missing_adapter["jev_score"] is None


@pytest.mark.asyncio
async def test_host_can_supply_authorized_search_added_papers_without_snapshot():
    proposed = route(source_paper_ids=["search-added-paper"])
    with pytest.raises(ValueError, match="outside its research scope"):
        await evaluate_literature(proposed, scope())
    result = await evaluate_literature(proposed, scope(), scope_paper_ids=["search-added-paper"])
    assert result["status"] == "unavailable" and result["rule_priority"]


@pytest.mark.asyncio
async def test_score_records_actual_model_rubric_evidence_hashes_and_raw_distribution():
    candidate, inputs = fixture()
    snapshot = validate_snapshot(candidate, **inputs)
    calls = []
    async def adapter(payload):
        calls.append(deepcopy(payload))
        return response_for(payload, model="jev-fixture-version")
    result = await evaluate_literature(route(source_paper_ids=["synthetic-paper"]), scope(), snapshot,
        model="jev-latest", adapter=adapter)
    assert result["status"] == "succeeded" and len(calls) == 1
    assert result["model_requested"] == "jev-latest" and result["jev_score"]["model"] == "jev-fixture-version"
    assert result["source_evidence"] == [item.model_dump(mode="json") for item in snapshot.evidence]
    assert result["rubric_version"] == RUBRIC_VERSION and len(result["request_sha256"]) == 64
    assert result["jev_score"]["weighted_score"] == pytest.approx(2 / 3)
    assert result["jev_score"]["dimensions"]["cost"]["confidence_interpretation"] == "distribution_concentration_not_accuracy"
    assert "not_probability" in result["jev_score"]["score_scale"]
    assert set(calls[0]) == {"model", "state", "questions"}
    assert calls[0]["state"]["available_exact_excerpts"][0]["quote_sha256"]
    assert "api_key" not in json.dumps(result) and "authorization" not in json.dumps(calls).lower()
    assert not any(key in calls[0] for key in ("tools", "messages", "actions"))


@pytest.mark.asyncio
async def test_quantized_mass_keeps_raw_audit_but_bounded_provider_score_position():
    async def adapter(payload):
        raw = response_for(payload, probabilities={"0": 0, "1": 0, "2": .01, "3": 1})
        for answer in raw["answers"].values():
            answer["score"] = 3.0
        return raw
    result = await evaluate_literature(route(), scope(), model="selected-model", adapter=adapter)
    assert result["status"] == "succeeded"
    scored = result["jev_score"]
    assert 0 <= scored["weighted_score"] <= 1
    assert scored["raw_distribution_weighted_score"] > 1
    assert scored["dimensions"]["cost"]["probability_sum"] == pytest.approx(1.01)
    assert scored["weight_basis"] == "provider_score_divided_by_top_rubric_level"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong-model", "wrong-question", "wrong-legend", "wrong-score", "boolean-confidence", "negative-usage", "choice"])
async def test_invalid_provider_results_never_produce_a_science_score(fault):
    async def adapter(payload):
        raw = response_for(payload)
        if fault == "wrong-model":
            raw["model"] = "unexpected-model"
        elif fault == "wrong-question":
            raw["answers"]["xrd_choice"] = raw["answers"].pop("cost")
        elif fault == "wrong-legend":
            raw["answers"]["cost"]["legend"]["0"] = "Different rubric"
        elif fault == "wrong-score":
            raw["answers"]["relevance"]["score"] = 0
        elif fault == "boolean-confidence":
            raw["answers"]["testability"]["confidence"] = True
        elif fault == "negative-usage":
            raw["usage"]["input_tokens"] = -1
        else:
            raw["answers"]["relevance"]["type"] = "choice"
        return raw
    result = await evaluate_literature(route(), scope(), model="selected-model", adapter=adapter)
    assert result["status"] == "failed" and result["reason_code"] == "invalid_score_response"
    assert result["jev_score"] is None


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_never_retried():
    calls = []
    async def adapter(payload):
        calls.append(payload)
        raise RuntimeError("Authorization: Bearer PRIVATE_KEY; unreleased paper content")
    result = await evaluate_literature(route(), scope(), model="selected-model", adapter=adapter)
    assert result["status"] == "failed" and result["reason_code"] == "provider_error"
    assert len(calls) == 1 and "PRIVATE_KEY" not in json.dumps(result) and "unreleased" not in json.dumps(result)


@pytest.mark.asyncio
async def test_budget_timeout_and_cancellation_have_distinct_honest_outcomes():
    calls = []
    async def slow(payload):
        calls.append(payload)
        await asyncio.sleep(.05)
        return response_for(payload)
    timed = await evaluate_literature(route(), scope(), model="selected-model", adapter=slow, timeout_seconds=.001)
    assert timed["reason_code"] == "provider_timeout" and timed["jev_score"] is None and len(calls) == 1
    async def cancelled(payload):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await evaluate_literature(route(), scope(), model="selected-model", adapter=cancelled)
    oversized = route(missing_evidence=["x" * 5000] * 10)
    result = await evaluate_literature(oversized, scope(), model="selected-model", adapter=slow)
    assert result["reason_code"] == "input_exceeds_budget" and len(calls) == 1
