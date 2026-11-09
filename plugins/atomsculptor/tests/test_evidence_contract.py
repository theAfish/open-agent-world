"""Real stdout compatibility and generic evidence/recovery boundaries; no model calls."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from jsonschema import Draft202012Validator

from oaw_atomsculptor.measurements import parse_measurements, measurement_contract
from test_measurement_handoffs import execute, assess, record, artifact
from test_workflow import evidence, activate, tool
from test_execution_steps import setup, prior


RECORDS = json.loads((Path(__file__).parent / "fixtures/measurement_stdout_records.json").read_text())


@pytest.mark.parametrize("data,pointer,detail", [
    ({"diagnostic_json": "x" * 3122}, "/oaw_measurements/values/diagnostic_json", {"actual_chars": 3122, "max_chars": 1200}),
    ({"a/b": {"samples": list(range(129))}}, "/oaw_measurements/values/a~1b/samples", {"actual_items": 129, "max_items": 128}),
    ({"a": {"b": {"c": {"d": {"e": [1]}}}}}, "/oaw_measurements/values/a/b/c/d/e/0", {"actual_depth": 6, "max_depth": 5}),
])
def test_large_diagnostics_are_projected_without_losing_failed_checks(data, pointer, detail):
    checks = [{"name": "scientific_check", "passed": False, "reason": "Measured mismatch"}]
    parsed, issues = parse_measurements(record(values=data, constraints=checks))
    assert not issues and parsed["constraints"] == checks
    assert "record_pointer" in json.dumps(parsed["values"])
    repaired, issues = parse_measurements(record(values={"diagnostic_file": "diagnostic.json", "nested": {"count": 12}}, constraints=checks))
    assert not issues and repaired["constraints"] == checks
    assert repaired["values"]["nested"]["count"] == 12
    contract = measurement_contract()
    assert contract["data_limits"]["max_string_chars"] == 1200
    assert "JSON-encoding" in contract["schema"]["properties"]["values"]["description"]


@pytest.mark.parametrize("row", RECORDS, ids=lambda r: str(r["execution"]))
def test_real_success_stdout_is_accepted_or_identifies_the_actual_field(row):
    parsed, issues = parse_measurements(row["stdout"])
    assert (parsed is not None) == row["expected_valid"]
    if parsed:
        assert not issues
        Draft202012Validator(measurement_contract()["schema"]).validate(parsed)
    else:
        assert issues[0]["field"].startswith("oaw_measurements.coverage.")
        original = json.loads(row["stdout"].removeprefix("OAW_MEASUREMENTS "))["oaw_measurements"]
        fixed = deepcopy(original)
        nested = {k:v for k,v in fixed["coverage"].items() if not isinstance(v,int)}
        fixed["values"].update(nested)
        fixed["coverage"] = {k:v for k,v in fixed["coverage"].items() if isinstance(v,int)}
        accepted, after = parse_measurements(record(**fixed))
        assert not after and accepted["constraints"] == original["constraints"]
        assert accepted["values"] == fixed["values"]


@pytest.mark.parametrize("bad,field", [
    ({"objective":{"name":"metric","value":True,"direction":"minimize"}}, ".objective.value"),
    ({"coverage":{"region":{"size":2}}}, ".coverage.region"),
    ({"constrants":[]}, ".constrants"),
    ({"values":{"size":float('nan')}}, ".values"),
    ({"constraints":[{"name":"test","passed":"false"}]}, ".constraints[0].passed"),
])
def test_errors_are_field_specific_and_never_reuse_an_old_pass(bad, field):
    old = record(constraints=[{"name":"test","passed":True}])
    parsed, issues = parse_measurements(old+'\n'+record(**bad))
    assert parsed is None and any(field in i["field"] for i in issues)


@pytest.mark.parametrize("encoding", ["json", "marked"])
def test_trailing_diagnostics_cannot_hide_the_last_measurement_or_its_error(encoding):
    good = record(values={"count": 3}, constraints=[{"name": "measured_check", "passed": False}])
    if encoding == "marked":
        good = "OAW_MEASUREMENTS " + good
    tail = "\n" + "detail line\n" * 90
    parsed, issues = parse_measurements(good + tail)
    assert not issues and parsed["values"]["count"] == 3
    assert parsed["constraints"][0]["passed"] is False
    bad = 'OAW_MEASUREMENTS {"oaw_measurements":{"coverage":{"atoms":"three"}}}'
    parsed, issues = parse_measurements(good + tail + bad + tail)
    assert parsed is None and issues[0]["field"] == "oaw_measurements.coverage.atoms"


def test_conflicting_envelope_cannot_hide_a_failed_constraint():
    value = {"constraints":[{"name":"test","passed":False}]}
    assert parse_measurements('OAW_MEASUREMENTS '+json.dumps({"oaw_measurements":value, **value}))[0] == value
    conflicting = {"oaw_measurements":value, "constraints":[{"name":"test","passed":True}]}
    assert parse_measurements('OAW_MEASUREMENTS '+json.dumps(conflicting))[0] is None


@pytest.mark.asyncio
async def test_protocol_repair_does_not_consume_scientific_review_and_resumes_after_patch():
    outputs = iter([record(coverage={"region":{"nested":3}}), record(kind="diagnostic",coverage={"region":3})])
    e, calls, run = await setup(stdout=lambda: next(outputs))
    await run()
    assessment = prior(e)
    assert e.workflow.assess_previous(e, assessment)["observation"]["outcome"] == "continue"
    assert e.workflow.advance(e,"probe","box","step.py")["error"]["code"]=="measurement_contract"
    assert not e.workflow.review_history and not e.workflow.review_request
    changed = await run(script_path="unrelated.py")
    assert changed["error"]["code"] == "measurement_repair_required" and len(calls)==2
    repaired = await run(source="", edits=[{"old":"measurement","new":"fixed"}])
    assert repaired["ok"]
    assert not e.workflow.protocol_issues(e) and not e.workflow._protocol_repairs
    assert not e.workflow.review_history


@pytest.mark.asyncio
async def test_scientific_contradiction_can_still_review_with_a_separate_protocol_issue():
    e, _, run = await setup(stdout=record(coverage={"region":{"nested":3}}))
    await run()
    assessment = prior(e); assessment.update(assumption_invalid=True, outcome="needs_plan_review")
    assert e.workflow.assess_previous(e,assessment)["observation"]["outcome"] == "needs_plan_review"
    assert await tool(e,"finish_task",{"result":{"status":"needs_plan_review","reason":"Measured geometry contradicts method"}},"Task completed.") == "Task completed."
    assert len(e.workflow.review_history)==1


async def build(delivery="report"):
    e=evidence(); await activate(e,delivery=delivery)
    await execute(e,"probe.py","measured primitive")
    assert assess(e)["ok"] and e.workflow.advance(e,"probe","box","probe.py")["ok"]
    return e


def candidate(e, **extra):
    return {"kind":"candidate", "measurement_revision":"regions-v1", "coverage":{"interface":20},
        "constraints":[{"name":"interface_complete","passed":True,"regions":["interface"],
                        "check_ids":[r["id"] for r in e.workflow.requirements() if r["stage"]=="build"]}], **extra}


@pytest.mark.asyncio
async def test_partial_check_is_not_whole_task_success_and_failed_checker_control_cannot_pass():
    e=await build()
    partial=candidate(e,constraints=[{"name":"graph_connected","passed":True}])
    await execute(e,"partial.py",record(**partial)); assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","partial.py")["error"]["code"] == "requirement_evidence"
    assert [r for r in e.workflow.requirement_coverage(e) if r["stage"]=="build"][0]["status"]=="unknown"
    bad=candidate(e);bad["constraints"][0]["controls"]=[{"name":"known_disconnected","passed":False}]
    await execute(e,"bad-checker.py",record(**bad)); assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","bad-checker.py")["error"]["code"]=="candidate_constraints"
    good=candidate(e);good["constraints"][0]["controls"]=[{"name":"known_disconnected","passed":True}]
    await execute(e,"final.py",record(**good)); assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","final.py")["ok"]


@pytest.mark.asyncio
async def test_nonoptimization_diagnostics_are_counted_and_unknown_check_ids_are_protocol_errors():
    e=await build()
    assert "objective" not in e.workflow.plan[1]["experiment"]
    for i in range(4):
        await execute(e,f"diagnostic-{i}.py",record(kind="diagnostic",values={"measured_count":i}))
        result=assess(e)
    assert result["observation"]["outcome"]=="continue"
    assert e.workflow.recovery["unchanged_evidence"] is False
    another=await build();value=candidate(another);value["constraints"][0]["check_ids"]=["made-up"]
    await execute(another,"unknown.py",record(**value))
    assert another.workflow.protocol_issues(another)[0]["field"].endswith("check_ids")
    assert not another.measurement_reports


@pytest.mark.asyncio
async def test_requirement_identity_survives_reorder_and_current_execution_does_not_inherit_old_pass():
    e=await build(); ids={r["check"]:r["id"] for r in e.workflow.requirements()}
    e.workflow.plan[1]["checks"].reverse()
    assert ids=={r["check"]:r["id"] for r in e.workflow.requirements()}
    await execute(e,"valid.py",record(**candidate(e)));assert assess(e)["ok"]
    await execute(e,"next.py",record(kind="diagnostic",values={"new_measurement":1}));assert assess(e)["ok"]
    assert all(r["status"]=="unknown" for r in e.workflow.requirement_coverage(e))


@pytest.mark.asyncio
async def test_completed_stage_preserves_evidence_but_next_stage_chooses_its_own_question():
    e=evidence();await activate(e,delivery="report")
    check=next(r for r in e.workflow.requirements() if r["stage"]=="probe")
    await execute(e,"probe.py",record(kind="baseline",coverage={"input":4},
        constraints=[{"name":"input_count","passed":True,"check_ids":[check["id"]],"regions":["input"]}]))
    assert assess(e)["ok"] and e.workflow.advance(e,"probe","box","probe.py")["ok"]
    ledger=e.workflow.requirement_coverage(e)
    assert next(r for r in ledger if r["id"]==check["id"])["status"]=="reported_pass"
    assert next(r for r in ledger if r["stage"]=="build")["status"]=="unknown"
    assert e.working_decision()["current_subtask"]["question"]==e.workflow.plan[1]["experiment"]["question"]


@pytest.mark.asyncio
async def test_nonoptimization_progress_uses_new_requirement_evidence_and_valid_candidate_resets_failures():
    e=await build()
    e.workflow.plan[1]["checks"].append("Check the other interface")
    ids=[r["id"] for r in e.workflow.requirements() if r["stage"]=="build"]
    for i in range(3):
        await execute(e,f"diagnostic-{i}.py",record(kind="diagnostic",values={"size":i}))
        assert assess(e)["ok"]
    partial=candidate(e)
    partial["constraints"][0]["check_ids"]=ids[:1]
    await execute(e,"partial.py",record(**partial)); assert assess(e)["ok"]
    assert e.workflow._diagnostic_experiments==0 and e.workflow.host_signal is None
    partial["values"] = {"new_measured_count": 3}
    await execute(e,"same-partial.py",record(**partial)); assert assess(e)["ok"]
    assert e.workflow._diagnostic_experiments==1
    bad=candidate(e);bad["constraints"][0]["passed"]=False
    await execute(e,"bad.py",record(**bad)); assert assess(e)["ok"]
    assert e.workflow._invalid_candidates==1
    await execute(e,"good.py",record(**candidate(e))); assert assess(e)["ok"]
    assert e.workflow._invalid_candidates==e.workflow._diagnostic_experiments==0
    assert e.workflow.host_signal is None


@pytest.mark.asyncio
async def test_diagnostic_can_discover_a_failed_requirement_without_becoming_a_failed_candidate():
    e=await build()
    values=candidate(e,kind="diagnostic")
    values["constraints"][0]["passed"]=False
    await execute(e,"first.py",record(**values)); assert assess(e)["ok"]
    assert e.workflow._invalid_candidates == 0 and e.workflow.host_signal is None
    await execute(e,"still-diagnostic.py",record(**values))
    assert assess(e)["observation"]["outcome"] == "continue"
    assert e.workflow.host_signal["advisory"] is True
    assert e.workflow._invalid_candidates == 0



@pytest.mark.asyncio
async def test_malformed_typed_evidence_cannot_be_dropped_to_bypass_candidate_checks():
    e=await build()
    await execute(e,"bad.py",record(coverage={"region":{}}));assert assess(e)["ok"]
    await execute(e,"bad.py","now only ordinary stdout");assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","bad.py")["error"]["code"]=="requirement_evidence"


@pytest.mark.asyncio
async def test_unversioned_measurement_cannot_skip_explicit_candidate_coverage():
    e=await build()
    await execute(e,"partial.py",record(constraints=[{"name":"graph_connected","passed":True}]))
    assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","partial.py")["error"]["code"]=="requirement_evidence"


@pytest.mark.asyncio
async def test_an_earlier_candidate_cannot_borrow_a_later_candidates_check_results():
    e=await build()
    await execute(e,"earlier.py",record(**candidate(e,constraints=[{"name":"partial","passed":True}])));assert assess(e)["ok"]
    await execute(e,"later.py",record(**candidate(e)));assert assess(e)["ok"]
    assert e.workflow.advance(e,"build","box","earlier.py")["error"]["code"]=="requirement_evidence"
    result=e.workflow.advance(e,"build","box","later.py")
    assert result["ok"]
    assert all(r["execution_sequence"]==result["checkpoint"]["execution_sequence"] for r in result["checkpoint"]["requirement_evidence"])


@pytest.mark.asyncio
async def test_build_requirement_evidence_and_independent_validation_must_use_same_path():
    e=await build(delivery="document")
    value=candidate(e,artifacts=[artifact(path="cnt.extxyz")])
    await execute(e,"candidate.py",record(**value));assert assess(e)["ok"]
    for path, expected in (("other.extxyz","requirement_artifact"),("cnt.extxyz",None)):
        await tool(e,"run_skill_script",{"sandbox":"box","script":"scripts/structure_inspect.py","argv":["validate_structure"]},
            {"exit_code":0,"stdout":json.dumps({"passed":True,"file":path})})
        result=e.workflow.advance(e,"build","box","candidate.py",structure_path=path)
        assert result["error"]["code"]==expected if expected else result["ok"]
    saved=e.closeout_checkpoint("fixture")
    assert next(r for r in saved["requirement_coverage"] if r["stage"]=="build")["status"]=="reported_pass"


def test_requirement_pass_requires_an_actual_tested_population():
    report,issues=parse_measurements(record(constraints=[{"name":"claimed","passed":True,"check_ids":["build:test"]}]))
    assert report is None and "regions" in issues[0]["message"]


def request():
    return NS(contents=[],config=types.GenerateContentConfig(max_output_tokens=81920))


def test_cumulative_usage_never_reduces_oaw_selected_request_limit():
    e=evidence();e.output_tokens=262144-10185
    req=request();e.before_model("structure_builder",req,None,81920)
    assert req.config.max_output_tokens==81920 and e.closing_reason is None
    e.output_tokens+=81920
    req=request();e.before_model("structure_builder",req,None,81920)
    assert e.closing_reason is None and req.config.max_output_tokens==81920
    e.output_tokens+=81920
    req=request();e.before_model("atom_sculptor",req,None,81920)
    assert req.config.max_output_tokens==4096
    assert e.limits.max_run_seconds is None


@pytest.mark.asyncio
async def test_dynamic_context_uses_one_current_decision_and_does_not_repeat_review_history():
    e=await build();e.workflow.review_history=[{"reason":"a measured contradiction", "plan_revision":1,
        "stage":"probe","execution_sequence":1,"script_path":"probe.py",'script_version':"a"*64,"observations":"long stdout "*1000}]
    req=request();e.before_model("structure_builder",req,NS(types=types),81920)
    frames={p.text.split(' (')[0]:json.loads(p.text.split('\n',1)[1]) for c in req.contents for p in c.parts
            if p.text and p.text.startswith(('HOST RUN EVIDENCE','HOST STABLE CONTEXT'))}
    dynamic,stable=frames['HOST RUN EVIDENCE'],frames['HOST STABLE CONTEXT']
    assert 'review_history' not in dynamic['modelling_workflow']
    assert 'observations' not in stable['modelling_plan_context']['review_history'][0]
    assert stable['measurement_contract']==measurement_contract()
    assert 'current_subtask' in dynamic['working_decision']
    assert 'total_run_tokens' not in dynamic['output_budget']
    assert dynamic['requirement_coverage']
