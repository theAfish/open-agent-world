import asyncio
from dataclasses import replace
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend import development, poc
from backend.config import Settings
from backend.hosting import scenarios
from backend.hosting.template_updates import update_runtime
from backend.hosting.templates import initialize_runtime
from backend.hosting.worker import create_worker_app
from backend.main import create_app
from backend.plugins.builtin import create_builtin_registry
from backend.services import create_services
from backend.tests.test_hosted_worker import config, headers


def core_services(settings):
    return create_services(settings, plugins=create_builtin_registry())


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(poc, "create_services", core_services)
    directory = tmp_path / "scenarios"
    monkeypatch.setattr(scenarios, "SCENARIOS", directory)
    path = directory / "legion.json"
    with poc.open_profile("author", initialize=True) as services:
        instance = asyncio.run(poc.apply_definition(services, preset="assistant"))
        asyncio.run(poc.save_definition(services, instance["node_ids"]["group"], path))
        asyncio.run(services.shutdown())
    return path


@pytest.mark.parametrize("multiple", [False, True])
def test_git_catalog_uses_existing_authenticated_hosted_publication_and_compatible_updates(scene, tmp_path, multiple):
    if multiple:
        with poc.open_profile("author") as services:
            asyncio.run(poc.apply_definition(services, preset="coding"))
            asyncio.run(poc.save_definition(services, None, scene))
            asyncio.run(services.shutdown())
    with TestClient(create_app(Settings.for_data_root(tmp_path / "personal"), services_factory=core_services)) as client:
        assert client.get("/api/deployments").json() == []
    builder = config(tmp_path / "builder")
    with TestClient(create_worker_app(builder, services_factory=core_services)) as client:
        assert client.get("/api/deployments").status_code == 401
        entry, = client.get("/api/deployments", headers=headers(builder)).json()
        assert entry["name"] == "Git: General assistant"
        url = "/api/hosting-template/" + entry["id"]
        assert client.get(url).status_code == 401
        response = client.get(url, headers=headers(builder))
        assert response.status_code == 200, response.text
        template = response.json()
        assert template["source_release_id"] == entry["id"]
    output = tmp_path / "runtime-template.json"
    output.write_text(json.dumps(template))
    runtime = replace(builder, mode="runtime", profile_id=str(uuid4()), data_root=tmp_path / "runtime")
    asyncio.run(initialize_runtime(runtime, output, services_factory=core_services))
    manifest = json.loads((runtime.data_root / "deployment.json").read_text())
    assert len(manifest["workspaces"]) == (2 if multiple else 1)
    user_file = runtime.data_root / "projects" / "keep.txt"
    user_file.write_text("Keep user work")
    definition = poc.read_template(scene)
    agent = next(node for node in definition.blueprint.nodes if node.type == "agent")
    agent.config["system_instruction"] = "Updated through Git review"
    updated = scenarios.template_from_definition(definition)
    output.write_text(json.dumps(updated))
    asyncio.run(update_runtime(runtime, output, services_factory=core_services))
    current = json.loads((runtime.data_root / "deployment.json").read_text())
    assert current["legion_id"] == manifest["legion_id"]
    assert [w["legion_id"] for w in current["workspaces"]] == [w["legion_id"] for w in manifest["workspaces"]]
    assert current["source_release_id"] == updated["source_release_id"] != template["source_release_id"]
    assert user_file.read_text() == "Keep user work"
    with TestClient(create_worker_app(runtime, services_factory=core_services)) as client:
        assert client.get("/api/hosting-template/" + updated["source_release_id"], headers=headers(runtime)).status_code in {403, 404}


@pytest.mark.parametrize("private", [{"api_key": "secret"}, {"working_directory": "C:/private/customer"}])
def test_shared_git_definition_rejects_credentials_and_host_bindings(scene, private):
    definition = poc.read_template(scene)
    definition.blueprint.nodes[1].config.update(private)
    with pytest.raises(ValueError, match="credentials|host filesystem"):
        scenarios.template_from_definition(definition)


def test_missing_pack_cannot_be_silently_replaced_with_core(scene, tmp_path):
    definition = poc.read_template(scene)
    definition.blueprint.nodes[1].plugin_id = "missing.harness"
    scene.write_text(definition.model_dump_json())
    services = core_services(Settings.for_data_root(tmp_path / "check"))
    try:
        with pytest.raises(ValueError, match="compatible installed Packs"):
            scenarios.resolve(scenarios.catalog()[0]["id"], services)
    finally:
        asyncio.run(services.shutdown())
        services.close()
