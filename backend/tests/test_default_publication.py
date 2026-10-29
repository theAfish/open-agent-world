import base64

from fastapi.testclient import TestClient

from backend.deploy import create_deployment
from backend.main import create_app
from backend.tests.test_deployments import PASSWORD, node, settings, sign_in


def test_xrd_publishes_without_declarations_and_runs_inside_its_workspace(tmp_path):
    source, target = tmp_path / "source", tmp_path / "runtime"
    from oaw_xrd.preset import PRESET_ID
    with TestClient(create_app(settings(source))) as client:
        response = client.post(f"/api/legions/presets/{PRESET_ID}/instances", json={})
        assert response.status_code == 201, response.text
        nodes = response.json()["nodes"]
        cards = {card["type"]: card for card in nodes}
        outside = node(client, "text", "Outside publication", content="PRIVATE")
        request = {"legion_id": cards["legion"]["id"], "name": "XRD publication"}
        readiness = client.post("/api/deployments/preview", json=request).json()
        assert readiness["ready"] and readiness["warnings"], readiness
        response = client.post("/api/deployments", json=request)
        assert response.status_code == 201, response.text
        release = response.json()
    create_deployment(source, target, release["id"], password=PASSWORD)
    with TestClient(create_app(settings(target))) as client:
        root = "/api/runtime-app/workspace"
        assert client.get(root + "/world").status_code == 401
        sign_in(client)
        world = client.get(root + "/world")
        assert world.status_code == 200, world.text
        assert outside["id"] not in world.text and "system_instruction" not in world.text
        assert {card["id"] for card in world.json()["nodes"]} == {card["id"] for card in nodes}
        canvas, owner = cards["xrd.spectrum-canvas"]["id"], cards["xrd.match"]["id"]
        document = client.get(f"{root}/nodes/{canvas}/document").json()
        spectrum = base64.b64encode("\n".join(f"{10 + i * .1} {1 + i % 7}" for i in range(30)).encode()).decode()
        imported = client.post(f"{root}/nodes/{canvas}/actions/import", json={"expected_revision": document["revision"], "arguments": {"filename": "sample.txt", "source_base64": spectrum}})
        assert imported.status_code == 200, imported.text
        assert len(imported.json()["value"]["points"]) == 30
        configured = client.patch(f"{root}/nodes/{owner}", json={"config": {"library_top_n": 7}})
        assert configured.status_code == 200, configured.text
        assert configured.json()["config"]["library_top_n"] == 7
        info = client.get(f"{root}/agents/{owner}")
        assert info.status_code == 200 and info.json()["details"]["workflow_reset_supported"], info.text
        assert "system_instruction" not in info.text
        assert client.get(f"{root}/agents/{owner}/capabilities").status_code == 200
        harness = cards["xrd.multiphase-harness"]["id"]
        assert client.post(f"{root}/nodes/{harness}/actions/overview", json={"arguments": {}}).status_code == 200
        assert client.get(f"{root}/nodes/{outside['id']}/document").status_code == 404
        assert client.patch(f"{root}/nodes/{owner}", json={"config": {"system_instruction": "override"}}).status_code == 403
        assert client.patch(f"{root}/nodes/{owner}", json={"config": {"workspace_path": "/host/private"}}).status_code == 403
        assert client.patch(f"{root}/nodes/{owner}", json={"parent_id": None}).status_code == 403
        assert client.get("/api/settings/models").status_code == 404
        assert client.put(root + "/settings/models", json={}).status_code == 405
