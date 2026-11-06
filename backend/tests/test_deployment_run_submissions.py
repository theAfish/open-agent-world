from fastapi.testclient import TestClient

from backend.deploy import create_deployment
from backend.main import create_app
from backend.tests.test_deployments import PASSWORD, node, prepare_source, settings, sign_in


def test_published_run_submission_requires_current_access_for_retries(tmp_path):
    source, target = tmp_path / "source", tmp_path / "runtime"
    records = prepare_source(source)
    with TestClient(create_app(settings(source))) as builder:
        private_agent = node(builder, "agent", "Unpublished assistant")
        release = builder.post("/api/deployments", json={
            "legion_id": records["legion"]["id"], "name": "Run submission test",
        })
        assert release.status_code == 201, release.text
    create_deployment(source, target, release.json()["id"], password=PASSWORD)

    root = "/api/runtime-app/workspace/agents"
    route = f"{root}/{records['agent']['id']}/run"
    headers = {"Idempotency-Key": "published-run"}
    payload = {"prompt": "Run from the published workspace", "fresh_context": True}
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        first = client.post(route, json=payload, headers=headers)
        assert first.status_code == 202, first.text
        retry = client.post(route, json=payload, headers=headers)
        assert retry.status_code == 202, retry.text
        assert retry.json() == first.json()
        conflict = client.post(route, json={**payload, "prompt": "Different work"}, headers=headers)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "idempotency_conflict"

        hidden_route = f"{root}/{private_agent['id']}/run"
        unpublished = client.post(hidden_route, json=payload, headers=headers)
        assert unpublished.status_code == 404, unpublished.text
        assert client.delete("/api/runtime-app/session").status_code == 200
        signed_out = client.post(route, json=payload, headers=headers)
        assert signed_out.status_code == 401, signed_out.text
        assert [run.run_id for run in client.app.state.services.run_manager.list_runs()] == [first.json()["run_id"]]
