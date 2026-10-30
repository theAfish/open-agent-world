"""Office formation and Skill dependencies use the existing live graph boundary."""
import json
from pathlib import Path

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.tests.test_skill_runtime import runtime_client


def test_office_formation_exposes_progressive_skills_and_revokes_execution(runtime_client):
    client, _, native = runtime_client
    result = client.post("/api/legions/presets/oaw.office.workbench/instances", json={})
    assert result.status_code == 201, result.text
    nodes = result.json()["node_ids"]
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)

    def invoke(name, **arguments):
        return client.portal.call(provider.invoke_tool, nodes["agent"], "operation:" + name, arguments)

    listing = invoke("read_skills", toolbox=nodes["skills"])
    assert {skill["skill_id"] for skill in listing["skills"]} == {"word", "excel", "powerpoint"}
    selected = next(skill for skill in listing["skills"] if skill["skill_id"] == "word")
    skill = invoke("read_skills", toolbox=nodes["skills"], skill_id=selected["id"])["skill"]
    assert "content" not in skill["files"]["scripts/office.py"]
    assert skill["runtime_requirements"] == [{"owner": "oaw.office", "environment": "word", "digest": None}]
    assert client.post(f"/api/sandboxes/{nodes['sandbox']}/start").status_code == 200
    arguments = {"sandbox": nodes["sandbox"], "skill": selected["id"], "script": "scripts/office.py",
                 "argv": ["inspect", "--input", "source.docx"]}
    assert invoke("run_skill_script", **arguments)["exit_code"] == 0
    runner = Path(native.last_argv[3])
    recipe = json.loads(runner.with_name("recipe.json").read_text())
    assert recipe["python"] == ["python-docx<2,>=1.2"]
    assert runner.with_name("recipe.json").is_file()
    edges = services.world.list_edges_from(nodes["agent"])
    grant = next(edge for edge in edges if edge.relationship == "oaw.office.toolbox.use")
    assert client.delete(f"/api/edges/{grant.id}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke("run_skill_script", **arguments)


def test_office_source_declares_only_sandbox_document_engines():
    from backend.packs.spec import read_source_pack
    from backend.sandbox.pack_profiles import unsupported_profile_reason
    source = Path(__file__).resolve().parents[2] / "plugins/office"
    spec = read_source_pack(source)
    assert spec.backend.python == ()
    assert set(spec.environments) == {"word", "excel", "powerpoint"}
    for environment in spec.environments.values():
        assert unsupported_profile_reason(environment) is None
        assert environment.target == "sandbox" and environment.prepare == "on-use"
