"""Exercise the shipped CLIs with real Office engines and generated documents."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src/oaw_office"


@pytest.fixture
def cli(tmp_path):
    for name in ("word", "excel", "powerpoint"):
        target = tmp_path / "bundles" / name
        shutil.copytree(ROOT / "skills" / name / "scripts", target)
        shutil.copyfile(ROOT / "runtime.py", target / "office_runtime.py")

    def invoke(skill, *arguments, entry="office.py", success=True):
        result = subprocess.run([sys.executable, "-B", str(tmp_path / "bundles" / skill / entry), *arguments],
            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return json.loads(result.stdout if success else result.stderr)
    return invoke


def write_spec(tmp_path, data):
    (tmp_path / "spec.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return "spec.json"


def test_word_create_read_edit_preserves_source_and_block_order(cli, tmp_path):
    payload = {"blocks": [{"type": "paragraph", "text": "研究报告", "style": "Title"},
        {"type": "table", "rows": [["Sample", "Value"], ["A1", "10"]]},
        {"type": "paragraph", "text": "Original conclusion"}]}
    cli("word", "create", "--spec", write_spec(tmp_path, payload), "--output", "report.docx")
    original = (tmp_path / "report.docx").read_bytes()
    first = cli("word", "inspect", "--input", "report.docx", "--limit", "2")
    assert first["blocks"][0]["text"] == "研究报告"
    assert [block["kind"] for block in first["blocks"]] == ["paragraph", "table"]
    assert first["truncated"] and first["next_offset"] == 2
    last = cli("word", "inspect", "--input", "report.docx", "--offset", "2")
    assert last["blocks"][0]["index"] == 1 and not last["truncated"]
    edits = {"paragraphs": [{"index": 1, "text": "Updated conclusion"}],
             "cells": [{"table": 0, "row": 1, "column": 1, "text": "12"}]}
    result = cli("word", "edit", "--input", "report.docx", "--spec", write_spec(tmp_path, edits), "--output", "edited.docx")
    assert result["structurally_verified"] and not result["visually_verified"]
    inspected = cli("word", "inspect", "--input", "edited.docx")
    assert inspected["blocks"][1]["rows"][1][1] == "12"
    assert inspected["blocks"][2]["text"] == "Updated conclusion"
    assert (tmp_path / "report.docx").read_bytes() == original


def test_excel_keeps_formulas_and_exposes_missing_caches_and_windows(cli, tmp_path):
    payload = {"sheets": [{"name": "Results", "rows": [["Value", "Double"], [10, "=A2*2"], [15, "=A3*2"]]},
                          {"name": "Notes", "rows": [["Source"], ["Original experiment"]]}]}
    cli("excel", "create", "--spec", write_spec(tmp_path, payload), "--output", "data.xlsx")
    preview = cli("excel", "inspect", "--input", "data.xlsx", "--rows", "2", "--columns", "1")
    assert preview["truncated"] and preview["next_row"] == 3 and preview["next_column"] == 2
    assert [s["name"] for s in preview["sheets"]] == ["Results", "Notes"]
    values = cli("excel", "inspect", "--input", "data.xlsx", "--start-row", "2", "--start-column", "2")
    assert values["rows"][0][0] == {"address": "B2", "value": None, "formula": "=A2*2", "cache_missing": True}
    assert values["missing_formula_caches"] == 2 and not values["truncated"]
    original = (tmp_path / "data.xlsx").read_bytes()
    edits = {"cells": [{"sheet": "Results", "cell": "A2", "value": 12}]}
    cli("excel", "edit", "--input", "data.xlsx", "--spec", write_spec(tmp_path, edits), "--output", "edited.xlsx")
    result = cli("excel", "inspect", "--input", "edited.xlsx")
    assert result["rows"][1][0]["value"] == 12
    assert result["rows"][1][1]["formula"] == "=A2*2"
    assert (tmp_path / "data.xlsx").read_bytes() == original


def test_powerpoint_create_read_and_edit_notes_and_text(cli, tmp_path):
    payload = {"slides": [{"title": "Research", "bullets": ["A1: 10", "A2: 15"], "notes": "Original notes"},
                          {"title": "Next steps", "bullets": ["Repeat"]}]}
    cli("powerpoint", "create", "--spec", write_spec(tmp_path, payload), "--output", "deck.pptx")
    original = (tmp_path / "deck.pptx").read_bytes()
    first = cli("powerpoint", "inspect", "--input", "deck.pptx", "--limit", "1")
    assert first["truncated"] and first["next_offset"] == 1
    assert first["slides"][0]["notes"] == "Original notes"
    shape = first["slides"][0]["shapes"][0]["shape_id"]
    edits = {"text": [{"slide": 1, "shape_id": shape, "text": "Updated research"}],
             "notes": [{"slide": 1, "text": "Updated notes"}]}
    cli("powerpoint", "edit", "--input", "deck.pptx", "--spec", write_spec(tmp_path, edits), "--output", "edited.pptx")
    result = cli("powerpoint", "inspect", "--input", "edited.pptx")
    assert not result["truncated"] and result["total_slides"] == 2
    assert result["slides"][0]["shapes"][0]["text"] == "Updated research"
    assert result["slides"][0]["notes"] == "Updated notes"
    assert (tmp_path / "deck.pptx").read_bytes() == original


@pytest.mark.parametrize(("skill", "module"), [("word", "docx"), ("excel", "openpyxl"), ("powerpoint", "pptx")])
def test_custom_workspace_script_can_use_prepared_engine_and_literal_arguments(cli, tmp_path, skill, module):
    script = tmp_path / "work" / "custom.py"
    script.parent.mkdir()
    script.write_text(f"import {module}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    assert cli(skill, "work/custom.py", "a b", "$(literal)", entry="run.py") == ["a b", "$(literal)"]


def test_output_overwrite_and_workspace_escape_are_rejected(cli, tmp_path):
    definition = write_spec(tmp_path, {"blocks": [{"type": "paragraph", "text": "Keep me"}]})
    cli("word", "create", "--spec", definition, "--output", "original.docx")
    original = (tmp_path / "original.docx").read_bytes()
    result = cli("word", "create", "--spec", definition, "--output", "original.docx", success=False)
    assert "already exists" in result["error"]
    result = cli("word", "create", "--spec", definition, "--output", "../escape.docx", success=False)
    assert "inside" in result["error"]
    assert (tmp_path / "original.docx").read_bytes() == original


def test_corrupt_legacy_and_oversized_inputs_fail_with_actionable_errors(cli, tmp_path):
    (tmp_path / "broken.xlsx").write_bytes(b"not a workbook")
    assert "zip" in cli("excel", "inspect", "--input", "broken.xlsx", success=False)["error"].lower()
    (tmp_path / "old.xls").write_bytes(b"legacy")
    assert "convert" in cli("excel", "inspect", "--input", "old.xls", success=False)["error"]
    with (tmp_path / "large.xlsx").open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert "64 MiB" in cli("excel", "inspect", "--input", "large.xlsx", success=False)["error"]
