from __future__ import annotations

from configparser import ConfigParser
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from packaging.specifiers import SpecifierSet

from backend.packs.archive import inspect_archive
from backend.packs.build import build_project, doctor
from backend.packs.planning import manifest_spec
from backend.packs.spec import read_source_pack


PREVIEW_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M0 0h20v20H0z"/></svg>'


def zip_bytes(files):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('''
[project]
name="oaw-build-example"
version="1.0.0"
dependencies=["pydantic>=2,<3"]
[project.entry-points."open_agent_world.plugins"]
example="oaw_build_example:create_plugin"
[project.entry-points."open_agent_world.setup"]
setup="oaw_build_example:setup"
[tool.hatch.build.targets.wheel]
packages=["oaw_build_example", "oaw_example_engine"]
''', encoding="utf-8")
    (root / "oaw.pack.toml").write_text('''
schema_version=3
runtime-resource-directories=["oaw_example_engine"]
[distribution]
id="example.release"
name="Build Example"
[compatibility]
plugin_api="1.23"
[plugin]
owner="example.runtime"
packs=["example.runtime.cards", "example.runtime.tools"]
node_types=["example.legacy.card"]
[dependency-groups.shared]
sources=[{path="requirements.txt",kind="requirements"}]
[environments.engine]
target="host-worker"
include=["shared"]
verify_script="verify.py"
[environments.analysis]
target="sandbox"
include=["shared"]
[features.analysis]
environments=["analysis"]
''', encoding="utf-8")
    (root / "requirements.txt").write_text("numpy==2.0.0\n", encoding="utf-8")
    (root / "verify.py").write_text("raise RuntimeError('verification only runs during install')", encoding="utf-8")
    (root / "oaw_example_engine").mkdir()
    (root / "oaw_example_engine" / "__init__.py").write_text("engine = True", encoding="utf-8")
    (root / "oaw_example_engine" / "__pycache__").mkdir()
    (root / "oaw_example_engine" / "__pycache__" / "cached.pyc").write_bytes(b"not-delivered")
    frontend = tmp_path / "compiled"
    frontend.mkdir()
    (frontend / "index.js").write_text("export default {apiVersion:1, views:{}}", encoding="utf-8")
    (frontend / "index.css").write_text(".pack { color: green; }", encoding="utf-8")
    prefix = "oaw_build_example-1.0.0.dist-info"
    files = {
        "oaw_build_example/__init__.py": "raise RuntimeError('the builder must not import code')",
        "oaw_example_engine/__init__.py": "engine = True",
        f"{prefix}/METADATA": "Metadata-Version: 2.1\nName: oaw-build-example\nVersion: 1.0.0\nRequires-Dist: pydantic>=2,<3\n",
        f"{prefix}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{prefix}/entry_points.txt": "[open_agent_world.plugins]\nexample = oaw_build_example:create_plugin\n\n[open_agent_world.setup]\nsetup = oaw_build_example:setup\n",
        f"{prefix}/RECORD": "old record",
    }
    wheel = tmp_path / "adapter.whl"
    wheel.write_bytes(zip_bytes(files))
    return root, wheel, frontend, files


def test_source_build_preserves_ids_shared_requirements_and_explicit_resources(tmp_path):
    root, wheel, frontend, _ = project(tmp_path)
    output = build_project(root, tmp_path / "result.oawpack", backend_wheel=wheel, frontend_dir=frontend)
    inspected = inspect_archive(output.read_bytes())
    source = read_source_pack(root)
    distributed = manifest_spec(inspected.manifest)
    assert distributed.owner == source.owner == "example.runtime"
    assert distributed.packs == source.packs == ("example.runtime.cards", "example.runtime.tools")
    assert distributed.node_types == source.node_types == ("example.legacy.card",)
    assert distributed.distribution_id == source.distribution_id == "example.release"
    assert inspected.manifest.backend_modules == ("oaw_build_example", "oaw_example_engine")
    assert source.environments["analysis"].python == distributed.environments["analysis"].python == ("numpy==2.0.0",)
    assert distributed.environments["engine"].verify_script == "runtime/verify.py"
    assert distributed.environments["engine"].requirement_files == ("runtime/requirements.txt",)
    assert "runtime/oaw_example_engine/__init__.py" in inspected.files
    assert not any("__pycache__" in name for name in inspected.files)
    assert "frontend/index.css" in inspected.files
    with ZipFile(io.BytesIO(inspected.files["backend/adapter.whl"])) as archive:
        prefix = "oaw_build_example-1.0.0.dist-info"
        entries = ConfigParser()
        entries.read_string(archive.read(prefix + "/entry_points.txt").decode())
        assert entries.sections() == ["open_agent_world.plugins"]
        assert entries["open_agent_world.plugins"]["example.runtime"] == "oaw_build_example:create_plugin"
        assert "sha256=" in archive.read(prefix + "/RECORD").decode()


