import json
from pathlib import Path
import runpy
import shutil
import tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
gate = runpy.run_path(str(ROOT / "scripts/check-release.py"))
assets = runpy.run_path(str(ROOT / "scripts/release-assets.py"))


def run(number=1, **changes):
    return {"id": number, "run_attempt": 1, "head_sha": "a" * 40,
            "event": "push", "path": ".github/workflows/ci.yml",
            "status": "completed", "conclusion": "success",
            "html_url": f"https://github.com/example/repo/actions/runs/{number}", **changes}


@pytest.mark.parametrize("changes", [
    {"conclusion": "failure"}, {"conclusion": "cancelled"},
    {"conclusion": "skipped"}, {"status": "in_progress", "conclusion": None},
])
def test_release_never_falls_back_to_older_success(changes):
    with pytest.raises(ValueError, match="latest run"):
        gate["verified_run"]([run(), run(2, **changes)], "ci.yml", "a" * 40)


@pytest.mark.parametrize("changes", [{"head_sha": "b" * 40}, {"event": "pull_request"},
                                    {"path": ".github/workflows/other.yml"}])
def test_release_requires_exact_source_and_workflow(changes):
    with pytest.raises(ValueError, match="no push/manual"):
        gate["verified_run"]([run(**changes)], "ci.yml", "a" * 40)


def test_latest_manual_validation_is_accepted():
    assert gate["verified_run"]([run(conclusion="failure"), run(2, event="workflow_dispatch")],
                                "ci.yml", "a" * 40)["id"] == 2


def test_failure_from_a_new_attempt_of_an_older_run_blocks_release():
    old_rerun = run(1, run_attempt=2, run_started_at="2026-10-20T12:00:00Z", conclusion="failure")
    newer_id = run(2, run_started_at="2026-10-20T11:00:00Z")
    with pytest.raises(ValueError, match="latest run"):
        gate["verified_run"]([newer_id, old_rerun], "ci.yml", "a" * 40)


