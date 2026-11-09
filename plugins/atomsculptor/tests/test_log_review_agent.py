"""Native Agent registration and the exact packaged offline review scripts."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from oaw_atomsculptor.review_agent import LogReviewAgentConfig, log_review_skills
from oaw_atomsculptor.skill_package import atomsculptor_skills


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    skill = log_review_skills().skills[0]
    root = tmp_path / "bundle"
    for name, asset in skill.files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(asset.encode() if isinstance(asset, str) else base64.b64decode(asset.data_base64))
    scripts = root / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "font-cache"))
    spec = importlib.util.spec_from_file_location("packaged_log_review", scripts / "log_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, scripts, workspace


def native_fixture(workspace, name="input", run_id="r1"):
    folder = workspace / name
    folder.mkdir()
    def save(name, value):
        (folder / name).write_text(json.dumps(value))
    save("run.json", {"run_id": run_id, "status": "succeeded", "started_at": "2026-11-01T00:00:00+00:00",
                      "finished_at": "2026-11-01T00:01:00+00:00"})
    save("tool calls.json", [
        {"id": "t1", "name": "run_modelling_step", "state": "returned",
         "arguments": {"source": "raise AssertionError('ARCHIVED_CODE_MUST_NOT_RUN')"},
         "response": {"execution": {"command_id": "c1", "exit_code": 0},
                      "constraint_failures": [{"passed": False}]}},
        {"id": "t2", "name": "validate_modelling_artifact", "state": "returned", "response": {"ok": True}}])
    save("sandbox logs.json", [{"id": "c1", "exit_code": 0, "duration_seconds": 0.1}])
    save("model requests.json", [
        {"id": "m1", "run_attempt": 0, "scope": "builder", "role": "builder", "model_request": 1,
         "model_attempt": 1, "state": "interrupted", "started_at": "2026-11-01T00:00:00+00:00",
         "interrupted_at": "2026-11-01T00:00:10+00:00"},
        {"id": "m2", "run_attempt": 0, "scope": "builder", "role": "builder", "model_request": 1,
         "model_attempt": 2, "state": "returned", "input_tokens": 10}])
    save("model thinking.json", [{"text": "Visible reasoning retained as data."}])
    (folder / "candidate.extxyz").write_text('2\nLattice="4 0 0 0 4 0 0 0 4" Properties=species:S:1:pos:R:3 pbc="T T T"\nCu 0 0 0\nAu 2 2 2\n')
    save("document.json", {"format_version": 1, "atoms": [{"symbol": "Cu", "x": 0, "y": 0, "z": 0},
        {"symbol": "Au", "x": 2, "y": 2, "z": 2}], "cell": [[4,0,0],[0,4,0],[0,0,4]], "pbc": [True]*3})
    return folder


def manifest(workspace):
    value = {"title": "Saved structure review <script>", "summary": "Recorded evidence only.", "nodes": [
        {"id": "build", "title": "Build & compare", "run_id": "r1", "tool_id": "t1", "command_id": "c1",
         "outcome": "exit0, self-check failed", "structures": [
             {"path": "candidate.extxyz", "provenance": "collection_snapshot", "relation": "output"}]},
        {"id": "validate", "title": "Read native document", "run_id": "r1", "tool_id": "t2", "parents": ["build"],
         "structures": [{"path": "document.json", "provenance": "collection_snapshot", "relation": "unchanged"},
                        {"provenance": "missing", "note": "Overwritten original; not regenerated."}]}]}
    path = workspace / "manifest.json"
    path.write_text(json.dumps(value))
    return path, value


def test_card_uses_native_runtime_and_separate_private_skill_package():
    from backend.plugins.loader import load_plugin_registry
    plugins = load_plugin_registry()
    card = plugins.node_type("atomsculptor.log-review-agent")
    assert {"core.agent", "ui.schema-agent.v1"} <= card.traits
    assert card.config_model().runtime_provider_id == "google.adk"
    assert card.default_equipment[0].type == "atomsculptor.review-skills"
    assert card.label == card.default_name == "Report Agent"
    assert card.user_creatable
    for kind in ("atomsculptor.skills", "atomsculptor.review-skills",
                 "atomsculptor.skills.skill", "atomsculptor.review-skills.skill"):
        assert not plugins.node_type(kind).catalog_item("atomsculptor").user_creatable
    assert plugins.runtime_provider_owner_id("google.adk") != "atomsculptor"
    assert LogReviewAgentConfig().model == "oaw:default"
    assert LogReviewAgentConfig().run_execution_deadline_seconds == 0
    assert "log_review" not in {s.id for s in atomsculptor_skills().skills}
    skill = log_review_skills().skills[0]
    assert skill.runtime_requirements[0].environment == "log-review"
    source = Path(__file__).parents[1] / "src/oaw_atomsculptor/skills/structure-image/scripts/structure_image.py"
    assert skill.files["scripts/structure_image.py"] == source.read_text()
    from importlib.resources import files
    assert skill.files["scripts/oaw_tool_activity.py"] == files("open_agent_world").joinpath("tool_activity.py").read_text()


def test_native_card_creation_equips_review_skill_without_execution_grants(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    from backend.tests.test_plugin_defaults import owned, document
    from backend.capabilities.provider import WorldAgentCapabilityProvider

    settings = replace(Settings.for_data_root(tmp_path), agent_runtime="core.mock")
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        created = client.post("/api/nodes", json={"type": "atomsculptor.log-review-agent"})
        assert created.status_code == 201, created.text
        assert created.json()["name"] == "Report Agent"
        agent = created.json()["id"]
        equipment = owned(client, agent)
        assert len(equipment) == 1 and equipment[0]["type"] == "atomsculptor.review-skills"
        package = document(client, equipment[0]["id"])["value"]
        assert package["package_id"] == "atomsculptor.review-skills"
        assert len(package["skills"]) == 1 and package["skills"][0]["id"] == "log_review"
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        tools = client.portal.call(provider.list_tools, agent)
        assert any(t.name == "read_skills" for t in tools)
        assert not any(t.name in {"execute_command", "run_skill_script", "import_atom_structure_file"} for t in tools)


def test_real_packaged_cli_builds_flow_files_previews_and_portable_evidence(packaged):
    module, scripts, workspace = packaged
    source = native_fixture(workspace)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    plan, _ = manifest(workspace)
    inspected = module.inspect_archive(str(source))
    assert inspected["statistics"]["input_usage_unknown_attempts"] == 1
    assert set(inspected["candidate_structure_paths"]) == {"candidate.extxyz", "document.json"}
    assert [row["short_id"] for row in inspected["operation_index"]] == ["A1:T001", "A1:T002"]
    inspection = json.loads((workspace / "review-input.json").read_text())
    assert inspection["operations"][0]["requested"]["source_characters"] > 0
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)  # The shipped command must select headless rendering itself.
    result = subprocess.run([sys.executable, str(scripts / "log_review.py"), "build_review",
        "--folder", str(source), "--manifest-file", str(plan), "--output-folder", "review"],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["previews"] == 2 and receipt["nodes"] == 2
    assert receipt["operations"] == receipt["unexplained_operations"] == 2
    output = workspace / "review"
    report = json.loads((output / "review.json").read_text())
    assert report["nodes"][0]["native_status"] == "exit 0 / self-check failed"
    assert any(w.get("kind") == "missing_version" for w in report["warnings"])
    html = (output / "index.html").read_text() + (output / 'records.html').read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "2 attempts / 1 interrupted" in html
    assert "尚无逐步解读" in html
    ElementTree.fromstring((output / "flowchart.svg").read_text())
    for node in report["nodes"]:
        for item in node["structures"]:
            if item.get("preview"):
                assert item["structure_summary"]["atom_count"] == 2
                assert item["structure_summary"]["pbc"] == [True]*3
                assert (output / item["preview"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                assert item["preview_kind"] == "saved_file_render_not_ui_capture"
    with ZipFile(output / "review.zip") as bundle:
        assert bundle.read("raw/attempt-01/tool calls.json") == before["tool calls.json"]
        assert bundle.read("raw/attempt-01/model thinking.json") == before["model thinking.json"]
        assert "flowchart.svg" in bundle.namelist() and "review.zip" not in bundle.namelist()
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


def test_operation_explanations_cover_history_source_paging_failure_and_execution(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    calls = [
        {"id": "t1", "name": "inspect_sandbox", "state": "returned",
         "arguments": {"sandbox": "s1", "history_scope": "all", "history_limit": 3, "include_output": True},
         "response": {"state": "ready", "history_scope": "all", "recent_commands": [{"id": "old-command", "stdout": "Earlier candidate had 12 atoms."}]}},
        {"id": "t2", "name": "read_modelling_source", "state": "returned",
         "arguments": {"script_path": "scripts/build.py", "offset": 0},
         "effective_arguments": {"script_path": "scripts/build.py", "start_line": 10, "end_line": 20, "limit": 1200},
         "response": {"text": "saved source slice", "offset": 200, "total_chars": 5000, "next_offset": 1400, "version": 2}},
        {"id": "t3", "name": "read_tool_result", "state": "returned",
         "arguments": {"invocation_id": "t1", "section": "response", "pointer": "/response/recent_commands", "offset": 0, "limit": 800},
         "response": {"ok": False, "error": {"code": "tool_result_pointer", "message": "Wrong pointer"}, "text": "ECHO_MUST_NOT_BECOME_OBSERVATION"}},
        {"id": "t4", "name": "read_tool_result", "state": "returned",
         "arguments": {"invocation_id": "t1", "pointer": "/recent_commands/0/stdout", "offset": 0, "limit": 800},
         "response": {"text": "Earlier candidate had 12 atoms.", "offset": 0, "total_chars": 31, "next_offset": None}},
        {"id": "t5", "name": "run_modelling_step", "state": "returned",
         "arguments": {"script_path": "scripts/build.py", "reason": "Check the saved candidate"},
         "response": {"execution": {"command_id": "c1", "exit_code": 1}}},
    ]
    (source / "tool calls.json").write_text(json.dumps(calls))
    (source / "sandbox logs.json").write_text(json.dumps([{"id": "c1", "exit_code": 1}]))
    plan = workspace / "operations-manifest.json"
    notes = [
        {"run_id": "r1", "tool_id": "t1", "purpose": "查阅已有命令历史", "finding": "返回一条旧命令摘要", "follow_up": "随后读取源码；无法证明此次读取被用于改动", "basis": "inferred", "evidence": [{"run_id": "r1", "kind": "tool", "id": "t1"}]},
        {"run_id": "r1", "tool_id": "t2", "finding": "仅取得一页源码，未读完整文件", "basis": "unknown"},
        {"run_id": "r1", "tool_id": "t3", "finding": "指针被拒绝，没有获得请求内容", "follow_up": "t4改用真实指针后返回文本", "basis": "stated", "evidence": [{"run_id": "r1", "kind": "tool", "id": "t3"}, {"run_id": "r1", "kind": "tool", "id": "t4"}]},
    ]
    plan.write_text(json.dumps({"nodes": [{"id": "build", "title": "执行检查", "run_id": "r1", "tool_id": "t5", "command_id": "c1"}], "operations": notes}))
    receipt = module.build_review(str(source), str(plan))
    output = workspace / "review-output"
    report = json.loads((output / "review.json").read_text())
    ops = report["operations"]
    assert receipt["operations"] == 5 and receipt["unexplained_operations"] == 4
    assert ops[0]["action"] == "检查 Sandbox 与命令历史"
    assert any("1条" in fact for fact in ops[0]["observed"])
    assert ops[1]["requested"] == {"script_path": "scripts/build.py", "limit": 1200, "start_line": 10, "end_line": 20}
    assert any("1400" in fact for fact in ops[1]["observed"])
    assert "请求被拒绝/报错" in ops[2]["result_status"]
    assert not any("ECHO_MUST_NOT_BECOME_OBSERVATION" in fact for fact in ops[2]["observed"])
    assert ops[3]["target_label"] == "inspect_sandbox /recent_commands/0/stdout"
    assert "命令失败" in ops[4]["result_status"]
    assert ops[4]["stated_purpose"] == "Check the saved candidate"
    html = (output / 'records.html').read_text()
    markdown = (output / "操作说明.md").read_text()
    for text in ("查阅已有命令历史", "仅取得一页源码", "指针被拒绝", "审阅者推断", "A1:T005"):
        assert text in html and text in markdown
    assert "时间顺序，不代表因果" in html
    assert (output / "raw/attempt-01/tool calls.json").read_bytes() == (source / "tool calls.json").read_bytes()


@pytest.mark.parametrize("annotation", [
    {"run_id": "r1", "tool_id": "absent"},
    {"run_id": "r1", "tool_id": "t1", "basis": "stated"},
    {"run_id": "r1", "tool_id": "t1", "evidence": [{"run_id": "r1", "kind": "model", "id": "missing"}]},
])
def test_operation_annotation_requires_real_native_evidence_before_output(packaged, annotation):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    plan, value = manifest(workspace)
    value["operations"] = [annotation]
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="identity|evidence"):
        module.build_review(str(source), str(plan))
    assert not (workspace / "review-output").exists()


@pytest.mark.parametrize("change, message", [
    ({"run_id": "other-run"}, "Run"),
    ({"tool_id": "missing-tool"}, "tool ID"),
    ({"command_id": "other-command"}, "command"),
    ({"parents": ["absent"]}, "parent"),
])
def test_invalid_evidence_is_rejected_before_output(packaged, change, message):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    plan, value = manifest(workspace)
    value["nodes"][0].update(change)
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match=message):
        module.build_review(str(source), str(plan))
    assert not (workspace / "review-output").exists()


@pytest.mark.parametrize("reference", ["../outside.extxyz", "leak.extxyz", "program.py"])
def test_structure_paths_cannot_escape_archive_or_execute_programs(packaged, reference):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    outside = workspace / "outside.extxyz"
    outside.write_text("not authorized by selecting input/")
    (source / "leak.extxyz").symlink_to(outside)
    (source / "program.py").write_text("raise AssertionError('must not execute')")
    plan, value = manifest(workspace)
    value["nodes"][0]["structures"][0]["path"] = reference
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="escapes|supported"):
        module.build_review(str(source), str(plan))
    assert not (workspace / "review-output").exists()


def test_missing_version_cannot_borrow_present_snapshot(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    plan, value = manifest(workspace)
    value["nodes"][0]["structures"][0]["provenance"] = "missing"
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="substitute"):
        module.build_review(str(source), str(plan))


def test_modification_graph_cycles_are_rejected_before_output(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    plan, value = manifest(workspace)
    value["nodes"][0]["parents"] = ["validate"]
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="cycle"):
        module.build_review(str(source), str(plan))
    assert not (workspace / "review-output").exists()


def test_outputs_and_inspection_cannot_mutate_input_or_overwrite_outputs(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    plan, _ = manifest(workspace)
    with pytest.raises(ValueError, match="outside"):
        module.build_review(str(source), str(plan), "input/new/report")
    with pytest.raises(ValueError, match="outside"):
        module.inspect_archive(str(source), "input/new/inspection.json")
    assert not (source / "new").exists()
    existing = workspace / "already"
    existing.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        module.build_review(str(source), str(plan), "already")


def test_explicit_task_attempts_keep_identity_and_missing_records(packaged):
    module, _, workspace = packaged
    task = workspace / "task"
    task.mkdir()
    first = native_fixture(task, "first", "r1")
    second = native_fixture(task, "second", "r2")
    (second / "model requests.json").unlink()
    (task / "task.json").write_text(json.dumps({"attempts": [{"directory": "first"}, {"directory": "second"}]}))
    result = module.inspect_archive(str(task))
    assert result["runs"] == ["r1", "r2"]
    assert result["statistics"]["record_gaps"] == [{"run_id": "r2", "missing": "model requests.json"}]
    (task / "task.json").write_text(json.dumps({"attempts": [{"directory": "first"}, {"directory": "first"}]}))
    with pytest.raises(ValueError, match="Duplicate"):
        module.inspect_archive(str(task), "other-inspection.json")


def test_auto_collected_workspace_files_and_gaps_survive_report_bundle(packaged):
    module, _, workspace = packaged
    task = workspace / "collected"
    task.mkdir()
    native_fixture(task, "attempt1")
    (task / "task.json").write_text(json.dumps({"attempts": [{"directory": "attempt1"}]}))
    collection = {"files": [{"path": "sandbox files/s1/workspace/build.py", "provenance": "collection_snapshot"}],
        "warnings": [{"kind": "sandbox_access_missing", "sandbox_id": "other-source"}]}
    (task / "collection.json").write_text(json.dumps(collection))
    script = task / "sandbox files/s1/workspace/build.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise AssertionError('DO_NOT_EXECUTE_CAPTURED_CODE')")
    plan = workspace / "manifest.json"
    plan.write_text(json.dumps({"nodes": [{"id": "build", "title": "实际执行", "run_id": "r1", "tool_id": "t1"}]}))
    inspected = module.inspect_archive(str(task))
    assert inspected["collection_summary"]["files"] == inspected["collection_summary"]["warnings"] == 1
    receipt = module.build_review(str(task), str(plan))
    assert any(row.get("kind") == "sandbox_access_missing" for row in receipt["warnings"])
    with ZipFile(workspace / "review-output/review.zip") as bundle:
        assert bundle.read("sandbox files/s1/workspace/build.py") == script.read_bytes()
        assert json.loads(bundle.read("raw/collection.json")) == collection
        assert "raw/task.json" in bundle.namelist()


def test_inspection_prefills_native_manifest_without_lifecycle_or_preview_duplication(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    saved = json.loads((source / "run.json").read_text())
    saved["lifecycle"] = {"tool_trace": "large native evidence" * 10000}
    (source / "run.json").write_text(json.dumps(saved))
    receipt = module.inspect_archive(str(source))
    index = json.loads((workspace / receipt["inspection_file"]).read_text())
    draft = json.loads((workspace / receipt["manifest_file"]).read_text())
    assert "lifecycle" not in index["attempts"][0]["run"]
    assert all("native_preview" not in op for op in index["operations"])
    assert draft["nodes"][0]["tool_id"] == "t1" and draft["nodes"][0]["command_id"] == "c1"
    assert draft["nodes"][1]["tool_id"] == "t2" and "command_id" not in draft["nodes"][1]
    assert all(node['parents'] == [] and node['structures'][0]['provenance'] == 'missing' for node in draft['nodes'])
    assert draft['operations'] == []  # No mandatory empty annotation for every read/tool.
    assert [step['step'] for step in receipt['modification_index']] == ['A1:T001', 'A1:T002']
    result = module.build_review(str(source), receipt["manifest_file"], "draft-report")
    assert result["unexplained_operations"] == 2  # Blank drafts cannot count as explanations.
    assert (source / "run.json").read_text().count("large native evidence") == 10000


def test_default_cli_builds_structure_action_sequence_and_article_data_without_annotation_loop(packaged):
    import csv

    module, scripts, workspace = packaged
    source = native_fixture(workspace)
    (source / 'seed.extxyz').write_text('1\nProperties=species:S:1:pos:R:3\nCu 0 0 0\n')
    (source / 'alternative.extxyz').write_bytes((source / 'candidate.extxyz').read_bytes())
    tools = json.loads((source / 'tool calls.json').read_text())
    tools[0].update(call_id='actual-call', role='builder', run_attempt=0)
    tools[0]['arguments']['input_artifacts'] = [{'path': 'seed.extxyz'}]
    tools[0]['response']['measurements'] = {'artifacts': [{'path': path} for path in ('candidate.extxyz', 'alternative.extxyz')]}
    tools[1]['arguments'] = {'structure_path': 'candidate.extxyz'}
    tools.insert(1, {'id': 'read-source', 'name': 'read_modelling_source', 'state': 'returned',
        'arguments': {'script_path': 'build.py'}, 'response': {'text': 'Retained source, never execute it.'}})
    (source / 'tool calls.json').write_text(json.dumps(tools))
    models = json.loads((source / 'model requests.json').read_text())
    models[1]['response'] = [{'content': {'parts': [{'text': 'Choose a small saved candidate and verify it.', 'thought': True},
        {'function_call': {'id': 'actual-call', 'name': 'run_modelling_step'}}]}}]
    (source / 'model requests.json').write_text(json.dumps(models))
    inspected = module.inspect_archive(str(source))
    assert [step['step'] for step in inspected['modification_index']] == ['A1:T001', 'A1:T003']
    assert inspected['modification_index'][0]['model_steps'] == ['A1:M002']
    annotations = {'A1:T001': {'purpose': '选择小规模候选并保存，随后检查结果。', 'basis': 'stated', 'evidence': ['A1:M002']}}
    result = subprocess.run([sys.executable, str(scripts / 'log_review.py'), 'build_review',
        '--folder', str(source), '--output-folder', 'article', '--steps', json.dumps(annotations)],
        cwd=workspace, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout or result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['explanation_status'] == 'optional' and receipt['remaining_steps'] == []
    assert not any(w.get('kind') in {'operations_unexplained', 'commands_unexplained'} for w in receipt['warnings'])
    report = json.loads((workspace / 'article/review.json').read_text())
    story = report['story']
    assert [entry['kind'] for entry in story] == ['structure', 'action', 'structure', 'action']
    assert story[1]['reasoning_summary'] == annotations['A1:T001']['purpose']
    assert story[1]['summary_evidence'][0]['id'] == 'm2'
    assert len(story[2]['structures']) == 2  # Candidates share one frame; validation creates no new geometry.
    assert report['operations'][1]['name'] == 'read_modelling_source'
    with (workspace / receipt['data_csv']).open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    structures = [row for row in rows if row['kind'] == 'structure']
    assert [row['atom_count'] for row in structures] == ['1', '2', '2']
    assert structures[1]['order'] == structures[2]['order']
    assert all((workspace / 'article' / row[key]).is_file() for row in structures for key in ('file', 'preview'))
    html = (workspace / receipt['report']).read_text()
    assert 'records.html' in html and '<details' not in html
    assert '思考：' in html and '操作：' in html and 'exit 0' not in html
    assert [entry['kind'] for entry in module.compact_story(story)] == ['structure', 'action', 'structure']
    assert '<details id="timeline">' in (workspace / 'article/records.html').read_text()
    assert (workspace / receipt['story']).is_file()
    with ZipFile(workspace / receipt['bundle']) as bundle:
        assert {'trajectory.csv', 'trajectory.json', '结构演变.md', 'report.css', 'records.html'} <= set(bundle.namelist())
        assert bundle.read('report.css') == (scripts / 'report.css').read_bytes()
    ElementTree.fromstring((workspace / receipt['flowchart']).read_text())


def test_portable_figure_keeps_every_candidate_and_embeds_its_preview(packaged):
    module, _, workspace = packaged
    preview = workspace / 'preview.png'
    preview.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aKmQAAAAASUVORK5CYII='))
    items = [{'preview': preview.name, 'provenance': 'collection_snapshot',
        'structure_summary': {'atom_count': 24, 'elements': {'Cu': 12, 'Au': 12}, 'pbc': [True, True, False]}}
        for _ in range(5)]
    items.append({'provenance': 'missing'})
    svg, _ = module.flowchart([{'id': 'long-title', 'kind': 'structure',
        'title': '并列结构与长标题 <script> ' * 10, 'structures': items}], image_root=workspace)
    root = ElementTree.fromstring(svg)
    images = root.findall('.//{http://www.w3.org/2000/svg}image')
    assert len(images) == 5  # The old three-thumbnail cutoff hid later candidates.
    assert all(base64.b64decode(image.attrib['href'].split(',', 1)[1]) == preview.read_bytes() for image in images)
    assert all(float(image.attrib['width']) == float(image.attrib['height']) for image in images)
    assert '<script>' not in svg and '历史版本缺失' in svg
    assert all(float(image.attrib['y']) + float(image.attrib['height']) < float(root.attrib['height']) for image in images)


def test_shared_structure_renderer_preserves_atomic_circle_aspect(packaged):
    from PIL import Image
    from structure_image import generate_structure_image

    _, _, workspace = packaged
    (workspace / 'atom.xyz').write_text('1\nSingle atom, no cell.\nCu 0 0 0\n')
    receipt = generate_structure_image('.', 'atom.xyz', 'atom.png')
    assert 'error' not in receipt
    with Image.open(workspace / 'atom.png') as image:
        mask = image.convert('L').point(lambda pixel: 255 if pixel < 100 else 0)
        left, top, right, bottom = mask.getbbox()
    assert 0.97 < (right-left)/(bottom-top) < 1.03


def test_summary_json_is_a_data_attachment_and_archived_program_is_only_copied(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    (source / "summary.json").write_text('{"energy_eV": -1.0}')
    code = "raise AssertionError('DATA_ATTACHMENT_MUST_NOT_EXECUTE')"
    (source / "analysis.py").write_text(code)
    plan, value = manifest(workspace)
    value["nodes"][0]["structures"].append({"path": "summary.json", "provenance": "collection_snapshot"})
    value["nodes"][0]["attachments"] = [{"path": "analysis.py", "provenance": "collection_snapshot"}]
    plan.write_text(json.dumps(value))
    receipt = module.build_review(str(source), str(plan))
    assert receipt["previews"] == 2
    assert not any(w.get("kind") == "preview_unavailable" for w in receipt["warnings"])
    output = workspace / "review-output"
    report = json.loads((output / "review.json").read_text())
    attachments = report["nodes"][0]["attachments"]
    assert {a["path"] for a in attachments} == {"summary.json", "analysis.py"}
    assert (output / attachments[0]["copied_file"]).read_text() == code
    assert '数据附件 · 无结构预览' in (output / 'records.html').read_text()
    assert len(report["nodes"][0]["structures"]) == 1


@pytest.mark.parametrize("reference", ["../outside.txt", "linked.txt"])
def test_data_attachments_keep_the_archive_boundary(packaged, reference):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    outside = workspace / "outside.txt"
    outside.write_text("outside selected archive")
    (source / "linked.txt").symlink_to(outside)
    plan, value = manifest(workspace)
    value["nodes"][0]["attachments"] = [{"path": reference, "provenance": "collection_snapshot"}]
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="escapes"):
        module.build_review(str(source), str(plan))
    assert not (workspace / "review-output").exists()


def test_failed_model_only_run_can_generate_a_report_without_invented_actions(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    (source / "tool calls.json").write_text('[]')
    (source / "sandbox logs.json").write_text('[]')
    run = json.loads((source / "run.json").read_text())
    (source / "run.json").write_text(json.dumps({**run, "status": "failed", "error": "API unavailable"}))
    inspected = module.inspect_archive(str(source))
    result = module.build_review(str(source), inspected["manifest_file"])
    assert result["nodes"] == result["operations"] == result["previews"] == 0
    assert result["statistics"]["model_attempts_including_compaction"] == 2
    assert 'API unavailable' in (workspace / "review-input.json").read_text()
    ElementTree.fromstring((workspace / "review-output/flowchart.svg").read_text())


def test_prepare_then_incremental_notes_delivers_step_files_without_rewriting_native_ids(packaged):
    module, scripts, workspace = packaged
    source = native_fixture(workspace)
    tools = json.loads((source / 'tool calls.json').read_text())
    tools[0]['response']['measurements'] = {'artifacts': [{'path': 'candidate.extxyz'}]}
    tools[1]['arguments'] = {'structure_path': 'candidate.extxyz'}
    tools.insert(1, {'id': 'read1', 'name': 'read_modelling_source', 'state': 'returned',
        'arguments': {'script_path': 'build.py'}, 'response': {'text': 'archived code, never execute'}})
    (source / 'tool calls.json').write_text(json.dumps(tools))
    prepared = module.prepare_review(str(source))
    assert prepared['nodes'] == 2 and prepared['operations'] == 3 and prepared['previews'] == 2
    notes = workspace / prepared['notes_file']
    assert notes.stat().st_size < 300
    initial = workspace / prepared['report']
    assert initial.is_file() and prepared['explanation_status'] == 'incomplete'
    first = module.update_review_notes(str(source), prepared['notes_file'], {'steps': {'A1:T001': {
        'purpose': '比较候选', 'finding': '退出成功但自报检查未过', 'follow_up': '随后只读源码再验证；使用关系不明',
        'basis': 'inferred', 'evidence': ['A1:T001', 'A1:C001']}}})
    assert first['unexplained_operations'] == 2
    module.update_review_notes(str(source), prepared['notes_file'], {'steps': {'A1:T002': {
        'purpose': '读取源码', 'finding': '取得归档文本', 'follow_up': '后续验证；未证明依赖此次读取', 'basis': 'inferred'}}})
    saved = notes.read_bytes()
    with pytest.raises(ValueError, match='identities|editable'):
        module.update_review_notes(str(source), prepared['notes_file'], {'steps': {'A1:T001': {'run_id': 'forged'}}})
    assert notes.read_bytes() == saved
    command = subprocess.run([sys.executable, str(scripts / 'log_review.py'), 'update_review_notes',
        '--folder', str(source), '--notes-file', prepared['notes_file'],
        '--updates', json.dumps({'summary': 'CLI增量说明，保留已有步骤'}, ensure_ascii=False)],
        cwd=workspace, text=True, capture_output=True, timeout=30)
    assert command.returncode == 0, command.stderr or command.stdout
    assert json.loads(command.stdout)['unexplained_operations'] == 1
    evidence = module.read_review_step(str(source), 'A1:T002', '/response/text', limit=8)
    assert evidence['text'] == 'archived' and evidence['next_offset'] == 8
    result = module.build_review(str(source), str(notes), 'review-final')
    report = json.loads((workspace / 'review-final/review.json').read_text())
    assert result['unexplained_operations'] == 1
    assert report['operations'][0]['annotation']['purpose'] == '比较候选'
    assert report['operations'][1]['annotation']['purpose'] == '读取源码'
    assert len(report['nodes']) == 2  # Read remains in the complete timeline.
    for node in report['nodes']:
        structure = node['structures'][0]
        assert (workspace / 'review-final' / structure['copied_file']).read_bytes() == (source / 'candidate.extxyz').read_bytes()
        assert structure['provenance'] == 'collection_snapshot'
        assert (workspace / 'review-final' / structure['preview']).is_file()
    svg = (workspace / 'review-final/flowchart.svg').read_text()
    assert 'stroke-dasharray' in svg and '<image' in svg
    assert initial.read_bytes()  # Updating notes does not overwrite the initial report.


def test_same_path_later_producer_cannot_supply_an_earlier_step_image(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    tools = json.loads((source / 'tool calls.json').read_text())
    tools[0]['response']['measurements'] = {'artifacts': [{'path': 'candidate.extxyz'}]}
    tools[1] = {'id': 't2', 'name': 'patch_modelling_step', 'state': 'returned',
        'response': {'execution': {'command_id': 'c2', 'exit_code': 0, 'stdout': '', 'stderr': ''},
            'measurements': {'artifacts': [{'path': 'candidate.extxyz'}]}}}
    (source / 'tool calls.json').write_text(json.dumps(tools))
    prepared = module.prepare_review(str(source))
    report = json.loads((workspace / 'review-initial/review.json').read_text())
    assert report['nodes'][0]['structures'][0]['provenance'] == 'missing'
    assert 'preview' not in report['nodes'][0]['structures'][0]
    assert report['nodes'][1]['structures'][0]['provenance'] == 'collection_snapshot'
    assert prepared['previews'] == 1


def test_command_result_outliving_recent_history_is_valid_in_nodes_and_explanations(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    tools = json.loads((source / 'tool calls.json').read_text())
    tools[0]['response']['execution'].update(stdout='finished', stderr='', duration_seconds=3.5)
    tools[0]['response']['measurements'] = {'fake': {'command_id': 'not-a-native-command', 'exit_code': 0, 'stdout': 'model output'}}
    (source / 'tool calls.json').write_text(json.dumps(tools))
    (source / 'sandbox logs.json').write_text('[]')
    plan, value = manifest(workspace)
    value['operations'] = [{'run_id': 'r1', 'tool_id': 't1', 'basis': 'stated',
        'evidence': [{'run_id': 'r1', 'kind': 'command', 'id': 'c1'}]}]
    plan.write_text(json.dumps(value))
    result = module.build_review(str(source), str(plan))
    assert result['statistics']['commands'] == 1
    assert result['statistics']['command_seconds'] == 3.5
    assert any(w.get('kind') == 'command_history_recovered' for w in result['warnings'])
    value['operations'][0]['evidence'] = [{'run_id': 'r1', 'kind': 'command', 'id': 'missing-1'},
        {'run_id': 'r1', 'kind': 'command', 'id': 'missing-2'}]
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError) as caught:
        module.build_review(str(source), str(plan), 'refused')
    assert [i['path'] for i in caught.value.issues] == ['/operations/0/evidence/0', '/operations/0/evidence/1']
    assert not (workspace / 'refused').exists()


def test_packaged_cli_validation_returns_locatable_json_not_a_traceback(packaged):
    _, scripts, workspace = packaged
    source = native_fixture(workspace)
    plan, value = manifest(workspace)
    value['nodes'][0]['command_id'] = 'wrong-command'
    plan.write_text(json.dumps(value))
    result = subprocess.run([sys.executable, str(scripts / 'log_review.py'), 'build_review',
        '--folder', str(source), '--manifest-file', str(plan)], cwd=workspace,
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 1 and 'Traceback' not in result.stderr
    error = json.loads(result.stdout)['error']
    assert error['issues'][0]['path'] == '/nodes/0/command_id'
    assert error['issues'][0]['recorded_command_ids'] == ['c1']


def test_unassociated_command_and_durable_progress_remain_explainable(packaged):
    module, _, workspace = packaged
    source = native_fixture(workspace)
    (source / 'sandbox logs.json').write_text(json.dumps([
        {'id': 'c1', 'exit_code': 0}, {'id': 'unassociated', 'exit_code': 0, 'argv': ['convert', '--file-name', 'candidate.extxyz']}]))
    (source / 'run activity.json').write_text(json.dumps([{'identity': 'progress:early', 'timestamp': '2026-11-01T00:00:01Z',
        'metadata': {'type': 'agent_progress', 'kind': 'status', 'text': 'Early native progress beyond lifecycle preview'}}]))
    result = module.prepare_review(str(source))
    assert result['remaining_command_steps'] == ['A1:C002']
    assert 'Early native progress beyond lifecycle preview' in (workspace / 'review-initial/records.html').read_text()
    updated = module.update_review_notes(str(source), result['notes_file'], {'steps': {'A1:C002': {
        'purpose': '用途仅能依据argv推断', 'finding': '原生命令退出0，工具关联缺失',
        'follow_up': '后续依赖未在记录中明确', 'basis': 'unknown', 'evidence': ['A1:C002'],
        'node': {'title': '未关联工具的实际命令'}}}})
    assert updated['remaining_command_steps'] == []
    built = module.build_review(str(source), result['notes_file'], 'final')
    assert built['remaining_command_steps'] == []
    html = (workspace / 'final/records.html').read_text()
    assert '用途仅能依据argv推断' in html
    assert (workspace / 'final/raw/attempt-01/run activity.json').read_bytes() == (source / 'run activity.json').read_bytes()
