"""Standalone commands use the same durable project operations."""
import json

import pytest

from oaw_knowledge_base.service import cli


def invoke(capsys, store, *arguments):
    code = cli.main(["--store", str(store), *arguments])
    output = capsys.readouterr()
    assert code == 0, output.err
    return json.loads(output.out)


def test_cli_folder_preview_commit_conversion_and_space_projection(tmp_path, capsys, monkeypatch):
    store = tmp_path / "store"
    materials = tmp_path / "reports" / "sample"
    materials.mkdir(parents=True)
    (materials / "report.md").write_text("# Report\nMeasured temperature 25 C.", encoding="utf-8")
    (materials / "SI.csv").write_text("sample,temperature\nA,25", encoding="utf-8")
    preview = invoke(capsys, store, "import", str(tmp_path / "reports"), "--grouping", "depth", "--depth", "2")
    assert preview["projects"][0]["file_count"] == 2
    assert invoke(capsys, store, "projects")["projects"] == []
    imported = invoke(capsys, store, "import", "--preview-token", preview["token"],
                      "--grouping", "depth", "--depth", "2", "--commit")
    project_id = imported["projects"][0]["id"]
    detail = invoke(capsys, store, "projects", project_id)["project"]
    assert detail["state"] == "ready"
    assert {item["relative_path"] for item in detail["files"]} == {"report.md", "SI.csv"}
    definition = tmp_path / "space.json"
    definition.write_text(json.dumps({"name": "Temperature", "system_prompt": "Read temperature.",
        "definition": {"type": "object", "required": ["temperature"],
                       "properties": {"temperature": {"type": "number"}}}}), encoding="utf-8")
    space = invoke(capsys, store, "spaces", "add", "--file", str(definition))["space"]
    requests = []

    def complete(messages, **options):
        requests.append(messages)
        assert "report.md" in messages[1]["content"] and "SI.csv" in messages[1]["content"]
        return '{"data":{"temperature":25}}'

    monkeypatch.setattr(cli, "_complete", complete)
    result = invoke(capsys, store, "project", project_id, "--space", space["id"])["projections"][0]
    assert result["status"] == "completed" and result["data"] == {"temperature": 25}
    repeated = invoke(capsys, store, "project", project_id, "--space", space["id"])["projections"][0]
    assert repeated["id"] == result["id"] and len(requests) == 1
    reviewed = invoke(capsys, store, "review", result["id"])["projection"]
    assert reviewed["parent_id"] == result["id"]


def test_cli_model_failure_is_recorded_and_retry_can_make_a_new_run(tmp_path, capsys, monkeypatch):
    store = tmp_path / "store"
    document = tmp_path / "report.md"
    document.write_text("# Report", encoding="utf-8")
    project = invoke(capsys, store, "import", str(document), "--grouping", "files", "--commit")["projects"][0]
    definition = tmp_path / "space.json"
    definition.write_text(json.dumps({"name": "Result", "system_prompt": "Read all records."}), encoding="utf-8")
    space = invoke(capsys, store, "spaces", "add", "--file", str(definition))["space"]

    def fail(*args, **kwargs):
        raise cli.CliError("Provider unavailable")

    monkeypatch.setattr(cli, "_complete", fail)
    assert cli.main(["--store", str(store), "project", project["id"], "--space", space["id"]]) == 1
    assert "Provider unavailable" in capsys.readouterr().err
    result = invoke(capsys, store, "call", "projections")["projections"][0]
    assert result["status"] == "failed"
    monkeypatch.setattr(cli, "_complete", lambda *a, **k: '{"data":{"records":[]}}')
    retried = invoke(capsys, store, "project", project["id"], "--space", space["id"])["projections"][0]
    assert retried["id"] != result["id"] and retried["status"] == "completed"


def test_cli_removed_arbitrary_draft_and_collection_commands():
    parser = cli.build_parser()
    for arguments in (["knowledge", "open"], ["--collection", "old", "projects"], ["groups", "list"]):
        with pytest.raises(SystemExit):
            parser.parse_args(arguments)
