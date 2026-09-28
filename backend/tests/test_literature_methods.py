"""A real tiny subprocess, independent baseline, portable export and KDG lineage.

Every local Paper source in this file is a synthetic fixture, not a publication.
The public principle link is an HTML source and is never assigned a PDF page.
"""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import io
import json
import subprocess
import sys
from zipfile import ZipFile

import pytest

from open_agent_world.skill_packages import SkillPackage, export_plugin
from oaw_literature.methods import (ExecutionReceipt, MethodSpec, method_gaps, method_sha256,
    method_to_skill_package, verify_execution)
from backend.tests.test_literature_evidence import source


INPUT = b"[2, 4, 6, 8]\n"
SCRIPT = '''import json, math, statistics, sys
from pathlib import Path
values = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(values, list) or len(values) < 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
    raise ValueError("At least two finite independent numeric observations are required")
result = {"mean": statistics.mean(values), "standard_error": statistics.stdev(values) / math.sqrt(len(values))}
Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False) + "\\n", encoding="utf-8")
'''


def spec(**changes):
    value = {
        "id": "fixture-mean-standard-error", "name": "Synthetic demo: mean and standard error",
        "purpose": "Exercise source-to-Skill plumbing on a controlled arithmetic example; not a literature reproduction.",
        "sources": [source()],
        "inputs": [{"name": "observations", "data_type": "array[number]", "units": "dimensionless", "description": "Independent synthetic observations"}],
        "outputs": [{"name": name, "data_type": "number", "units": "dimensionless", "description": name} for name in ("mean", "standard_error")],
        "parameters": [{"name": "ddof", "value": 1, "units": "dimensionless", "origin": "engineering_adaptation", "rationale": "Implement the sample estimator for this controlled fixture."}],
        "steps": [{"instruction": "Compute the sample mean and sample standard deviation divided by sqrt(n).", "origin": "engineering_adaptation", "rationale": "Original short implementation of the linked public statistical principle; no copied paper code."}],
        "preconditions": ["At least two finite, independent observations in the same units."],
        "constraints": ["Serial correlation, weighted samples, and finite-population corrections are outside this example."],
        "acceptance": ["Both metrics match the separate known-input baseline within 1e-12."],
        "license": "MIT", "license_notes": "Only the original example implementation; the NIST reference is linked, not republished.",
        "runtime": {"language": "python", "version": ">=3.11", "entrypoint": "scripts/mean.py", "dependencies": []},
        "implementation_files": {"scripts/mean.py": SCRIPT},
        "principle_references": [{"title": "NIST/SEMATECH: Confidence Limits for the Mean",
            "url": "https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm",
            "notes": "The mean uncertainty term uses sample standard deviation divided by sqrt(N). HTML principle source; no invented DOI or PDF page."}],
        "baseline": {"id": "four-number-exact-baseline", "input_sha256": hashlib.sha256(INPUT).hexdigest(),
            "description": "For [2,4,6,8], the exact mean is 5, squared-deviation sum 20, and SEM squared is 5/3.",
            "independence": "Expected constants calculated algebraically, separately from the statistics-library script.",
            "metrics": {"mean": {"value": 5.0, "absolute_tolerance": 1e-12, "units": "dimensionless"},
                "standard_error": {"value": 1.2909944487358056, "absolute_tolerance": 1e-12, "units": "dimensionless"}}},
    }
    return MethodSpec.model_validate({**value, **changes})


