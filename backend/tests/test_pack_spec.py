"""Static declaration contracts: identity, provenance and environment boundaries."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

from backend.packs.spec import EnvironmentSpec, discover_source_packs, read_source_pack


@pytest.mark.parametrize("entry", ["spec", "manifest", "interfaces", "factory_models"])
def test_pack_models_import_in_fresh_process(entry):
    # Pytest's shared imports can hide cycles that break discovery/setup subprocesses.
    result = subprocess.run(
        [sys.executable, "-c", f"import backend.packs.{entry}; "
         "from backend.packs.manifest import PackManifest; "
         "from backend.packs.spec import PackSpec; "
         "PackManifest.model_json_schema(); PackSpec.model_json_schema()"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def source(tmp_path: Path, extra: str = "", *, pyproject_extra: str = "") -> Path:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "pyproject.toml").write_text('''
[project]
name = "example-python-package"
version = "1.2.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2,<3"]
[project.entry-points."open_agent_world.plugins"]
example = "missing_plugin_that_must_never_be_imported:create_plugin"
''' + pyproject_extra, encoding="utf-8")
    (root / "oaw.pack.toml").write_text('''
schema_version = 3
[distribution]
id = "example.release"
[plugin]
owner = "example.runtime"
packs = ["example.cards", "example.skills"]
''' + extra, encoding="utf-8")
    return root


def test_static_identity_and_host_dependencies_do_not_leak_into_sandbox(tmp_path):
    root = source(tmp_path, '''
[environments.analysis]
target = "sandbox"
python = ["numpy>=2"]
''')
    spec = read_source_pack(root)
    assert spec.distribution_id == "example.release"
    assert spec.owner == "example.runtime"
    assert spec.packs == ("example.cards", "example.skills")
    assert spec.backend.python == ("pydantic<3,>=2",)
    assert spec.environments["analysis"].python == ("numpy>=2",)
    assert "source_root" not in spec.model_dump()
    assert spec.entrypoints["example"].startswith("missing_plugin")


def test_static_pack_presentation_is_bounded_and_does_not_import_plugin(tmp_path):
    root = source(tmp_path)
    declaration = root / 'oaw.pack.toml'
    declaration.write_text(declaration.read_text(encoding='utf-8').replace(
        'id = "example.release"', 'id = "example.release"\npresentation = "pack.json"'), encoding='utf-8')
    definition = {'id': 'example.cards', 'name': 'Example', 'cards': ['example.runtime.card'], 'packaging': 'premium'}
    (root / 'pack.json').write_text(json.dumps(definition), encoding='utf-8')
    spec = read_source_pack(root)
    assert spec.presentation.packaging == 'premium'
    assert spec.presentation.cards == ('example.runtime.card',)
    assert 'pack.json' in spec.source_files
    # The same source metadata cannot advertise an unrelated Pack identity.
    (root / 'pack.json').write_text(json.dumps({**definition, 'id': 'another.pack'}), encoding='utf-8')
    with pytest.raises(ValueError, match='provided Pack'):
        read_source_pack(root)


def test_groups_reuse_project_optional_pep_and_requirements_sources(tmp_path):
    root = source(tmp_path, '''
[dependency-groups.shared]
sources = [{path="pyproject.toml", kind="group", group="COMPUTE"}]
[dependency-groups.engine]
include = ["shared"]
sources = [{path="pyproject.toml", kind="optional", group="engine"}]
[environments.engine]
target = "host-worker"
include = ["engine"]
[environments.analysis]
target = "sandbox"
include = ["shared"]
sources = [{path="requirements.txt", kind="requirements"}]
''', pyproject_extra='''
[project.optional-dependencies]
engine = ["httpx>=0.28"]
[dependency-groups]
base = ["numpy>=2"]
compute = [{include-group="base"}, "ase>=3.22"]
''')
    (root / "requirements.txt").write_text("# Sandbox additions\n-r extra.txt\nmatplotlib>=3.7 # plot\n", encoding="utf-8")
    (root / "extra.txt").write_text("scipy>=1.15\n", encoding="utf-8")
    spec = read_source_pack(root)
    assert set(spec.environments["engine"].python) == {"numpy>=2", "ase>=3.22", "httpx>=0.28"}
    assert set(spec.environments["analysis"].python) == {"numpy>=2", "ase>=3.22", "scipy>=1.15", "matplotlib>=3.7"}
    assert "pydantic<3,>=2" not in spec.environments["engine"].python
    assert set(spec.environments["analysis"].requirement_files) == {"requirements.txt", "extra.txt"}
    assert spec.requirement_origins["numpy>=2"] == ("pyproject.toml:group:base",)
    assert set(spec.source_files) == {"oaw.pack.toml", "pyproject.toml", "requirements.txt", "extra.txt"}


def test_full_project_dependency_reuse_is_explicit(tmp_path):
    root = source(tmp_path, '''
[environments.analysis]
target = "sandbox"
sources = [{path="pyproject.toml", kind="project"}]
python = ["ase"]
''')
    spec = read_source_pack(root)
    assert spec.environments["analysis"].python == ("ase", "pydantic<3,>=2")


@pytest.mark.parametrize("extra,match", [
    ('[dependency-groups.a]\ninclude=["b"]\n[dependency-groups.b]\ninclude=["a"]', "Cyclic"),
    ('[environments.analysis]\ntarget="sandbox"\ninclude=["absent"]', "Unknown Pack dependency group"),
    ('[features.analysis]\nenvironments=["absent"]', "unknown environments"),
    ('[features.analysis]\nprerequisites=["absent"]', "unknown prerequisites"),
    ('[environments.analysis]\ntarget="sandbox"\nprerequisites=["absent"]', "unknown prerequisites"),
    ('[components.backend]\npython=["httpx"]', "only in pyproject"),
    ('[components.backend]\nproject="elsewhere.toml"', "entry-point pyproject"),
    ('[environments.analysis]\ntarget="sandbox"\nverify_script="check.py"', "managed Python host worker"),
])
def test_invalid_declarations_fail_before_import(tmp_path, extra, match):
    with pytest.raises(ValueError, match=match):
        read_source_pack(source(tmp_path, extra))


@pytest.mark.parametrize("path", ["../outside.txt", "/outside.txt", "C:/outside.txt", "folder\\outside.txt", "CON", "nested/../deps.txt"])
def test_dependency_source_paths_cannot_escape_pack(tmp_path, path):
    import json
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nsources=[{kind="requirements",path=' + json.dumps(path) + '}]')
    with pytest.raises(ValueError, match="Unsafe Pack path"):
        read_source_pack(root)


def test_symlink_escape_is_rejected(tmp_path):
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nsources=[{kind="requirements",path="deps.txt"}]')
    external = tmp_path / "outside.txt"
    external.write_text("numpy", encoding="utf-8")
    try:
        (root / "deps.txt").symlink_to(external)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this user")
    with pytest.raises(ValueError, match="escapes its root"):
        read_source_pack(root)


@pytest.mark.parametrize("contents,match", [
    ("-r requirements.txt", "Cyclic requirements"),
    ("--index-url https://example.test", "Unsupported installer option"),
    ("-e .", "Unsupported installer option"),
])
def test_requirements_file_cannot_supply_installer_commands(tmp_path, contents, match):
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nsources=[{kind="requirements",path="requirements.txt"}]')
    (root / "requirements.txt").write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        read_source_pack(root)


@pytest.mark.parametrize("groups,match", [
    ('a=[{include-group="b"}]\nb=[{include-group="a"}]', "Cyclic dependency groups"),
    ('a=["numpy"]\nA=["ase"]', "Ambiguous dependency group"),
    ('a=[{other="b"}]', "Unsupported dependency group"),
])
def test_pep_dependency_groups_are_bounded_and_unambiguous(tmp_path, groups, match):
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nsources=[{kind="group",path="pyproject.toml",group="a"}]',
                  pyproject_extra="\n[dependency-groups]\n" + groups)
    with pytest.raises(ValueError, match=match):
        read_source_pack(root)


def test_digest_tracks_dependency_content_and_not_checkout_location(tmp_path):
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nsources=[{kind="requirements",path="requirements.txt"}]')
    (root / "requirements.txt").write_text("numpy==2.0.0", encoding="utf-8")
    original = read_source_pack(root)
    copied = tmp_path / "another-checkout"
    shutil.copytree(root, copied)
    assert original.spec_digest == read_source_pack(copied).spec_digest
    (root / "requirements.txt").write_text("numpy==2.1.0", encoding="utf-8")
    assert original.spec_digest != read_source_pack(root).spec_digest


def test_locked_verifier_resources_are_hashed(tmp_path):
    root = source(tmp_path, '''
[environments.engine]
target="host-worker"
lock="runtime.lock"
verify_script="verify.py"
resources=["data.json"]
verify_imports=["example.module"]
''')
    for name in ("runtime.lock", "verify.py", "data.json"):
        (root / name).write_text("content", encoding="utf-8")
    spec = read_source_pack(root)
    assert spec.environments["engine"].lock_digest == spec.source_files["runtime.lock"]
    assert {"runtime.lock", "verify.py", "data.json"} <= spec.source_files.keys()


def test_legacy_pyproject_metadata_does_not_guess_runtime_identity(tmp_path):
    root = source(tmp_path, pyproject_extra='\n[tool.open-agent-world.runtime]\npython=["ase>=3.22"]')
    (root / "oaw.pack.toml").unlink()
    spec = read_source_pack(root)
    assert spec.legacy and spec.owner is None and spec.packs == ()
    assert spec.legacy_python_requirements == ("ase>=3.22",)
    assert spec.environments["legacy-sandbox"].management == "legacy-shared"


def test_legacy_entrypoint_only_folder_remains_discoverable(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project.entry-points."open_agent_world.plugins"]\nexample="missing:create_plugin"', encoding="utf-8")
    spec = read_source_pack(root)
    assert spec.legacy and spec.version == "0" and spec.owner is None
    assert spec.distribution_id.startswith("legacy.")


def test_legacy_runtime_override_must_not_be_silent(tmp_path):
    root = source(tmp_path, '[environments.analysis]\ntarget="sandbox"\nmanagement="legacy-shared"\npython=["numpy"]',
                  pyproject_extra='\n[tool.open-agent-world.runtime]\npython=["ase"]')
    with pytest.raises(ValueError, match="Conflicting legacy"):
        read_source_pack(root)


def test_discovery_deduplicates_roots_and_rejects_duplicate_owner(tmp_path):
    root = source(tmp_path)
    assert len(discover_source_packs([tmp_path, tmp_path])) == 1
    copied = tmp_path / "second"
    shutil.copytree(root, copied)
    text = (copied / "oaw.pack.toml").read_text(encoding="utf-8").replace("example.release", "other.release")
    (copied / "oaw.pack.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate owner"):
        discover_source_packs([tmp_path])


def test_archive_environment_metadata_reuses_same_validation():
    environment = EnvironmentSpec(target="host-worker", python=("httpx>=0.28",), verify_imports=("httpx",))
    assert EnvironmentSpec.model_validate_json(environment.model_dump_json()) == environment
    with pytest.raises(ValueError, match="Python module"):
        EnvironmentSpec(target="host-worker", verify_imports=("httpx; print('bad')",))


def test_repository_sources_preserve_runtime_and_pack_ids():
    root = Path(__file__).resolve().parents[2]
    specs = {spec.owner: spec for spec in discover_source_packs([root / "plugins"])}
    assert len(specs) == 9
    assert not {"oaw.barracks", "oaw.skills", "oaw.tasks", "openai.codex"} & specs.keys()
    assert read_source_pack(root / "packs/codex").packs == ("openai.codex.default",)
    assert specs["matcreator"].packs == ("matcreator.default",)
    assert specs["atomsculptor"].packs == ("atomsculptor.default",)
    assert specs["oaw.factory"].packs == ("oaw.factory",)
    assert specs["atomsculptor"].legacy_python_requirements == ()
    assert specs["atomsculptor"].environments['modelling'].python == (
        "ase>=3.22", "matplotlib>=3.7", "numpy>=1.23", "pymatgen>=2024.0")
    assert specs["research.xrd"].environments["scientific"].management == "managed"
    assert specs["research.xrd"].environments["scientific"].prepare == "on-use"
    assert specs["matcreator"].environments["native"].target == "host-worker"
    assert "mp-api" in specs["atomsculptor"].environments["materials-project-search"].python
