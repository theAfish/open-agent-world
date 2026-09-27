"""Creator artifacts cross a clean profile without code or author-local state."""
import json

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.legions.presets import preset_record
from backend.main import create_app
from backend.packs.archive import inspect_archive
from backend.packs.content import CreatorRequest, export_archive, prepare_export
from backend.packs.installation import PackInstallationManager
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs

HEADERS = {"X-OAW-Pack-Install": "1", "Content-Type": "application/vnd.oaw.pack"}
CREATOR_HEADERS = {"X-OAW-Pack-Install": "1"}


def creation(registry=None):
    registry = registry or create_builtin_registry()
    record = preset_record("coding", registry)
    request = CreatorRequest(legion_id=record.id, id="local.research", name="Research assistant")
    result, files = prepare_export(record, request, registry)
    assert result["can_export"], result
    return registry, files


def test_content_pack_install_load_and_immutable_versions(tmp_path):
    registry, files = creation()
    artifact = export_archive(files)
    pack = inspect_archive(artifact)
    assert pack.backend_entry is None
    assert pack.manifest.entrypoints is None
    assert set(pack.files) == {"manifest.json", "checksums.json", "content/legion.json", "README.md"}
    manager = PackInstallationManager(tmp_path, registry)
    assert manager.install(artifact)["restart_required"]
    assert not registry.has_plugin("local.research")
    fresh = create_builtin_registry()
    load_installed_packs(fresh, tmp_path)
    assert fresh.has_plugin("local.research")
    assert "local.research" not in fresh.frontend_modules
    assert fresh.catalog().packs[-1].cards == ()
    assert preset_record("local.research.legion", fresh).blueprint == preset_record("coding", registry).blueprint
    with pytest.raises(ValueError, match="immutable"):
        manager.install(artifact)
    manager.uninstall("local.research")
    manager.remove_version("local.research", "0.1.0")
    changed = dict(files, **{"README.md": b"changed"})
    with pytest.raises(ValueError, match="immutable"):
        manager.install(export_archive(changed))


@pytest.mark.parametrize("extra", ["backend/payload.whl", "frontend/index.js", "assets/run.py", "content/unused.json"])
def test_content_pack_cannot_smuggle_executable_or_unlisted_files(extra):
    _, files = creation()
    files[extra] = b"payload"
    with pytest.raises(ValueError, match="Unexpected Pack file"):
        export_archive(files)


@pytest.mark.parametrize("change", [
    {"entrypoints": {"backend": "backend/a.whl", "frontend": "frontend/a.js"}},
    {"runtime": {"sandbox": {"python": ["numpy"]}}},
    {"schema_version": 1},
])
def test_content_pack_rejects_code_and_runtime_install_contracts(change):
    _, files = creation()
    manifest = json.loads(files["manifest.json"])
    manifest.update(change)
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError):
        export_archive(files)


def test_missing_undeclared_and_malformed_dependencies_never_select_pack(tmp_path):
    registry, files = creation()
    manager = PackInstallationManager(tmp_path, registry)
    manifest = json.loads(files["manifest.json"])
    manifest["dependencies"]["packs"] = []
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="Undeclared"):
        manager.install(export_archive(files))
    assert manager.selected() == {}
    registry, files = creation()
    template = json.loads(files["content/legion.json"])
    template["blueprint"]["edges"][0]["target"] = "missing"
    files["content/legion.json"] = json.dumps(template).encode()
    with pytest.raises(ValueError, match="unknown node"):
        manager.install(export_archive(files))
    assert manager.selected() == {}