def test_build_embeds_source_bound_visual_assets_without_loading_pack_code(tmp_path):
    import base64

    root, wheel, frontend, _ = project(tmp_path)
    (root / "assets").mkdir()
    (root / "assets/logo.svg").write_bytes(PREVIEW_SVG)
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6XChfoAAAAASUVORK5CYII="
    (root / "assets/background.svg").write_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,{png}"/></svg>'.encode())
    with (root / "oaw.pack.toml").open("a", encoding="utf-8") as stream:
        stream.write('\n[distribution.preview]\nicon="bot"\nicon_asset="assets/logo.svg"\nartwork_asset="assets/background.svg"\n')
    source = read_source_pack(root)
    output = build_project(root, tmp_path / "preview.oawpack", backend_wheel=wheel, frontend_dir=frontend)
    preview = inspect_archive(output.read_bytes()).manifest.preview
    assert preview is not None and preview.icon == "bot"
    assert base64.b64decode(preview.icon_url.split(",", 1)[1]) == PREVIEW_SVG
    assert base64.b64decode(preview.artwork_url.split(",", 1)[1]) == (root / "assets/background.svg").read_bytes()
    assert source.source_files["assets/logo.svg"] == hashlib.sha256(PREVIEW_SVG).hexdigest()


@pytest.mark.parametrize("image_path, content", [
    ("../outside.svg", PREVIEW_SVG),
    ("https://example.test/image.svg", PREVIEW_SVG),
    ("assets/fake.png", b"not an image"),
    ("assets/large.svg", b" " * (256 * 1024 + 1)),
    ("assets/active.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
], ids=["traversal", "external-url", "not-an-image", "oversize", "active-svg"])
def test_source_preview_rejects_unsafe_paths_and_images(tmp_path, image_path, content):
    root, _, _, _ = project(tmp_path)
    (root / "assets").mkdir()
    if image_path.startswith("assets/"):
        (root / image_path).write_bytes(content)
    with (root / "oaw.pack.toml").open("a", encoding="utf-8") as stream:
        stream.write(f'\n[distribution.preview]\nicon_asset="{image_path}"\n')
    with pytest.raises(ValueError):
        read_source_pack(root)


def test_build_rejects_preview_image_changed_after_source_discovery(tmp_path, monkeypatch):
    import backend.packs.build as build_module

    root, wheel, frontend, _ = project(tmp_path)
    (root / "logo.svg").write_bytes(PREVIEW_SVG)
    with (root / "oaw.pack.toml").open("a", encoding="utf-8") as stream:
        stream.write('\n[distribution.preview]\nicon_asset="logo.svg"\n')
    source = read_source_pack(root)
    (root / "logo.svg").write_bytes(PREVIEW_SVG.replace(b"20 20", b"30 30"))
    monkeypatch.setattr(build_module, "read_source_pack", lambda _: source)
    with pytest.raises(ValueError, match="Preview image changed"):
        build_project(root, tmp_path / "changed.oawpack", backend_wheel=wheel, frontend_dir=frontend)


@pytest.mark.parametrize("declaration", ["implicit", "cli", "explicit"])
def test_build_keeps_oaw_compatibility_open_unless_author_declares_an_upper_bound(tmp_path, declaration):
    root, wheel, frontend, _ = project(tmp_path)
    if declaration == "cli":
        path = root / "oaw.pack.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('[compatibility]\nplugin_api="1.23"\n', ''), encoding="utf-8")
    output = build_project(root, tmp_path / "result.oawpack", backend_wheel=wheel, frontend_dir=frontend,
        plugin_api="1.23", oaw=">=0.6.0,<2" if declaration == "explicit" else None)
    compatibility = SpecifierSet(inspect_archive(output.read_bytes()).manifest.compatibility.oaw)
    assert "0.0.1" not in compatibility
    assert "1.0" in compatibility
    assert ("2.0" in compatibility) == (declaration != "explicit")


def test_runtime_fingerprint_is_identical_after_archive_path_relocation(tmp_path):
    from backend.packs.environments import PythonEnvironmentSpec
    root, wheel, frontend, _ = project(tmp_path)
    source = read_source_pack(root)
    output = build_project(root, tmp_path / "result.oawpack", backend_wheel=wheel, frontend_dir=frontend)
    archive = inspect_archive(output.read_bytes())
    unpacked = tmp_path / "unpacked"
    for name, content in archive.files.items():
        path = unpacked / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    original = PythonEnvironmentSpec.from_declaration(source.owner, "engine", source.environments["engine"], root)
    relocated = PythonEnvironmentSpec.from_declaration(source.owner, "engine", archive.manifest.environments["engine"], unpacked)
    assert original.fingerprint == relocated.fingerprint


def test_runtime_artifact_hash_paths_are_relocated_and_verified(tmp_path):
    root, wheel, frontend, _ = project(tmp_path)
    (root / "artifact.bin").write_bytes(b"pinned artifact")
    digest = hashlib.sha256(b"pinned artifact").hexdigest()
    manifest = root / "oaw.pack.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('verify_script="verify.py"',
                        'verify_script="verify.py"\nartifact_hashes={"artifact.bin"="' + digest + '"}'), encoding="utf-8")
    output = build_project(root, tmp_path / "result.oawpack", backend_wheel=wheel, frontend_dir=frontend)
    archive = inspect_archive(output.read_bytes())
    assert archive.manifest.environments["engine"].artifact_hashes == {"runtime/artifact.bin": digest}
    (root / "artifact.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Artifact hash mismatch"):
        read_source_pack(root)


