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


def test_published_queued_status_and_cancel_require_the_original_caller(tmp_path):
    source, target = tmp_path / "source", tmp_path / "runtime"
    records = prepare_source(source)
    with TestClient(create_app(settings(source))) as builder:
        paused = builder.patch(f"/api/nodes/{records['legion']['id']}", json={
            "config": {"mode": "team", "paused": True},
        })
        assert paused.status_code == 200, paused.text
        release = builder.post("/api/deployments", json={
            "legion_id": records["legion"]["id"], "name": "Queued Run test",
        })
        assert release.status_code == 201, release.text
    create_deployment(source, target, release.json()["id"], password=PASSWORD)
    root = "/api/runtime-app/workspace"
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        original_cookies = dict(client.cookies)
        accepted = client.post(f"{root}/agents/{records['agent']['id']}/run", json={
            "prompt": "PRIVATE USER INPUT", "enqueue": True,
        }, headers={"Idempotency-Key": "queued-published"})
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        route = f"{root}/runs/{run_id}"
        visible = client.get(route)
        assert visible.status_code == 200, visible.text
        assert visible.json()["status"] == "created"
        assert visible.json()["lifecycle"]["execution"] == "queued"
        assert "PRIVATE USER INPUT" not in visible.text
        assert set(visible.json()) == {
            "run_id", "agent_id", "status", "created_at", "updated_at",
            "started_at", "finished_at", "lifecycle",
        }
        # A shared-password deployment session identifies this caller, not all
        # later sessions that happen to know the same password.
        sign_in(client)
        assert client.get(route).status_code == 404
        assert client.post(route + "/cancel").status_code == 404
        assert client.post(f"{root}/agents/{records['agent']['id']}/stop").status_code == 409
        client.cookies.clear()
        client.cookies.update(original_cookies)
        assert client.get(route).json()["status"] == "created"
        cancelled = client.post(route + "/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert client.get(route).json()["status"] == "cancelled"
        assert client.get(route + "/invocations/models").status_code == 404
        assert client.delete("/api/runtime-app/session").status_code == 200
        assert client.get(route).status_code == 401