def execute_fixture(tmp_path, method, input_bytes=INPUT):
    script = tmp_path / "mean.py"
    script.write_text(method.implementation_files[method.runtime.entrypoint], encoding="utf-8")
    input_path, result_path = tmp_path / "input.json", tmp_path / "metrics.json"
    input_path.write_bytes(input_bytes)
    started = datetime.now(UTC)
    command = [sys.executable, str(script), str(input_path), str(result_path)]
    process = subprocess.run(command, capture_output=True, timeout=10, check=False)
    finished = datetime.now(UTC)
    outputs = {"metrics.json": result_path.read_bytes()} if process.returncode == 0 else {"stderr.txt": process.stderr}
    receipt = ExecutionReceipt(run_id="isolated-real-python-run", executor="controlled_python_fixture",
        method_sha256=method_sha256(method), started_at=started, finished_at=finished, command=command,
        exit_code=process.returncode, input_path="input.json", input_sha256=hashlib.sha256(input_bytes).hexdigest(),
        output_sha256={path: hashlib.sha256(data).hexdigest() for path, data in outputs.items()},
        metrics_path=next(iter(outputs)), validation_scope="Only independent synthetic [2,4,6,8], dimensionless arithmetic; no general research validation.")
    return receipt, {"input.json": input_bytes, **outputs}


def package(method, **kwargs):
    return method_to_skill_package(method, source_validator=lambda anchor: "current", allow_synthetic=True, **kwargs)


def test_no_execution_is_draft_and_missing_information_is_not_invented():
    method = spec(parameters=[{"name": "sample-correlation", "origin": "missing", "missing_reason": "Not supplied in synthetic source", "units": "dimensionless"}])
    generated = package(method)
    manifest = json.loads(generated.skills[0].files["method.json"])
    assert generated.skills[0].defaults["method_status"] == "draft"
    assert manifest["spec"]["parameters"][0]["value"] is None
    assert any("sample-correlation" in gap for gap in method_gaps(method))
    with pytest.raises(ValueError, match="extra"):
        MethodSpec.model_validate({**method.model_dump(mode="json"), "status": "validated"})
    with pytest.raises(ValueError, match="source anchor"):
        spec(parameters=[{"name": "temperature", "value": 300, "units": "K"}])


def test_actual_subprocess_validates_known_mean_and_standard_error(tmp_path):
    method = spec()
    receipt, artifacts = execute_fixture(tmp_path, method)
    assert receipt.exit_code == 0
    report = verify_execution(method, receipt, artifacts)
    assert report["status"] == "validated"
    assert all(check["passed"] for check in report["checks"])
    generated = package(method, receipt=receipt, artifacts=artifacts)
    assert generated.skills[0].defaults["method_status"] == "validated"
    assert json.loads(generated.skills[0].files["sources.json"])["synthetic_demo"] is True
    assert "validation/artifacts/metrics.json" in generated.skills[0].files
    assert "validation/receipt.json" in generated.skills[0].files
    assert "separately authorized" in generated.skills[0].instructions


def test_successful_execution_without_baseline_is_only_executable(tmp_path):
    method = spec(baseline=None)
    receipt, artifacts = execute_fixture(tmp_path, method)
    assert verify_execution(method, receipt, artifacts)["status"] == "executable"


def test_failed_process_and_disagreeing_baseline_cannot_be_validated(tmp_path):
    method = spec()
    receipt, artifacts = execute_fixture(tmp_path, method, b"[1]")
    assert receipt.exit_code != 0
    assert verify_execution(method, receipt, artifacts)["status"] == "draft"
    successful, artifacts = execute_fixture(tmp_path, method)
    bad_method = method.model_dump(mode="json")
    bad_method["baseline"]["metrics"]["mean"]["value"] = 9.0
    bad_method = MethodSpec.model_validate(bad_method)
    mismatched = successful.model_copy(update={"method_sha256": method_sha256(bad_method)})
    assert verify_execution(bad_method, mismatched, artifacts)["status"] == "executable"
    assert verify_execution(bad_method, mismatched, artifacts)["checks"][0]["passed"] is False


