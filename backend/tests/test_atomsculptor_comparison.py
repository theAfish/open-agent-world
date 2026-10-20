"""Native archive comparison must count compound executions and incomplete usage."""
import importlib.util
import json
from pathlib import Path


def comparator():
    spec = importlib.util.spec_from_file_location("execution_comparison", Path(__file__).parents[2] / "scripts/compare-atomsculptor-runs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pairing_reports_task_flips_separately_from_run_status_and_cost(tmp_path):
    from copy import deepcopy
    mod = comparator()
    def archive(name, verdict, duration, *, gateway="https://bailian.example/v1", provenance=True):
        folder = tmp_path / name; folder.mkdir()
        run = {"run_id":name, "status":"succeeded", "started_at":"2026-10-10T00:00:00Z",
               "finished_at":f"2026-10-10T00:00:{duration:02d}Z", "lifecycle":{"model_requests":[]}}
        metadata = {"run_id":name, "task_id":"junction", "repeat":1, "prompt_sha256":"same",
            "provenance_available":provenance, "acceptance":verdict,
            "provenance":{"model":{"adapter":"openai", "model_id":"fixture", "gateway":gateway},
                          "inputs":{"prompt_sha256":"same", "selection_sha256":"same-selection"},
                          "code":{"working_source_sha256":name}}, "effective_model_configurations":[]}
        for filename, data in (("agent logs.txt",[{"run":run}]), ("experiment.json",metadata)):
            (folder / filename).write_text(json.dumps(data))
        (folder / "conversation logs.txt").write_text("")
        return folder
    baseline = archive("baseline", "passed", 20)
    failed = archive("candidate", "failed", 5)
    result = mod.paired_compare([failed], [baseline])
    assert result["acceptance_flips"] == {"passed_to_failed":1} and result["success_cost_pairs"] == 0
    assert result["pairs"][0]["current"]["status"] == "succeeded"
    assert result["successful_pair_mean_duration_delta_seconds"] is None
    unknown = archive("unknown", "unknown", 10)
    assert mod.paired_compare([unknown],[baseline])["acceptance_flips"] == {"acceptance_unknown":1}
    other_gateway = archive("other", "passed", 10, gateway="https://another.example")
    assert mod.paired_compare([other_gateway],[baseline])["matched_pairs"] == 0
    historical = archive("historical", "passed", 10, provenance=False)
    assert mod.paired_compare([historical],[baseline])["matched_pairs"] == 0
    assert mod.paired_compare([failed, failed],[baseline])["excluded"][0]["reason"] == "Missing or duplicate matched repetition"
    for folder in (failed, baseline):
        path = folder / "experiment.json"
        metadata = json.loads(path.read_text())
        metadata["provenance"]["model"]["model_id"] = None
        path.write_text(json.dumps(metadata))
    incomplete = mod.paired_compare([failed], [baseline])
    assert incomplete["matched_pairs"] == 0
    assert "Run-time source/configuration provenance incomplete" in incomplete["excluded"][0]["reasons"]


def test_cache_and_reasoning_usage_preserve_missing_and_explicit_zero(tmp_path):
    mod = comparator()
    rows = [{"role":"builder", "input_tokens":10, "output_tokens":3, "reasoning_tokens":2},
            {"role":"builder", "input_tokens":10, "output_tokens":3, "cached_input_tokens":0}]
    cost = mod.process_metrics(tmp_path, {"run_id":"fixture"}, rows, [])["model_cost_by_subtask"][0]
    assert cost["known_cached_input_tokens"] == 0 and cost["attempts_with_known_cached_input_tokens"] == 1
    assert cost["known_reasoning_tokens"] == 2 and cost["attempts_with_known_reasoning_tokens"] == 1
    cost = mod.process_metrics(tmp_path, {"run_id":"fixture"}, [{}], [])["model_cost_by_subtask"][0]
    assert cost["known_cached_input_tokens"] is None and cost["known_reasoning_tokens"] is None


def test_recovery_is_native_execution_evidence_and_not_task_acceptance(tmp_path):
    tools = [{"name":"run_modelling_step", "response":{"step":{"state":"finished"},
              "execution":{"command_id":identity, "exit_code":code}}}
             for identity, code in (("one",1), ("two",1), ("three",0), ("four",1))]
    tools.append({**tools[2], "response":{**tools[2]["response"], "reused_execution":True}})
    result = comparator().process_metrics(tmp_path, {"run_id":"fixture"}, [], tools)["recovery"]
    assert result["modelling_failure_episodes"] == 2
    assert result["episodes_followed_by_successful_execution"] == 1
    assert result["native_execution_recovery_rate"] == .5 and result["unresolved_failure_episode"]
    assert result["reused_execution_receipts"] == 1 and "does not imply task acceptance" in result["scope"]


def test_native_comparison_separates_unchanged_saves_and_interrupted_time(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(json.dumps(value))
    write("manifest.json", {"run_id": "test", "invocations": {"model_requests_with_unknown_input_tokens": ["interrupted"]}})
    write("tool calls.json", [
        {"name": "run_modelling_step", "response": {"step": {"sandbox": "box", "script_path": "probe.py"}, "save": {"sha256": "a"}}},
        {"name": "run_modelling_step", "response": {"step": {"sandbox": "box", "script_path": "probe.py"}, "save": {"sha256": "a"}}},
        {"name": "patch_modelling_step", "response": {"step": {"sandbox": "box", "script_path": "probe.py", "source_version": 2}, "save": {"written": 8}}},
        {"name": "rerun_modelling_step", "response": {"step": {"sandbox": "box", "script_path": "probe.py", "source_version": 2}, "save": {"reused_saved_file": True}}},
        {"name": "run_modelling_step", "response": {"error": {"code": "step_patch_required"}}}])
    write("model requests.json", [{"request": {"contents": [{"parts": [{"text": "HOST RUN EVIDENCE\n" + json.dumps({"execution_role": "structure_builder", "modelling_workflow": {"current_stage": "probe"}})}]}]}}])
    write("sandbox logs.json", [{"exit_code": 1, "duration_seconds": 1}, {"exit_code": None, "duration_seconds": None}])
    result = comparator().execution_detail(tmp_path, {"run_id": "test"}, [{"interrupted_at": "now", "elapsed_seconds": 124}])
    assert result["native_commands"] == 2 and result["native_failed_commands"] == 1
    assert result["unchanged_script_saves"] == 1 and result["new_script_artifacts"] == 1
    assert result["saved_script_versions"] == 3 and result["changed_script_saves"] == 1
    assert result["rejections_by_code"] == {"step_patch_required": 1}
    assert result["stages_observed"] == ["probe"] and result["known_interrupted_model_seconds"] == 124
    assert result["unknown_input_token_attempts"] == ["interrupted"]


def test_unrelated_export_is_not_used_as_comparison_evidence(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"run_id": "other"}))
    assert comparator().execution_detail(tmp_path, {"run_id": "test"}, [])["scope"] == "unavailable"