def test_api_export_install_in_clean_profile_and_upgrade_preserves_instances(client, tmp_path):
    agent = client.post("/api/nodes", json={"type": "agent", "name": "Researcher",
        "config": {"model": "author-private-profile", "api_key": "DO-NOT-SHARE"}}).json()
    note = client.post("/api/nodes", json={"type": "text", "name": "Instructions",
        "content": "deliberate example", "size": {"width": 350, "height": 170}}).json()
    assert client.post("/api/edges", json={"source": agent["id"], "target": note["id"], "relationship": "read"}).status_code == 201
    saved = client.post("/api/legions", json={"name": "Research team", "node_ids": [agent["id"], note["id"]]}).json()
    request = {"legion_id": saved["id"], "id": "local.research", "name": "Research Pack", "version": "0.1.0",
        "creator": {"author": "Researcher", "preparation": "Configure your model", "example": "Read the note"}}
    assert client.post("/api/packs/creator/export", json=request).status_code == 403
    checked = client.post("/api/packs/creator/inspect", json=request, headers=CREATOR_HEADERS)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_export"]
    note_key = next(n["key"] for n in checked.json()["nodes"] if n["type"] == "text")
    empty_export = client.post("/api/packs/creator/export", json=request, headers=CREATOR_HEADERS)
    assert empty_export.status_code == 200, empty_export.text
    empty = inspect_archive(empty_export.content)
    assert b"deliberate example" not in empty.files["content/legion.json"]
    assert b"DO-NOT-SHARE" not in empty.files["content/legion.json"]
    assert b"author-private-profile" not in empty.files["content/legion.json"]
    request["include_state_nodes"] = [note_key]
    response = client.post("/api/packs/creator/export", json=request, headers=CREATOR_HEADERS)
    assert response.status_code == 200, response.text
    artifact = response.content
    request["version"] = "0.2.0"
    request["creator"]["example"] = "New example"
    newer = client.post("/api/packs/creator/export", json=request, headers=CREATOR_HEADERS).content
    settings = Settings.for_data_root(tmp_path / "recipient")
    with TestClient(create_app(settings)) as recipient:
        result = recipient.post("/api/packs/inspect", content=artifact, headers=HEADERS)
        assert result.status_code == 200, result.text
        assert result.json()["trusted_code"] is False
        assert recipient.post("/api/packs/install", content=artifact, headers=HEADERS).status_code == 201
    with TestClient(create_app(settings)) as recipient:
        library = recipient.get("/api/card-library").json()
        assert library["preset_pack_ids"]["local.research.legion"] == ["local.research"]
        assert not library["packs"]["local.research"]["opened"]
        assert not any(e["id"] == "local.research.legion" for d in library["decks"] for e in d["entries"])
        opened = recipient.post("/api/card-library/actions", json={"action": "open_pack", "id": "local.research", "expected_revision": library["revision"]})
        assert opened.status_code == 200, opened.text
        placed = recipient.post("/api/legions/presets/local.research.legion/instances", json={"position": {"x": 800, "y": 100}})
        assert placed.status_code == 201, placed.text
        nodes = placed.json()["nodes"]
        restored = next(n for n in nodes if n["type"] == "text")
        assert restored["size"] == note["size"]
        text_url = f'/api/resources/{restored["id"]}/text'
        assert recipient.get(text_url).json()["content"] == "deliberate example"
        assert recipient.put(text_url, json={"content": "recipient edits"}).status_code == 200
        assert recipient.post("/api/packs/install", content=newer, headers=HEADERS).status_code == 201
    with TestClient(create_app(settings)) as recipient:
        assert recipient.get(text_url).json()["content"] == "recipient edits"
        status = recipient.get("/api/packs").json()
        assert not status["restart_required"]
        assert next(v for v in status["versions"] if v["loaded"])["creator"]["example"] == "New example"


def test_publication_blocks_structured_secrets_without_returning_their_values():
    registry = create_builtin_registry()
    record = preset_record("assistant", registry)
    record.blueprint.nodes[0].initial_shared_state = {"api_key": "SUPER-SECRET"}
    request = CreatorRequest(legion_id=record.id, id="local.test", name="Test", include_state_nodes=["group"])
    result, _ = prepare_export(record, request, registry)
    assert not result["can_export"]
    assert "SUPER-SECRET" not in json.dumps(result)
    request.include_state_nodes = []
    result, files = prepare_export(record, request, registry)
    assert result["can_export"]
    assert b"SUPER-SECRET" not in files["content/legion.json"]