def test_receipt_hashes_bind_code_input_output_and_known_baseline(tmp_path):
    method = spec()
    receipt, artifacts = execute_fixture(tmp_path, method)
    changed = method.model_dump(mode="json")
    changed["implementation_files"]["scripts/mean.py"] += "\n# changed implementation\n"
    with pytest.raises(ValueError, match="different method"):
        verify_execution(changed, receipt, artifacts)
    for path in ("input.json", "metrics.json"):
        with pytest.raises(ValueError, match="changed execution artifact"):
            verify_execution(method, receipt, {**artifacts, path: b"forged"})
    with pytest.raises(ValueError, match="changed execution artifact"):
        verify_execution(method, receipt, {"input.json": INPUT})
    changed_input = b"[4,5,6]"
    claimed = receipt.model_copy(update={"input_sha256": hashlib.sha256(changed_input).hexdigest()})
    with pytest.raises(ValueError, match="independent baseline"):
        verify_execution(method, claimed, {**artifacts, "input.json": changed_input})


def test_complete_spec_with_missing_units_stays_draft_even_after_passing_run(tmp_path):
    method = spec(parameters=[{"name": "ddof", "value": 1, "origin": "engineering_adaptation", "rationale": "Fixture choice; units omitted deliberately"}])
    receipt, artifacts = execute_fixture(tmp_path, method)
    assert verify_execution(method, receipt, artifacts)["status"] == "draft"
    assert any("Missing units" in gap for gap in method_gaps(method))


@pytest.mark.parametrize("path", ["../escape.py", "C:/escape.py", "scripts\\escape.py", "/absolute.py", "scripts/con.py", "validation/report.json", "method.json"])
def test_export_cannot_overwrite_provenance_or_escape_package(path):
    with pytest.raises(ValueError):
        spec(runtime=None, implementation_files={path: "pass"})


def test_fixture_sources_cannot_enter_production_and_stale_sources_cannot_be_exported():
    with pytest.raises(ValueError, match="production"):
        method_to_skill_package(spec(), source_validator=lambda anchor: "current")
    with pytest.raises(ValueError, match="needs_relocation"):
        method_to_skill_package(spec(), source_validator=lambda anchor: "needs_relocation", allow_synthetic=True)


def test_standard_export_and_kdg_assimilation_retain_exact_sources_and_resources(tmp_path, client):
    from oaw_matcreator import knowledge
    method = spec()
    receipt, artifacts = execute_fixture(tmp_path, method)
    generated = package(method, receipt=receipt, artifacts=artifacts)
    assert SkillPackage.model_validate(generated.model_dump(mode="json")) == generated
    download = export_plugin(generated.model_dump(mode="json"))
    with ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        source_path = next(path for path in names if path.endswith("/sources.json"))
        assert json.loads(archive.read(source_path))["sources"][0]["quote_sha256"] == source()["quote_sha256"]
        assert any(path.endswith("/SKILL.md") for path in names)
        assert any(path.endswith("/validation/artifacts/metrics.json") for path in names)
    original = knowledge.Graph().model_dump(mode="json")
    graph = knowledge.assimilate(original, generated.model_dump(mode="json"), {"node_id": "method-skill-node", "source_type": "research.method"})
    assert original["snapshots"] == {} and original["entries"] == []
    entry = graph["entries"][0]
    assert entry["resources"] == [{"skill_id": graph["skills"][0]["id"], "path": "SKILL.md"}]
    assert entry["provenance"]["upstream"]["source_manifest"] == "sources.json"
    assert entry["verification"] == "unverified"  # KDG scientific trust is not silently upgraded.
    snapshot = next(iter(graph["snapshots"].values()))
    assert snapshot["package"]["skills"][0]["files"]["method.json"] == generated.skills[0].files["method.json"]
    with pytest.raises(ValueError, match="already assimilated"):
        knowledge.assimilate(graph, generated.model_dump(mode="json"), {"node_id": "same-again"})
    mutated = deepcopy(graph)
    next(iter(mutated["snapshots"].values()))["package"]["version"] = "0.999.0"
    with pytest.raises(ValueError, match="immutable"):
        knowledge.validate_update(graph, mutated)