def test_comparison_merges_truncated_history_native_results_and_delivery_failures_once(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(json.dumps(value))
    write('manifest.json', {'run_id': 'test', 'invocations': {}})
    write('model requests.json', [{'role': 'structure_builder', 'started_at': '2026-10-07T00:00:00Z',
        'returned_at': '2026-10-07T00:00:12Z', 'request': {'contents': [{'parts': [{'text': 'HOST RUN EVIDENCE\n' +
            json.dumps({'execution_role': 'structure_builder', 'modelling_workflow': {'current_stage': 'deliver'}})}]}]}}])
    write('sandbox logs.json', [{'id': 'last', 'exit_code': 0, 'duration_seconds': 4,
                                'tool_result': {'command_id': 'last', 'exit_code': 0}}])
    write('tool calls.json', [
        {'name': 'run_modelling_step', 'response': {'execution': {'command_id': 'old', 'exit_code': 0, 'duration_seconds': 2}}},
        {'name': 'wait_sandbox_operation', 'response': {'command_id': 'last', 'status': 'running'}},
        {'name': 'wait_sandbox_operation', 'response': {'command_id': 'last', 'exit_code': 0, 'duration_seconds': 4}},
        {'name': 'run_delivery_step', 'response': {'error': {'code': 'delivery_cli_error',
            'validation': {'command_id': 'cli', 'exit_code': 2, 'duration_seconds': 1}}}}])
    write('tool receipts.json', [{'capability_kind': 'sandbox.execute', 'result_hints_json': json.dumps(
        {'command_id': 'old', 'exit_code': 0})}])
    result = comparator().execution_detail(tmp_path, {'run_id': 'test'}, [
        {'interrupted_at': 'now', 'elapsed_seconds': 40, 'retry_delay_seconds': 2.5}])
    assert result['native_commands'] == result['native_finished_commands'] == 3
    assert result['native_failed_commands'] == 1 and result['native_command_seconds'] == 7
    assert result['retained_sandbox_history_entries'] == 1 and result['retry_wait_seconds'] == 2.5
    assert 'capability_receipts' in result['native_command_sources']
    assert result['native_exit_codes'] == {'0': 2, '2': 1}
    assert result['native_seconds_by_exit_code'] == {'0': 6, '2': 1}
    assert result['returned_model_requests_by_role_stage'] == {'structure_builder:deliver': 1}
    assert result['returned_model_seconds_by_role_stage'] == {'structure_builder:deliver': 12}


def test_subtask_costs_first_measurement_and_reasoning_have_native_provenance(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(json.dumps(value))
    host = {'execution_role': 'structure_builder', 'modelling_workflow': {'current_stage': 'build'},
            'working_decision': {'current_subtask': {'subtask_id': '1:build:2'}}}
    def request(**extra):
        return {'role': 'structure_builder', 'run_attempt': 0, 'model_request': 4, 'model_attempt': 1,
                'started_at': '2026-10-09T00:00:02Z', 'request': {'contents': [{'role': 'user', 'parts': [
                    {'text': 'HOST RUN EVIDENCE\n' + json.dumps(host)}]}]}, **extra}
    write('model thinking.json', [
        {'role': 'structure_builder', 'model_request': 4, 'model_attempt': 1, 'revision': 1, 'text': 'old'},
        {'role': 'structure_builder', 'model_request': 4, 'model_attempt': 1, 'revision': 2, 'text': 'measured'},
        {'role': 'structure_builder', 'model_request': 4, 'model_attempt': 2, 'revision': 1,
         'text': 'retry', 'interrupted': True, 'suffix_withheld': 4}])
    write('events.json', [{'payload': {'workflow_event': 'modelling_subtask', 'plan_revision': 1, 'execution_sequence': 2}},
                          {'payload': {'workflow_event': 'modelling_subtask', 'plan_revision': 1, 'execution_sequence': 2}},
                          {'payload': {'workflow_event': 'modelling_stage', 'stage': 'build'}}])
    tools = [
        {'returned_at': '2026-10-09T00:00:04Z', 'response': {'execution': {'command_id': 'invalid', 'exit_code': 0},
         'step': {'state': 'finished'}, 'measurements': {'values': {'count': 4}}, 'measurement_issues': ['bad']}},
        {'returned_at': '2026-10-09T00:00:06Z', 'response': {'execution': {'command_id': 'failed', 'exit_code': 1},
         'step': {'state': 'finished'}, 'measurements': {'values': {'count': 4}}}},
        {'returned_at': '2026-10-09T00:00:12Z', 'response': {'execution': {'command_id': 'measured', 'exit_code': 0},
         'step': {'state': 'finished', 'subtask_id': '1:build:2'}, 'measurements': {'kind': 'diagnostic', 'values': {'count': 4}}}},
        {'returned_at': '2026-10-09T00:00:16Z', 'response': {'execution': {'command_id': 'measured', 'exit_code': 0},
         'reused_execution': True, 'step': {'state': 'finished'}, 'measurements': {'values': {'count': 4}}}}]
    result = comparator().process_metrics(tmp_path, {'run_id': 'test', 'started_at': '2026-10-09T00:00:00Z'}, [
        request(returned_at='2026-10-09T00:00:10Z', input_tokens=100, output_tokens=20),
        request(model_attempt=2, interrupted_at='2026-10-09T00:00:22Z', error_type='Timeout')], tools)
    cost = result['model_cost_by_subtask'][0]
    assert cost['subtask_id'] == '1:build:2' and cost['returned_model_seconds'] == 8
    assert cost['interrupted_model_seconds'] == 20 and cost['interrupted_model_attempts'] == 1
    assert cost['known_input_tokens'] == 100 and cost['known_output_tokens'] == 20
    assert cost['attempts_with_unknown_usage'] == 1
    assert cost['received_reasoning_chars'] == 13 and cost['interrupted_received_reasoning_chars'] == 5
    assert cost['reasoning_records_truncated'] == 1 and 'not reasoning tokens' in result['reasoning_scope']
    assert result['first_valid_measurement'] == {'command_id': 'measured', 'subtask_id': '1:build:2',
                                               'kind': 'diagnostic', 'elapsed_seconds': 12}
    assert result['local_subtasks_completed'] == 1 and 'not stage/task acceptance' in result['measurement_timing_scope']


def test_subtask_comparison_uses_final_host_frame_and_never_guesses_missing_data(tmp_path):
    module = comparator()
    def part(stage):
        return {'role': 'user', 'parts': [{'text': 'HOST RUN EVIDENCE\n' + json.dumps(
            {'modelling_workflow': {'current_stage': stage}})}]}
    row = {'role': 'structure_builder', 'started_at': '2026-10-09T00:00:01Z',
           'request': {'contents': [part('probe'), part('build')]}}
    assert module.host_frame(row)['modelling_workflow']['current_stage'] == 'build'
    result = module.process_metrics(tmp_path, {'run_id': 'test'}, [row], [])
    assert result['model_cost_by_subtask'][0]['stage'] == 'build'
    assert result['model_cost_by_subtask'][0]['subtask_id'] == 'unavailable'
    assert result['first_valid_measurement'] is None and result['reasoning_scope'] == 'unavailable'
    assert result['local_subtask_scope'] == 'unavailable'


def test_legacy_measurement_needs_matching_completed_command_not_assessment_prose(tmp_path):
    module = comparator()
    current = {'command_id': 'legacy', 'exit_code': 0, 'measurements': {'values': {'count': 4}}}
    row = {'request': {'contents': [{'parts': [{'text': 'HOST RUN EVIDENCE\n' + json.dumps({'current_experiment': current})}]}]}}
    run = {'started_at': '2026-10-09T00:00:00Z'}
    missing = module.process_metrics(tmp_path, run, [row], [])
    assert missing['first_valid_measurement'] is None
    tools = [{'returned_at': '2026-10-09T00:00:05Z', 'response': {'command_id': 'legacy', 'exit_code': 0}}]
    assert module.process_metrics(tmp_path, run, [row], tools)['first_valid_measurement']['elapsed_seconds'] == 5
    tools[0]['response_complete'] = False
    assert module.process_metrics(tmp_path, run, [row], tools)['first_valid_measurement'] is None