@pytest.mark.parametrize("mutation,match", [
    ("private", "private host backend"),
    ("entrygroup", "unsupported entry-point groups"),
    ("version", "version differs"),
    ("dependencies", "dependencies differ"),
    ("module", "package roots differ"),
])
def test_source_build_rejects_unpublishable_or_mismatched_adapter(tmp_path, mutation, match):
    root, wheel, frontend, files = project(tmp_path)
    prefix = "oaw_build_example-1.0.0.dist-info"
    if mutation == "private":
        files["oaw_build_example/__init__.py"] = "from backend.config import Settings"
    elif mutation == "entrygroup":
        files[f"{prefix}/entry_points.txt"] += "\n[arbitrary_hooks]\ncli=oaw_build_example:main\n"
    elif mutation == "version":
        files[f"{prefix}/METADATA"] = files[f"{prefix}/METADATA"].replace("Version: 1.0.0", "Version: 2.0.0")
    elif mutation == "dependencies":
        files[f"{prefix}/METADATA"] += "Requires-Dist: requests\n"
    else:
        files["oaw_surprise/__init__.py"] = ""
    wheel.write_bytes(zip_bytes(files))
    output = tmp_path / "result.oawpack"
    with pytest.raises(ValueError, match=match):
        build_project(root, output, backend_wheel=wheel, frontend_dir=frontend)
    assert not output.exists()


