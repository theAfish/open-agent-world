"""Literature instructions use the real scoped Skill and portable export contracts."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.main import create_app
from backend.plugins.builtin import create_builtin_registry
from backend.services import create_services
from backend.tests.conftest import create_node
from open_agent_world.plugin_api import PluginDescriptor

# This suite installs only the helper under test into a fresh registry. It does
# not discover the working checkout's plugins or touch a running application.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins/literature/src"))
from oaw_literature.skills import (  # noqa: E402
    PACKAGE_ID, PACKAGE_VERSION, SKILL_IDS, TOOLBOX_TYPE,
    literature_skill_package, register_literature_skills,
)


class LiteratureSkillsFixture:
    descriptor = PluginDescriptor(id=PACKAGE_ID, version=PACKAGE_VERSION,
        plugin_api_version="1.23", name="Literature skill test")

    def register(self, registration):
        register_literature_skills(registration)


@pytest.fixture
def literature_client(tmp_path):
    registry = create_builtin_registry()
    registry.install(LiteratureSkillsFixture())
    settings = Settings.for_data_root(tmp_path / "isolated-literature-world")
    services = create_services(settings, plugins=registry)
    try:
        with TestClient(create_app(settings, services=services)) as client:
            yield client
    finally:
        services.close()


def test_six_skills_have_portable_contracts_provenance_and_no_execution_claim():
    package = literature_skill_package()
    assert package.package_id == PACKAGE_ID
    assert tuple(skill.id for skill in package.skills) == SKILL_IDS
    for skill in package.skills:
        assert "## Contract and access" in skill.instructions
        assert "required capability is unavailable" in skill.instructions
        assert skill.defaults["origin"]["scientific_execution_claim"] == "none"
        assert skill.defaults["required_capabilities"]
        assert "SKILL.md" not in skill.files
        for direction in ("input", "output"):
            schema = json.loads(skill.files[f"schemas/{direction}.schema.json"])
            assert schema["$schema"].endswith("2020-12/schema")
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
        missing = json.loads(skill.files["examples/missing-capability.json"])
        assert missing["status"] == "blocked" and missing["missing_capabilities"]
        assert missing["limitations"]
        rejected = json.loads(skill.files["examples/rejected-claim.json"])
        assert rejected["synthetic"] and rejected["rejection_reason"]
        assert "not source evidence" in skill.files["references/provenance.md"]
        digest = hashlib.sha256(json.dumps({"instructions": skill.instructions, "files": skill.files},
            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        assert skill.defaults["metadata"]["content_sha256"] == digest
    package.skills[0].instructions = "Local edit"
    assert literature_skill_package().skills[0].instructions != "Local edit"


def test_real_registration_creates_independent_skills_and_revokes_direct_access(literature_client):
    client = literature_client
    box = create_node(client, TOOLBOX_TYPE)
    document = client.get(f"/api/nodes/{box['id']}/document").json()["value"]
    assert len(document["skills"]) == 6
    assert document["source"] == {"plugin_id": PACKAGE_ID, "version": PACKAGE_VERSION}
    assert {skill["id"] for skill in document["skills"]} == set(SKILL_IDS)
    assert len({skill["node_id"] for skill in document["skills"]}) == 6
    child = next(skill for skill in document["skills"] if skill["id"] == "evidence-verify")
    node = client.get(f"/api/nodes/{child['node_id']}").json()
    assert node["parent_id"] == box["id"]
    assert node["type"] == TOOLBOX_TYPE + ".skill"
    agent = create_node(client, "agent")
    response = client.post("/api/edges", json={"source": agent["id"], "target": node["id"],
        "relationship": TOOLBOX_TYPE + ".skill.use"})
    assert response.status_code == 201, response.text
    edge = response.json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = client.portal.call(provider.list_tools, agent["id"])
    assert {tool.name for tool in tools} == {"read_skill"}
    tool = tools[0]

    def read(**arguments):
        return client.portal.call(provider.invoke_tool, agent["id"], tool.capability_id,
                                  {"skill": node["id"], **arguments})

    result = read()
    assert set(result) == {"skill"}
    assert result["skill"]["id"] == "evidence-verify"
    assert "paper-method-to-skill" not in result["skill"]["instructions"]
    assert json.loads(read(file_path="schemas/output.schema.json")["file"]["content"])["properties"]["verdict"]
    assert client.patch(f"/api/nodes/{node['id']}", json={"parent_id": None}).status_code == 200
    assert read() == result
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        read()


def test_toolbox_lists_progressively_and_exported_factory_reloads_all_assets(literature_client, tmp_path, monkeypatch):
    client = literature_client
    box = create_node(client, TOOLBOX_TYPE)
    agent = create_node(client, "agent")
    response = client.post("/api/edges", json={"source": agent["id"], "target": box["id"],
        "relationship": TOOLBOX_TYPE + ".use"})
    assert response.status_code == 201, response.text
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tool = next(tool for tool in client.portal.call(provider.list_tools, agent["id"]) if tool.name == "read_skills")
    listing = client.portal.call(provider.invoke_tool, agent["id"], tool.capability_id, {"toolbox": box["id"]})
    assert len(listing["skills"]) == 6
    assert all(set(skill) == {"id", "name", "description"} for skill in listing["skills"])
    selected = client.portal.call(provider.invoke_tool, agent["id"], tool.capability_id,
        {"toolbox": box["id"], "skill_id": listing["skills"][0]["id"]})
    assert selected["skill"]["files"]["schemas/input.schema.json"]
    download = client.get(f"/api/nodes/{box['id']}/document/downloads/plugin")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    current = client.get(f"/api/nodes/{box['id']}/document").json()["value"]
    with ZipFile(io.BytesIO(download.content)) as archive:
        assert len([name for name in archive.namelist() if name.endswith("/SKILL.md")]) == 6
        assert not any("credentials" in name or "__pycache__" in name for name in archive.namelist())
        archive.extractall(tmp_path / "export")
    exported = tmp_path / "export/oaw-toolbox-research-literature/src"
    monkeypatch.syspath_prepend(str(exported))
    exported_plugin = importlib.import_module("oaw_toolbox_research_literature").create_plugin()
    assert exported_plugin.descriptor.id == PACKAGE_ID
    assert len(exported_plugin.package.skills) == 6
    for old, reloaded in zip(current["skills"], exported_plugin.package.skills):
        assert reloaded.node_id is None
        assert reloaded.id == old["node_id"]  # Exporter remaps world IDs to portable local IDs.
        assert reloaded.instructions == old["instructions"]
        assert reloaded.files == old["files"]
        assert reloaded.defaults == old["defaults"]
    registry = create_builtin_registry()
    registry.install(exported_plugin)
    assert registry.node_type(TOOLBOX_TYPE).container.member_type == TOOLBOX_TYPE + ".skill"
