"""Two end-to-end contracts: using a toolbox and distributing a curated one."""
import importlib
import base64
import io
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.main import create_app
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from backend.tests.conftest import create_node
from open_agent_world.skill_packages import Skill, SkillPackagePlugin


def edit(client, node, action, arguments):
    url = f"/api/nodes/{node['id']}"
    current = client.get(url + "/document").json()
    response = client.post(url + f"/actions/{action}", json={"arguments": arguments, "expected_revision": current["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_toolbox_editing_progressive_read_and_legion_copy(client):
    node = create_node(client, "oaw.skills")
    initial = client.get(f"/api/nodes/{node['id']}/document").json()
    assert initial["value"]["skills"] == []
    edit(client, node, "configure", {"instructions": "Check the result before handing it over."})
    skill = Skill(id="review", name="Review", description="Review a patch", instructions="Read the diff and trace the changed behavior.",
                  files={"checklist.md": "Check the public API."}, defaults={"focus": "behavior"}).model_dump()
    skill = edit(client, node, "upsert", skill)["value"]["skills"][0]
    skill_id = skill["node_id"]
    agent = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": node["id"], "relationship": "oaw.skills.use"}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tool = client.portal.call(provider.list_tools, agent["id"])[0]
    def read(arguments):
        return client.portal.call(provider.invoke_tool, agent["id"], tool.capability_id, {"toolbox": node["id"], **arguments})
    listing = read({})
    assert listing["instructions"] == "Check the result before handing it over."
    assert listing["skills"] == [{"id": skill_id, "name": "Review", "description": "Review a patch"}]
    assert read({"skill_id": skill_id})["skill"] == skill
    assert read({"skill_id": skill_id, "file_path": "checklist.md"})["file"]["content"] == "Check the public API."
    edit(client, node, "upsert", {**skill, "instructions": "Follow the updated review procedure."})
    assert read({"skill_id": skill_id})["skill"]["instructions"] == "Follow the updated review procedure."

    template = client.post("/api/legions", json={"name": "Review bench", "node_ids": [node["id"], agent["id"]]}).json()
    response = client.post(f"/api/legions/{template['id']}/instances", json={"as_group": True})
    assert response.status_code == 201, response.text
    copied = next(card for card in response.json()["nodes"] if card["type"] == "oaw.skills")
    assert client.get(f"/api/nodes/{copied['id']}/document").json()["value"]["skills"][0]["files"] == skill["files"]
    copied_skill = client.get(f"/api/nodes/{copied['id']}/document").json()["value"]["skills"][0]
    edit(client, copied, "remove", {"skill_id": copied_skill["node_id"]})
    assert len(read({})["skills"]) == 1
    client.delete(f"/api/edges/{edge['id']}")
    with pytest.raises(PermissionDeniedError):
        read({})


def test_exported_plugin_owns_cards_and_updates_only_new_instances(client, tmp_path, monkeypatch):
    node = create_node(client, "oaw.skills")
    edit(client, node, "configure", {"package_id": "example.review", "name": "Review Bench", "author": "Example", "version": "1.0.0"})
    asset = {"data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\xff").decode(), "media_type": "image/png"}
    edit(client, node, "upsert", {"id": "review", "name": "Review", "instructions": "Original method.",
        "files": {"scripts/check.py": "print('review')\n", "assets/logo.png": asset, "templates/report.md": "# Findings"},
        "directories": ["output"], "defaults": {"language": "English", "checks": {"limit": 3, "enabled": True}, "formats": ["md", "txt"]}})
    download = client.get(f"/api/nodes/{node['id']}/document/downloads/plugin")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(download.content)) as archive:
        prefix = next(name.removesuffix("SKILL.md") for name in archive.namelist() if name.endswith("/SKILL.md"))
        assert archive.read(prefix + "scripts/check.py") == b"print('review')\n"
        assert archive.read(prefix + "assets/logo.png") == base64.b64decode(asset["data_base64"])
        assert prefix + "output/" in archive.namelist()
        archive.extractall(tmp_path / "export")
    root = tmp_path / "export" / "oaw-toolbox-example-review"
    monkeypatch.syspath_prepend(str(root / "src"))
    plugin = importlib.import_module("oaw_toolbox_example_review").create_plugin()
    package = plugin.package
    assert plugin.descriptor.id == "example.review"
    assert package.skills[0].instructions == "Original method."
    assert package.skills[0].files["assets/logo.png"].data_base64 == asset["data_base64"]
    assert package.skills[0].defaults["checks"] == {"limit": 3, "enabled": True}
    settings = Settings.for_data_root(tmp_path / "installed-world")
    ids = []
    for version in ("1.0.0", "2.0.0"):
        registry = load_plugin_registry()
        if version == "2.0.0":
            package = package.model_copy(update={"version": version, "skills": [Skill(id="review", name="Review", instructions="New method.")]})
            plugin = SkillPackagePlugin(package)
        registry.install(plugin)
        services = create_services(settings, plugins=registry)
        try:
            with TestClient(create_app(settings, services=services)) as installed:
                catalog = installed.get("/api/catalog").json()
                definition = next(item for item in catalog["node_types"] if item["id"] == "example.review.toolbox")
                assert definition["plugin_id"] == "example.review"
                current = create_node(installed, "example.review.toolbox")
                ids.append(current["id"])
                value = installed.get(f"/api/nodes/{current['id']}/document").json()["value"]
                assert value["source"] == {"plugin_id": "example.review", "version": version}
                skill_id = value["skills"][0]["node_id"]
                if version == "1.0.0":
                    agent = create_node(installed, "agent")
                    installed.post("/api/edges", json={"source": agent["id"], "target": current["id"], "relationship": "example.review.toolbox.use"})
                    provider = WorldAgentCapabilityProvider(services)
                    tool = installed.portal.call(provider.list_tools, agent["id"])[0]
                    contents = installed.portal.call(provider.invoke_tool, agent["id"], tool.capability_id, {"toolbox": current["id"], "skill_id": skill_id})
                    assert contents["skill"]["files"]["assets/logo.png"]["size_bytes"] == 10
                    file = installed.portal.call(provider.invoke_tool, agent["id"], tool.capability_id, {"toolbox": current["id"], "skill_id": skill_id, "file_path": "assets/logo.png"})
                    assert file["file"]["content"] == asset
                    edited = create_node(installed, "example.review.toolbox")
                    local_skill = installed.get(f"/api/nodes/{edited['id']}/document").json()["value"]["skills"][0]
                    edit(installed, edited, "upsert", {**local_skill, "instructions": "My local method."})
                else:
                    assert value["skills"][0]["instructions"] == "New method."
                    assert installed.get(f"/api/nodes/{ids[0]}/document").json()["value"]["skills"][0]["instructions"] == "Original method."
                    local = installed.get(f"/api/nodes/{edited['id']}/document").json()["value"]
                    assert local["skills"][0]["instructions"] == "My local method."
                    assert local["source"]["version"] == "1.0.0"
        finally:
            services.close()

def test_direct_skill_connection_stays_scoped_when_membership_changes(client):
    box = create_node(client, "oaw.skills")
    edit(client, box, "configure", {"instructions": "Shared toolbox convention"})
    edit(client, box, "upsert", {"name": "Sibling", "instructions": "Sibling-only content"})
    skill = create_node(client, "oaw.skills.skill")
    edit(client, skill, "replace", {"name": "Review", "instructions": "Direct content", "files": {"scripts/check.py": "print(1)"}})
    agent = create_node(client, "agent")
    direct = client.post("/api/edges", json={"source": agent["id"], "target": skill["id"], "relationship": "oaw.skills.skill.use"}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tool = client.portal.call(provider.list_tools, agent["id"])[0]
    def read(capability, arguments=None):
        selector = {"skill": skill["id"]} if capability.name == "read_skill" else {"toolbox": box["id"]}
        return client.portal.call(provider.invoke_tool, agent["id"], capability.capability_id, {**selector, **(arguments or {})})
    assert client.patch(f"/api/nodes/{skill['id']}", json={"parent_id": box["id"]}).status_code == 200
    result = read(tool)
    assert set(result) == {"skill"}
    assert result["skill"]["instructions"] == "Direct content"
    assert read(tool, {"file_path": "scripts/check.py"})["file"]["content"] == "print(1)"
    client.post("/api/edges", json={"source": agent["id"], "target": box["id"], "relationship": "oaw.skills.use"})
    whole = next(item for item in client.portal.call(provider.list_tools, agent["id"]) if item.capability_id != tool.capability_id)
    assert len(read(whole)["skills"]) == 2
    assert read(whole)["instructions"] == "Shared toolbox convention"
    assert client.patch(f"/api/nodes/{skill['id']}", json={"parent_id": None}).status_code == 200
    assert len(read(whole)["skills"]) == 1
    assert read(tool) == result
    client.delete(f"/api/edges/{direct['id']}")
    with pytest.raises(PermissionDeniedError):
        read(tool)

def test_existing_embedded_toolbox_migrates_to_live_skills(client):
    from backend.node_containers import migrate_collections
    from backend.state import StateContext
    services = client.app.state.services
    box = create_node(client, "oaw.skills")
    scope = services.state.ensure_scope("node_document", box["id"], schema_id="core.node_document")
    current = services.state.resolve(StateContext((scope,)), "document")
    legacy = {**current.value, "skills": [Skill(id="old", name="Existing", instructions="Keep my edits", files={"scripts/check.py": "print(1)"}).model_dump()]}
    services.state.set(scope, "document", legacy, expected_revision=current.revision)
    migrate_collections(services)
    value = client.get(f"/api/nodes/{box['id']}/document").json()["value"]
    member = value["skills"][0]
    assert member["instructions"] == "Keep my edits"
    assert member["files"] == {"scripts/check.py": "print(1)"}
    assert client.get(f"/api/nodes/{member['node_id']}").json()["parent_id"] == box["id"]
    migrate_collections(services)
    assert client.get(f"/api/nodes/{box['id']}/document").json()["value"] == value
