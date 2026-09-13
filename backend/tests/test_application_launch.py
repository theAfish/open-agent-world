from dataclasses import replace
import json
from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend import development
from backend.application import KEY, application_snapshot
from backend.card_library import LibraryEdit
from backend.config import Settings
from backend.development import DevelopmentControl, ResetRequest, prepare_profile, reset_profile
from backend.main import create_app
from backend.services import create_services


@pytest.fixture
def dev_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "PROFILES", tmp_path / "development/profiles")
    root = prepare_profile("test")
    return replace(Settings.for_data_root(root), application_mode="development", agent_runtime="core.mock")


def test_production_serves_built_assets_without_debug_endpoints(tmp_path):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>production</html>")
    (frontend / "app.js").write_text("console.log('production')")
    with TestClient(create_app(Settings.for_data_root(tmp_path / "data"), frontend_directory=frontend)) as client:
        assert client.get("/").text == "<html>production</html>"
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/debug").status_code == 404
        assert client.post("/api/debug/reset", json={"scopes": ["all"]}).status_code in {404, 405}
        assert client.get("/api/application").json()["mode"] == "production"
        assert client.get("/database/world.sqlite3").status_code == 404


def test_preferences_survive_restart_and_reject_stale_windows(dev_settings):
    with TestClient(create_app(dev_settings)) as client:
        profile = client.get("/api/application").json()
        body = {k: profile[k] for k in ("profile_id", "generation")}
        body["changes"] = {"oaw-theme": "dark", "oaw-onboarding-v1": '{"state":{"status":"completed"}}'}
        assert client.patch("/api/application/preferences", json=body).status_code == 200
    request = ResetRequest(scopes=["tutorial"], profile_id=profile["profile_id"], generation=profile["generation"])
    backup = reset_profile(dev_settings, request)
    assert (backup / "world.sqlite3").is_file()
    with TestClient(create_app(dev_settings)) as client:
        current = client.get("/api/application").json()
        assert current["values"] == {"oaw-theme": "dark"}
        assert current["profile_id"] == profile["profile_id"]
        assert current["generation"] != profile["generation"]
        assert client.patch("/api/application/preferences", json=body).status_code == 409


def test_reset_plan_is_scoped_and_blocks_new_writes(dev_settings):
    control = DevelopmentControl(dev_settings)
    with TestClient(create_app(dev_settings, development=control)) as client:
        plan = client.post("/api/debug/plan", json={"scopes": ["workspace"]}).json()
        assert plan["scopes"] == ["tutorial", "workspace"]
        body = {"scopes": ["workspace"], "profile_id": plan["profile_id"], "generation": "wrong"}
        assert client.post("/api/debug/reset", json=body).status_code == 409
        body["generation"] = plan["generation"]
        assert client.post("/api/debug/reset", json=body).status_code == 202
        assert client.post("/api/nodes", json={"type": "text"}).status_code == 503
        assert control.pending is not None


@pytest.mark.parametrize("selected", ["workspace", "decks", "packs", "models", "all"])
def test_reset_preserves_unselected_state_and_external_workspaces(dev_settings, tmp_path, selected):
    services = create_services(dev_settings)
    library = services.card_library.read()
    pack_id = next(key for key, pack in library.packs.items() if "text" in pack.definition.cards)
    library = services.card_library.edit(LibraryEdit(action="open_pack", id=pack_id, expected_revision=library.revision))
    services.card_library.edit(LibraryEdit(action="update_deck", id="starter", entries=[{"id": "text"}], expected_revision=library.revision))
    with services.database.transaction() as db:
        db.execute("INSERT INTO application_settings VALUES ('model_connections', ?)", ('{"revision":7,"connections":[],"default_model":null}',))
    snapshot = application_snapshot(services)
    services.close()
    assets = dev_settings.data_root / "assets"
    (assets / "example.txt").write_text("managed content")
    external = tmp_path / "external-workspace"
    external.mkdir()
    (external / "keep.txt").write_text("user content")
    backup = reset_profile(dev_settings, ResetRequest(scopes=[selected], profile_id=snapshot["profile_id"], generation=snapshot["generation"]))
    assert (external / "keep.txt").read_text() == "user content"
    assert (backup / "world.sqlite3").exists()
    with closing(sqlite3.connect(dev_settings.database_path)) as db:
        models = db.execute("SELECT value_json FROM application_settings WHERE key='model_connections'").fetchone()
        assert bool(models) == (selected not in {"models", "all"})
    services = create_services(dev_settings)
    current = services.card_library.read()
    assert bool(current.collection) == (selected not in {"packs", "all"})
    assert bool(current.decks[0].entries) == (selected not in {"decks", "packs", "all"})
    assert not current.migration_pending
    services.close()
    if selected in {"workspace", "all"}:
        assert (backup / "assets/example.txt").read_text() == "managed content"
    else:
        assert (assets / "example.txt").read_text() == "managed content"


