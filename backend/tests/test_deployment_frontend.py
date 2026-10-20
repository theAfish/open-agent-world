"""Published Pack UIs use the normal loader without opening management APIs."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.dependencies import get_services
from backend.deployment_runtime import password_record, runtime_router
from backend.deployment_workspace import frontend_catalog, workspace_snapshot
from backend.packs.installation import PackInstallationManager
from backend.tests.test_pack_modules import distribution, zipped


@pytest.fixture
def published_frontend():
    def descriptor(owner, dependencies=()):
        return {"id": owner, "version": "1.0.0", "plugin_api_version": "1.35", "name": owner,
                "description": None, "requires_plugins": list(dependencies),
                "python_requirements": ["PRIVATE-PYTHON-REQUIREMENT"], "state": {"private": True}}

    owners = [descriptor("science.xrd", ["module.fixture"]),
              descriptor("module.fixture", ["service.transitive"]),
              descriptor("service.transitive", ["science.xrd"]), descriptor("unrelated.pack")]
    catalog = {
        "plugins": owners, "enabled_plugin_ids": [owner["id"] for owner in owners],
        "node_types": [
            {"id": "science.xrd", "plugin_id": "science.xrd", "label": "XRD", "traits": [],
             "config_schema": {"private": True}},
            {"id": "private.type", "plugin_id": "unrelated.pack", "label": "Private"},
        ],
        "relationships": [], "packs": [],
        "frontend_modules": {
            "module.fixture": {"version": "1.0.0", "api_version": 1, "distribution_id": "module.fixture.release",
                               "url": "/api/packs/module.fixture.release/versions/1.0.0/frontend/index.js"},
            "unrelated.pack": {"version": "1.0.0", "api_version": 1,
                               "url": "/api/packs/unrelated.pack/versions/1.0.0/frontend/index.js"},
        },
    }
    cards = {
        "published": SimpleNamespace(id="published", type="science.xrd", name="Published", status="idle",
                                     config={"heading": "Public title", "connection": "PRIVATE-CONNECTION"},
                                     state_scope="global", finish=None),
        "legion": SimpleNamespace(finish=None),
    }
    definition = SimpleNamespace(deployment=SimpleNamespace(sections={"results": {}}),
                                 frontend={"body": "xrd", "workspace": "xrd", "editor": "private-editor"})
    services = SimpleNamespace(world=SimpleNamespace(get_card=cards.__getitem__), plugins=SimpleNamespace(
        catalog=lambda: SimpleNamespace(model_dump=lambda **kwargs: deepcopy(catalog)),
        node_type=lambda _: definition))
    manifest = {"id": "release", "name": "Published application", "legion_id": "legion", "created_at": 1,
                "layout": {}, "panels": [], "permissions": {"published": ["results"]},
                "plugin_access": {"published": {"config_fields": ["heading"]}}}
    return manifest, services, catalog


def test_snapshot_scopes_owner_identity_and_frontend_service_dependency_closure(published_frontend):
    manifest, services, _ = published_frontend
    snapshot = workspace_snapshot(manifest, services)
    catalog = snapshot["catalog"]
    assert catalog["enabled_plugin_ids"] == ["module.fixture", "science.xrd", "service.transitive"]
    assert {owner["id"] for owner in catalog["plugins"]} == set(catalog["enabled_plugin_ids"])
    assert all(set(owner) == {"id", "version", "plugin_api_version", "name", "description"}
               for owner in catalog["plugins"])
    assert set(catalog["frontend_modules"]) == {"module.fixture"}
    assert [node["id"] for node in catalog["node_types"]] == ["science.xrd"]
    assert catalog["node_types"][0]["frontend"] == {"body": "xrd", "workspace": "xrd"}
    assert snapshot["cards"][0]["config"] == {"heading": "Public title"}
    assert "PRIVATE" not in json.dumps(snapshot)
    assert "config_schema" not in catalog["node_types"][0]
    assert catalog["relationships"] == catalog["packs"] == []


def test_scope_excludes_disabled_and_unpublished_service_owners(published_frontend):
    manifest, services, catalog = published_frontend
    catalog["enabled_plugin_ids"].remove("module.fixture")
    scope = frontend_catalog(manifest, services)
    assert scope["enabled_plugin_ids"] == ["science.xrd"]
    assert scope["frontend_modules"] == {}
    manifest["permissions"].clear()
    assert frontend_catalog(manifest, services) == {"plugins": [], "enabled_plugin_ids": [], "frontend_modules": {}}


def test_installed_frontend_assets_require_session_published_scope_and_verified_active_bytes(tmp_path, published_frontend):
    manifest, services, catalog = published_frontend
    # Use the real installed-asset verifier, including worker/CSS assets and the
    # immutable database checksums. No Pack Python code is imported for this test.
    with ZipFile(io.BytesIO(distribution())) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != "checksums.json"}
    files.update({"frontend/chunks/worker.js": b"postMessage(42)", "frontend/chunks/module-worker.mjs": b"postMessage(42)",
                  "frontend/assets/style.css": b".fixture{color:red}", "frontend/assets/document.html": b"<script>bad()</script>"})
    files["checksums.json"] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}).encode()
    manager = PackInstallationManager(tmp_path)
    manager.install(zipped(files))
    loaded = manager.selected()
    manager.registry = SimpleNamespace(installed_packs=loaded, is_enabled=lambda owner: owner in catalog["enabled_plugin_ids"])
    services.pack_installations = manager
    manifest["password"] = password_record("test-operator-password")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: services
    app.include_router(runtime_router(manifest))
    base = "/api/packs/module.fixture.release/versions/1.0.0/"
    with TestClient(app) as client:
        assert client.get(base + "frontend/index.js").status_code == 401
        assert client.post("/api/deployment/session", json={"password": "test-operator-password"}).status_code == 200
        for asset, media in (("frontend/index.js", "text/javascript"), ("frontend/chunks/worker.js", "text/javascript"),
                             ("frontend/chunks/module-worker.mjs", "text/javascript"),
                             ("frontend/assets/style.css", "text/css")):
            response = client.get(base + asset)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith(media)
            assert response.headers["x-content-type-options"] == "nosniff"
            policy = response.headers["content-security-policy"]
            if media == "text/javascript":
                assert "script-src 'self' 'wasm-unsafe-eval'" in policy
                assert "connect-src 'self'" in policy
                assert "sandbox" not in policy
                assert "'unsafe-eval'" not in policy
            else:
                assert "sandbox" in policy
        assert client.get(base + "frontend/assets/document.html").headers["content-security-policy"] == \
            "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        for path in (
            base + "backend/fixture.whl", base + "manifest.json", base + "frontend/missing.js",
            base + "frontend/%2E%2E/backend/fixture.whl",
            base.replace("1.0.0/", "2.0.0/") + "frontend/index.js",
            base.replace("module.fixture.release", "module.fixture") + "frontend/index.js",
            "/api/packs/unrelated.pack/versions/1.0.0/frontend/index.js",
        ):
            assert client.get(path).status_code == 404, path
        assert client.get("/api/packs").status_code == 404
        assert client.post("/api/packs/install", content=b"invalid").status_code == 404
        assert client.delete("/api/packs/module.fixture.release").status_code == 404
        catalog["enabled_plugin_ids"].remove("module.fixture")
        assert client.get(base + "frontend/index.js").status_code == 404
        catalog["enabled_plugin_ids"].append("module.fixture")
        manager.version_path("module.fixture.release", "1.0.0").joinpath("frontend/index.js").write_bytes(b"tampered")
        assert client.get(base + "frontend/index.js").status_code == 404
        assert client.delete("/api/runtime-app/session").status_code == 200
        assert client.get(base + "frontend/chunks/worker.js").status_code == 401