def test_standalone_cli_metadata_removed_without_removing_library_modules(tmp_path):
    root, wheel, frontend, files = project(tmp_path)
    prefix = "oaw_build_example-1.0.0.dist-info"
    files[f"{prefix}/entry_points.txt"] += "\n[console_scripts]\ncli=oaw_build_example:main\n"
    wheel.write_bytes(zip_bytes(files))
    output = build_project(root, tmp_path / 'result.oawpack', backend_wheel=wheel, frontend_dir=frontend)
    packed = inspect_archive(output.read_bytes())
    with ZipFile(io.BytesIO(packed.files['backend/adapter.whl'])) as archive:
        assert 'console_scripts' not in archive.read(f'{prefix}/entry_points.txt').decode()
        assert 'oaw_build_example/__init__.py' in archive.namelist()


def test_build_never_overwrites_existing_release(tmp_path):
    root, wheel, frontend, _ = project(tmp_path)
    output = tmp_path / "existing.oawpack"
    output.write_bytes(b"previous release")
    with pytest.raises(ValueError, match="already exists"):
        build_project(root, output, backend_wheel=wheel, frontend_dir=frontend)
    assert output.read_bytes() == b"previous release"


def test_doctor_reports_unprepared_runtime_without_creating_profile(tmp_path):
    root, _, _, _ = project(tmp_path)
    data_root = tmp_path / "nonexistent-profile"
    result = doctor(root, data_root=data_root)
    assert result["read_only"] is True
    assert result["packs"][0]["host_requirements"][0]["state"] == "available"
    states = {item["id"]: item["state"] for item in result["packs"][0]["environments"]}
    assert states == {"engine": "missing", "analysis": "on_use"}
    assert not data_root.exists()


def test_doctor_does_not_present_declared_lock_as_installed(tmp_path):
    root, _, _, _ = project(tmp_path)
    (root / "lock.txt").write_text("fixture==1 --hash=sha256:" + "a" * 64, encoding="utf-8")
    manifest = root / "oaw.pack.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('verify_script="verify.py"',
                        'verify_script="verify.py"\nlock="lock.txt"'), encoding="utf-8")
    states = {item["id"]: item["state"] for item in doctor(root)["packs"][0]["environments"]}
    assert states["engine"] == "unchecked"
    report = doctor(root, data_root=tmp_path / "fresh-profile")
    states = {item["id"]: item["state"] for item in report["packs"][0]["environments"]}
    assert states["engine"] == "missing"
    assert not (tmp_path / "fresh-profile").exists()


def test_source_dependency_owner_maps_to_distribution_and_provided_pack(tmp_path):
    root, wheel, frontend, _ = project(tmp_path)
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    (dependency / "pyproject.toml").write_text('''
[project]
name="oaw-provider"
version="1.0.0"
[project.entry-points."open_agent_world.plugins"]
provider="missing_provider:create_plugin"
''', encoding="utf-8")
    (dependency / "oaw.pack.toml").write_text('''
schema_version=3
[distribution]
id="example.dependency"
[plugin]
owner="example.provider"
packs=["example.provider.cards"]
''', encoding="utf-8")
    with (root / "oaw.pack.toml").open("a", encoding="utf-8") as stream:
        stream.write('\n[[dependencies]]\nkind="owner"\nid="example.provider"\nversion=">=1"\n')
    output = build_project(root, tmp_path / "result.oawpack", backend_wheel=wheel, frontend_dir=frontend)
    dependency = inspect_archive(output.read_bytes()).manifest.dependencies.packs[0]
    assert dependency.id == "example.provider.cards"
    assert dependency.distribution == "example.dependency"


def test_cli_doctor_and_build_project_are_available(tmp_path):
    import subprocess
    import sys
    root, wheel, frontend, _ = project(tmp_path)
    result = subprocess.run([sys.executable, "-m", "open_agent_world.pack", "doctor", str(root)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["read_only"] is True
    output = tmp_path / "cli.oawpack"
    subprocess.run([sys.executable, "-m", "open_agent_world.pack", "build-project", str(root), str(output),
                    "--backend-wheel", str(wheel), "--frontend-dir", str(frontend)],
                   capture_output=True, text=True, check=True)
    assert hashlib.sha256(output.read_bytes()).hexdigest()