@pytest.fixture
def version_root(tmp_path):
    for name in ("frontend/package.json", "frontend/package-lock.json", "desktop/package.json",
                 "desktop/package-lock.json", "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock",
                 "desktop/src-tauri/tauri.conf.json", "backend/pyproject.toml", "backend/uv.lock", "backend/__init__.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return tmp_path


@pytest.mark.parametrize("file", ["frontend/package.json", "desktop/package-lock.json",
                                  "backend/pyproject.toml", "backend/uv.lock", "backend/__init__.py", "desktop/src-tauri/Cargo.lock"])
def test_version_drift_is_rejected(version_root, file):
    version = gate["check_versions"](version_root)
    path = version_root / file
    if file in ("backend/uv.lock", "desktop/src-tauri/Cargo.lock"):
        name = "open-agent-world-backend" if file.startswith("backend") else tomllib.loads(
            (version_root / "desktop/src-tauri/Cargo.toml").read_text())["package"]["name"]
        path.write_text(path.read_text().replace(f'name = "{name}"\nversion = "{version}"',
                                                f'name = "{name}"\nversion = "9.8.7"', 1))
    else:
        path.write_text(path.read_text().replace(version, "9.8.7", 1))
    with pytest.raises(ValueError, match="does not match"):
        gate["check_versions"](version_root)


def test_tag_is_checked_without_rewriting_sources(version_root):
    before = {p: p.read_bytes() for p in version_root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Tag"):
        gate["check_versions"](version_root, "v99.0.0")
    assert before == {p: p.read_bytes() for p in before}


def test_prerelease_uses_equivalent_python_version(version_root):
    version = gate["check_versions"](version_root)
    for path in version_root.rglob("*"):
        if path.is_file():
            replacement = "1.2.3rc1" if "backend" in path.parts else "1.2.3-rc.1"
            path.write_text(path.read_text().replace(version, replacement))
    assert gate["check_versions"](version_root, "v1.2.3-rc.1") == "1.2.3-rc.1"


@pytest.mark.parametrize("version", ["v1.2.3", "1.2.3-rc.1"])
def test_prepare_version_updates_all_application_versions_and_preserves_dependencies(version_root, version):
    previous = gate["check_versions"](version_root)
    for folder in ("frontend", "desktop"):
        path = version_root / folder / "package-lock.json"
        data = json.loads(path.read_text())
        data["packages"]["node_modules/unrelated"] = {"version": previous}
        path.write_text(json.dumps(data, indent=2) + "\n")
    for filename in ("backend/uv.lock", "desktop/src-tauri/Cargo.lock"):
        path = version_root / filename
        path.write_text(path.read_text() + f'\n[[package]]\nname = "unrelated"\nversion = "{previous}"\n')
    before = {p: p.read_text() for p in version_root.rglob("*") if p.is_file()}
    expected = version.removeprefix("v")
    assert gate["set_version"](version, version_root) == expected
    assert gate["check_versions"](version_root, "v" + expected) == expected
    prepared = {p: p.read_text() for p in before}
    assert all(prepared[p] != before[p] for p in before)
    for folder in ("frontend", "desktop"):
        data = json.loads((version_root / folder / "package-lock.json").read_text())
        assert data["packages"]["node_modules/unrelated"]["version"] == previous
    for filename in ("backend/uv.lock", "desktop/src-tauri/Cargo.lock"):
        assert f'name = "unrelated"\nversion = "{previous}"' in (version_root / filename).read_text()
    gate["set_version"](version, version_root)
    assert prepared == {p: p.read_text() for p in before}


@pytest.mark.parametrize("version", ["v0.6", "0.6.0+metadata"])
def test_prepare_invalid_version_does_not_modify_sources(version_root, version):
    before = {p: p.read_bytes() for p in version_root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Use X.Y.Z"):
        gate["set_version"](version, version_root)
    assert before == {p: p.read_bytes() for p in before}


@pytest.fixture
def release_root(tmp_path):
    output = tmp_path / "release-assets"
    output.mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/release-notes.md").write_text("Installation notes")
    # Simulate three separate builders before merging their artifacts.
    for platform, extension in assets["PLATFORMS"].items():
        builder = tmp_path / platform
        folder = builder / "release-assets"
        folder.mkdir(parents=True)
        installer = folder / f"Open-Agent-World-1.2.3-{platform}.{extension}"
        installer.write_bytes(b"fixture installer")
        installer.with_suffix(installer.suffix + ".sha256").write_text(f"{assets['digest'](installer)}  {installer.name}\n")
        assets["record"](builder, platform, "a" * 40, "1.2.3")
        for path in folder.iterdir():
            shutil.copy2(path, output / path.name)
    (output / "release-validation.json").write_text(json.dumps(
        {"version": "1.2.3", "commit": "a" * 40, "tag": "v1.2.3", "checks": {"ci.yml": run()}}))
    return tmp_path


def test_release_manifest_and_notes_trace_all_platforms(release_root):
    assets["assemble"](release_root, "a" * 40, "1.2.3")
    result = json.loads((release_root / "release-assets/release-manifest.json").read_text())
    assert len(result["builds"]) == 3
    assert "a" * 40 in (release_root / "release-notes.generated.md").read_text()
    for name, digest in result["sha256"].items():
        assert assets["digest"](release_root / "release-assets" / name) == digest


def test_manual_preview_validates_the_same_artifacts_without_claiming_a_release_tag(release_root):
    path = release_root / "release-assets/release-validation.json"
    evidence = json.loads(path.read_text())
    evidence["tag"] = None
    path.write_text(json.dumps(evidence))
    assets["assemble"](release_root, "a" * 40, "1.2.3")
    assert "Channel: manual preview" in (release_root / "release-notes.generated.md").read_text()


@pytest.mark.parametrize("damage", ["source", "version", "missing-platform", "changed-installer", "validation"])
def test_mixed_or_damaged_release_is_rejected(release_root, damage):
    folder = release_root / "release-assets"
    path = folder / "provenance-windows-x64.json"
    data = json.loads(path.read_text())
    if damage == "source":
        data["commit"] = "b" * 40
    elif damage == "version":
        data["version"] = "1.2.4"
    path.write_text(json.dumps(data))
    if damage == "missing-platform":
        path.unlink()
    elif damage == "changed-installer":
        next(folder.glob("*.exe")).write_bytes(b"changed")
    elif damage == "validation":
        evidence = folder / "release-validation.json"
        evidence.write_text(evidence.read_text().replace("a" * 40, "b" * 40))
    with pytest.raises(ValueError):
        assets["assemble"](release_root, "a" * 40, "1.2.3")
    assert not (folder / "release-manifest.json").exists()


def test_release_builds_depend_on_quality_and_pin_source():
    jobs = yaml.safe_load((ROOT / ".github/workflows/desktop-release.yml").read_text())["jobs"]
    assert jobs["build"]["needs"] == "quality"
    assert set(jobs["release"]["needs"]) == {"quality", "build"}
    for name in ("build", "release"):
        checkout = next(step for step in jobs[name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ needs.quality.outputs.sha }}"
    for filename in ("ci.yml", "docs.yml", "desktop-check.yml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        required = workflow["jobs"]["required"]
        assert required["if"] == "always()"
    assert "continue-on-error" not in (ROOT / ".github/workflows/desktop-release.yml").read_text()