def test_reset_rejects_formal_root_and_busy_store(dev_settings, tmp_path):
    with pytest.raises(ValueError, match="checkout"):
        DevelopmentControl(replace(dev_settings, data_root=tmp_path / "formal"))
    with pytest.raises(ValueError, match="development mode"):
        DevelopmentControl(replace(dev_settings, application_mode="production"))
    with TestClient(create_app(dev_settings)) as client:
        snapshot = client.get("/api/application").json()
        from backend.errors import ConflictError
        with pytest.raises(ConflictError):
            reset_profile(dev_settings, ResetRequest(scopes=["all"], profile_id=snapshot["profile_id"], generation=snapshot["generation"]))


def test_preferences_allowlist_and_old_secret_removal(dev_settings):
    with TestClient(create_app(dev_settings)) as client:
        snapshot = client.get("/api/application").json()
        body = {k: snapshot[k] for k in ("profile_id", "generation")}
        body["changes"] = {"arbitrary-secret": "no"}
        assert client.patch("/api/application/preferences", json=body).status_code == 422
        body["changes"] = {"oaw-model-settings": json.dumps({"baseUrl": "", "models": [], "apiKey": "old-secret"})}
        assert client.patch("/api/application/preferences", json=body).status_code == 200
        assert "old-secret" not in client.get("/api/application").text


def test_workspace_reset_removes_real_cards_and_restores_files_after_move_failure(dev_settings, monkeypatch):
    with TestClient(create_app(dev_settings)) as client:
        assert client.post("/api/nodes", json={"type": "text", "content": "keep on rollback"}).status_code == 201
        snapshot = client.get("/api/application").json()
    from pathlib import Path
    original = Path.rename
    def fail_projects(path, target):
        if path == dev_settings.data_root / "projects":
            raise OSError("simulated move failure")
        return original(path, target)
    request = ResetRequest(scopes=["workspace"], profile_id=snapshot["profile_id"], generation=snapshot["generation"])
    with monkeypatch.context() as temporary:
        temporary.setattr(Path, "rename", fail_projects)
        with pytest.raises(OSError, match="simulated"):
            reset_profile(dev_settings, request)
    with TestClient(create_app(dev_settings)) as client:
        assert len(client.get("/api/world").json()["nodes"]) == 1
        assert client.get("/api/application").json()["generation"] == snapshot["generation"]
    backup = reset_profile(dev_settings, request)
    with TestClient(create_app(dev_settings)) as client:
        assert client.get("/api/world").json()["nodes"] == []
    assert (backup / "assets").is_dir()


def test_interrupted_reset_restores_archived_directories_before_startup(dev_settings):
    with TestClient(create_app(dev_settings)) as client:
        snapshot = client.get("/api/application").json()
    root = dev_settings.data_root
    (root / "assets/probe.txt").write_text("recover me")
    backup = development.PROFILES.parent / "backups" / root.name / "crash-test"
    backup.mkdir(parents=True)
    (root / "assets").rename(backup / "assets")
    (root / development.RECOVERY).write_text(json.dumps({"backup": str(backup), "generation": "never-committed", "directories": ["assets"]}))
    assert prepare_profile(root.name) == root
    assert (root / "assets/probe.txt").read_text() == "recover me"
    assert not (root / development.RECOVERY).exists()
    with TestClient(create_app(dev_settings)) as client:
        assert client.get("/api/application").json()["generation"] == snapshot["generation"]
