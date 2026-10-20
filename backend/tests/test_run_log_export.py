"""Export native OAW records without adding or mutating storage."""
import importlib.util
import json
from pathlib import Path

import pytest

from backend.persistence.database import Database
from backend.runs.store import RunStore
from backend.runs import RunStatus


def exporter():
    path = Path(__file__).resolve().parents[2] / "scripts/export-atomsculptor-test.py"
    spec = importlib.util.spec_from_file_location("test_native_log_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_experiment_metadata_uses_run_time_provenance_and_explicit_acceptance():
    mod = exporter()
    result = mod.experiment_metadata({"run_id":"old", "status":"succeeded"}, "prompt", [], [])
    assert result["acceptance"] == "unknown" and result["provenance"] is None
    assert result["provenance_available"] is False
    provenance = {"code":{"git_revision":"run-head", "working_source_sha256":"dirty-source"},
                  "model":{"model_id":"model", "gateway":"https://gateway.example"}}
    result = mod.experiment_metadata({"run_id":"new"}, "prompt", [{"provenance":provenance,
        "role":"builder", "model":"model", "max_output_tokens":81920, "request":{"config":{"temperature":.2}}}],
        [{"response":{"validation":{"stdout":json.dumps({"passed":True,"validator_version":"2","file_sha256":"a"*64})}}}],
        task_id="junction", repeat=1, variant="candidate", acceptance="failed")
    assert result["provenance"]["code"]["git_revision"] == "run-head"
    assert result["effective_model_configurations"][0]["max_output_tokens"] == 81920
    assert result["validator_receipts"][0]["passed"] is True and result["acceptance"] == "failed"
    assert result["acceptance_source"] == "export_annotation"
    partial = mod.experiment_metadata({"run_id":"partial"}, "prompt",
                                      [{"provenance":provenance, "provenance_complete":False}], [])
    assert partial["provenance"] is None and not partial["provenance_available"]


def test_source_fingerprint_captures_dirty_bytes_independently_of_head(tmp_path):
    from backend.runs.provenance import source_snapshot
    source = tmp_path / "source.py"; source.write_text("print(1)")
    a = source_snapshot({"code":source})
    source.write_text("print(2)")
    b = source_snapshot({"code":source})
    assert a["working_source_sha256"] != b["working_source_sha256"]
    assert a["git_revision"] is None and a["files"]["code/source.py"] != b["files"]["code/source.py"]


def test_top_level_costs_include_compaction_retries_and_explicit_unknown_usage(tmp_path):
    path = Path(__file__).parents[2] / 'scripts/compare-atomsculptor-runs.py'
    spec = importlib.util.spec_from_file_location('compare_native_costs', path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    start, end = '2026-10-14T00:00:00Z', '2026-10-14T00:00:10Z'
    ordinary = {'role':'builder', 'phase':'task', 'model_request':1, 'model_attempt':1,
        'started_at':start, 'returned_at':end, 'input_tokens':100, 'output_tokens':10, 'reasoning_tokens':80}
    summary = {**ordinary, 'phase':'compaction', 'input_tokens':50, 'output_tokens':5, 'reasoning_tokens':4}
    interrupted = {'role':'builder', 'phase':'task', 'model_request':2, 'model_attempt':1,
        'started_at':start, 'interrupted_at':end, 'state':'interrupted'}
    retry = {**ordinary, 'model_request':2, 'model_attempt':2, 'input_tokens':120}
    (tmp_path/'agent logs.txt').write_text(json.dumps([{'run':{'run_id':'r', 'lifecycle':{'model_requests':[ordinary]}}}]))
    (tmp_path/'conversation logs.txt').write_text('')
    (tmp_path/'model requests.json').write_text(json.dumps([ordinary, summary, interrupted, retry]))
    result = mod.summarize(tmp_path/'agent logs.txt', tmp_path/'conversation logs.txt')
    assert result['known_input_tokens'] == 270 and result['known_output_tokens'] == 25
    assert result['model_requests_started'] == 4 and result['model_requests_returned'] == 3
    assert result['model_usage_scope'] == 'partial_or_unavailable'
    assert result['model_usage_by_phase']['compaction']['known_input_tokens'] == 50
    task = result['model_usage_by_phase']['task']
    assert task['retry_attempts'] == task['interrupted'] == task['attempts_with_unknown_usage'] == 1
    assert task['known_cached_input_tokens'] is None and task['known_reasoning_tokens'] == 160


def fixture_database(tmp_path):
    database = Database(tmp_path / "world.sqlite3")
    store = RunStore(database)
    run = store.create(agent_id="fixture-agent", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
    store.update_status(run.run_id, RunStatus.RUNNING)
    store.update_status(run.run_id, RunStatus.FAILED, error="fixture")
    store.update_lifecycle(run.run_id, tool_trace=[{"id": "native-tool-event", "type": "tool_completed",
        "timestamp": "2026-10-05T12:35:00+00:00", "name": "execute_command", "response": {"exit_code": 1}}],
        tool_trace_truncated=True, model_requests=[{"role": "builder", "model_request": 1,
        "started_at": "2026-10-05T12:34:00+00:00", "returned_at": "2026-10-05T12:34:30+00:00", "elapsed_ms": 30000}])
    with database.transaction() as c:
        stamp = run.created_at.isoformat()
        c.execute("INSERT INTO state_scopes VALUES ('scope','run',?,'core.run',0,?,?)", (run.run_id, stamp, stamp))
        c.execute("INSERT INTO state_values VALUES ('scope','input',?,1,0,?)", (json.dumps({"prompt": "original user prompt"}), stamp))
    return database, store, run


def test_export_keeps_native_execution_checkpoint_and_database_read_only(tmp_path):
    import hashlib
    from backend.plugins import create_builtin_registry
    from backend.state import StateStore
    database, store, run = fixture_database(tmp_path)
    try:
        state = StateStore(database, create_builtin_registry())
        checkpoint = {"status": "unfinished", "unknown_executions": ["unknown-command"],
                      "scripts": [{"path": "sample.py", "exit_code": 1}]}
        state.set(state.get_scope("run", run.run_id), "execution_checkpoint", checkpoint, run_id=run.run_id)
        with database.locked() as db:
            before = list(db.execute("SELECT value_json FROM state_values ORDER BY key"))
        folder = exporter().export_run(database.path, tmp_path / "logs", run_id=run.run_id)
        file = folder / "execution checkpoint.json"
        assert json.loads(file.read_text()) == checkpoint
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest['sha256'][file.name] == hashlib.sha256(file.read_bytes()).hexdigest()
        path = folder / 'execution path.md'
        assert path.is_file() and manifest['sha256'][path.name] == hashlib.sha256(path.read_bytes()).hexdigest()
        with database.locked() as db:
            assert list(db.execute("SELECT value_json FROM state_values ORDER BY key")) == before
    finally:
        database.close()


def test_execution_path_distinguishes_guard_rejection_native_execution_unknown_and_retry():
    common = {'role': 'builder', 'state': 'returned', 'response_complete': True,
              'started_at': '2026-10-13T07:21:00+00:00', 'arguments': {'script_path': 'step.py'}}
    tools = [
        {**common, 'id': 'guard-ref', 'name': 'run_modelling_step', 'response': {'error': {
            'code': 'step_input_changed', 'command_dispatched': False,
            'input_check': {'command_id': 'native-guard', 'exit_code': 1}}}},
        {**common, 'id': 'execute-ref', 'name': 'run_modelling_step', 'response': {
            'step': {'state': 'finished'}, 'execution': {'command_id': 'native-script', 'exit_code': 0}}},
        {**common, 'id': 'partial-ref', 'name': 'run_modelling_step', 'response_complete': False,
            'response': {'execution': {'exit_code': 0}}},
        {**common, 'id': 'argument-ref', 'name': 'run_modelling_step', 'response': {'error': {
            'code': 'step_arguments', 'kind': 'argument_validation', 'command_dispatched': False}}},
    ]
    models = [
        {'id': 'interrupted-ref', 'role': 'builder', 'model_request': 2, 'model_attempt': 1, 'state': 'interrupted'},
        {'id': 'retry-ref', 'role': 'builder', 'model_request': 2, 'model_attempt': 2, 'state': 'returned',
         'input_tokens': 20, 'output_tokens': 5, 'reasoning_tokens': 4},
    ]
    text = exporter().execution_path(models, tools)
    assert '输入检查拒绝 · 建模未执行' in text and 'native-guard' in text
    assert text.count('实际执行 · 成功返回') == 1 and 'native-script' in text
    assert '结果不完整/执行状态未知' in text and '宿主拒绝 · 建模未执行' in text
    assert '请求中断' in text and '请求重试' in text
    assert 'input=20, output=5, reasoning=4' in text
    for row in [*tools, *models]:
        assert text.count(row['id']) == 1


def test_export_is_read_only_run_scoped_immutable_and_uses_native_activity(tmp_path):
    database, store, run = fixture_database(tmp_path)
    other = store.create(agent_id="other-agent", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
    store.update_status(other.run_id, RunStatus.FAILED)
    store.update_lifecycle(other.run_id, progress="unrelated")
    try:
        with database.locked() as c:
            before = list(c.execute("SELECT run_id,status,updated_at,lifecycle_json FROM runs ORDER BY run_id"))
            schema = list(c.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        output = tmp_path / "logs"
        mod = exporter()
        folder = mod.export_run(database.path, output, run_id=run.run_id)
        events = json.loads((folder / "events.json").read_text())
        assert [e["type"] for e in events] == ["model_request_started", "model_request_returned", "tool_completed"]
        assert events[-1]["id"] == "native-tool-event"
        assert events[0]["derived_from_metadata"] is True
        assert "unrelated" not in (folder / "agent logs.txt").read_text()
        assert (folder / "prompt.txt").read_text() == "original user prompt"
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["tool_trace_truncated"] is True and manifest["complete_model_thinking_available"] is False
        original = (folder / "manifest.json").read_bytes()
        assert mod.export_run(database.path, output, run_id=run.run_id) == folder
        assert (folder / "manifest.json").read_bytes() == original
        with database.locked() as c:
            assert list(c.execute("SELECT run_id,status,updated_at,lifecycle_json FROM runs ORDER BY run_id")) == before
            assert list(c.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == schema
        store.update_status(other.run_id, RunStatus.RUNNING)
        with pytest.raises(ValueError, match="still running"):
            mod.export_run(database.path, output)
    finally:
        database.close()


@pytest.mark.parametrize("interrupted", [False, True])
def test_export_saved_thinking_full_text_and_coverage_without_new_storage(tmp_path, interrupted):
    from backend.plugins import create_builtin_registry
    from backend.state import StateStore
    from backend.runs.reasoning import RunReasoning
    database, store, run = fixture_database(tmp_path)
    try:
        state = StateStore(database, create_builtin_registry())
        thought = {"provider_message_id": "model-reasoning:builder:1", "role": "builder", "model_request": 1,
            "model_attempt": 1, "run_attempt": 0, "text": "saved thought\n" * 10000,
            "streaming": False, "interrupted": interrupted, "truncated": False}
        RunReasoning(state, run.run_id).save(thought)
        life = store.get(run.run_id).lifecycle
        store.update_lifecycle(run.run_id, model_requests=[{**life["model_requests"][0], "model_attempt": 1,
            "run_attempt": 0, "reasoning_available": True}])
        with database.locked() as db:
            schema = list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        folder = exporter().export_run(database.path, tmp_path / "logs", run_id=run.run_id)
        saved = json.loads((folder / "model thinking.json").read_text())
        assert saved[0]["text"] == thought["text"]
        assert thought["text"] in (folder / "conversation logs.txt").read_text()
        assert any(item.get("event", {}).get("payload", {}).get("text") == thought["text"]
                   for item in json.loads((folder / "agent logs.txt").read_text()))
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["complete_model_thinking_available"] is not interrupted
        assert manifest["saved_thinking_requests"] == 1 and manifest["missing_exposed_thinking_requests"] == 0
        with database.locked() as db:
            assert list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == schema
    finally:
        database.close()


def test_existing_manual_logs_are_copied_only_for_matching_run_and_never_overwritten(tmp_path):
    database, store, run = fixture_database(tmp_path)
    manual = tmp_path / "manual"
    manual.mkdir()
    (manual / "agent logs.txt").write_text(json.dumps({"run": {"run_id": "unrelated"}}))
    output = tmp_path / "logs"
    mod = exporter()
    try:
        with pytest.raises(ValueError, match="does not match"):
            mod.export_run(database.path, output, manual_source=manual)
        assert not output.exists()
        (manual / "agent logs.txt").write_text(json.dumps({"run": {"run_id": run.run_id}}))
        (manual / "conversaion logs.txt").write_text("manual original thought")
        folder = mod.export_run(database.path, output, manual_source=manual)
        assert "cannot be reconstructed" in (folder / "conversation logs.txt").read_text()
        assert (folder / "manual conversation logs.txt").read_text() == "manual original thought"
        assert (manual / "conversaion logs.txt").read_text() == "manual original thought"
    finally:
        database.close()


def test_export_distinguishes_native_idle_interruption_from_completed_response(tmp_path):
    database, store, run = fixture_database(tmp_path)
    try:
        store.update_lifecycle(run.run_id, model_requests=[{"role": "builder", "model_request": 7,
            "model_attempt": 1, "started_at": "2026-10-05T12:34:00+00:00",
            "interrupted_at": "2026-10-05T12:35:30+00:00", "error_type": "ModelStreamStalled",
            "error_origin": "local_idle_watchdog", "idle_seconds": 90, "received_content_chunks": 5}])
        folder = exporter().export_run(database.path, tmp_path / "logs")
        events = json.loads((folder / "events.json").read_text())
        native = [e for e in events if e["type"].startswith("model_request_")]
        assert [e["type"] for e in native] == ["model_request_started", "model_request_interrupted"]
        assert native[1]["derived_from_metadata"] is True
        assert native[1]["payload"]["received_content_chunks"] == 5
        assert "local_idle_watchdog" in (folder / "conversation logs.txt").read_text()
    finally:
        database.close()


def test_export_full_boundary_logs_and_honest_interrupted_coverage(tmp_path):
    from backend.plugins import create_builtin_registry
    from backend.state import StateStore
    from backend.runs.invocation_logs import RunInvocationLogs
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    database, store, run = fixture_database(tmp_path)
    try:
        archive = RunInvocationLogs(StateStore(database, create_builtin_registry()), run.run_id)
        request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="input" * 30000)])],
                            config=types.GenerateContentConfig(system_instruction="retain system instruction"))
        identity = archive.start_model("builder", 1, 0, 1, request)
        archive.finish_model(identity, [LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=42, candidates_token_count=2))])
        archive.start_model("builder", 2, 0, 1, request)
        archive.interrupt()
        tool_id = archive.start_tool("builder", "large-call", 0, "write_file", {"content": "source" * 30000})
        archive.tool_arguments(tool_id, {"content": "source" * 30000})
        archive.finish_tool(tool_id, {"stdout": "output" * 30000})
        store.update_lifecycle(run.run_id, model_requests=[{"role": "builder", "model_request": 1},
            {"role": "builder", "model_request": 2}], tool_trace=[{"id": "public", "call_id": "large-call",
            "type": "tool_completed", "timestamp": "2026-10-06T01:00:00Z", "response": {"stdout": "preview"}}])
        with database.locked() as db:
            before = list(db.execute("SELECT value_json FROM state_values ORDER BY key"))
            schema = list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        folder = exporter().export_run(database.path, tmp_path / "logs")
        models = json.loads((folder / "model requests.json").read_text())
        tools = json.loads((folder / "tool calls.json").read_text())
        assert len(models) == 2 and models[0]["input_tokens"] == 42 and models[1]["input_tokens"] is None
        assert len(tools[0]["arguments"]["content"]) == 180000
        assert len(tools[0]["response"]["stdout"]) == 180000
        coverage = json.loads((folder / "manifest.json").read_text())["invocations"]
        assert coverage["complete_model_request_payloads"] and coverage["complete_tool_payloads"]
        assert not coverage["complete_model_responses"] and coverage["model_requests_with_unknown_input_tokens"]
        with database.locked() as db:
            assert list(db.execute("SELECT value_json FROM state_values ORDER BY key")) == before
            assert list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == schema
    finally:
        database.close()


@pytest.mark.parametrize('value,expected', [('Legacy prompt', 'Legacy prompt'), (None, ''), (17, ''),
    ({'task_prompt': 'Original task', 'prompt': 'Runtime wrapper'}, 'Original task')])
def test_export_accepts_legacy_native_runtime_input_without_mutation(tmp_path, value, expected):
    database, _, run = fixture_database(tmp_path)
    try:
        with database.transaction() as c:
            c.execute("UPDATE state_values SET value_json=? WHERE key='input'", (json.dumps(value),))
        folder = exporter().export_run(database.path, tmp_path / 'logs', run_id=run.run_id)
        assert (folder / 'prompt.txt').read_text() == expected
        with database.locked() as c:
            assert json.loads(c.execute("SELECT value_json FROM state_values WHERE key='input'").fetchone()[0]) == value
    finally:
        database.close()
