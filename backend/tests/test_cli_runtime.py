"""External CLI reads the same persistent Run result after a server restart."""
import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient
import httpx

from backend.config import Settings
from backend.main import create_app
from backend.runs import RunStatus


def test_run_result_survives_restart_and_missing_run_is_not_found(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path), agent_runtime="core.mock", sandbox_runtime=None)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/nodes", json={"type": "agent", "name": "CLI tester"})
        assert response.status_code == 201, response.text
        agent_id = response.json()["id"]
        manager = app.state.services._require_run_manager()
        run = manager.store.create(agent_id=agent_id, runtime_provider_id="core.mock", caller_kind="api")
        scope = app.state.services.state.ensure_scope("run", run.run_id, schema_id="core.run")
        app.state.services.state.set(scope, "output_text", "Persisted answer", run_id=run.run_id)
        client.portal.call(manager.transition_run, run.run_id, RunStatus.RUNNING)
        client.portal.call(manager.transition_run, run.run_id, RunStatus.SUCCEEDED)
        assert client.get(f"/api/runs/{run.run_id}/result").json() == {
            "run_id": run.run_id, "agent_id": agent_id, "status": "succeeded", "text": "Persisted answer", "error": None,
        }
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/runs/{run.run_id}/result").json()["text"] == "Persisted answer"
        assert client.get("/api/runs/missing/result").status_code == 404


def test_instance_identity_is_only_available_to_the_management_client(tmp_path):
    app = create_app(replace(Settings.for_data_root(tmp_path), sandbox_runtime=None))
    app.state.local_instance = {"id": "registered-instance", "url": "http://127.0.0.1:38473"}
    with TestClient(app) as client:
        assert client.get("/api/instance").json()["id"] == "registered-instance"
        remote = TestClient(app, client=("203.0.113.10", 50123))
        assert remote.get("/api/instance").status_code == 403
        assert remote.get("/api/runs/any/result").status_code == 403


def test_launcher_registers_its_actual_port_and_removes_registration_on_exit(tmp_path, monkeypatch):
    from backend.launcher import bind_local_port, serve
    from open_agent_world.instances import list_instances

    monkeypatch.setenv("OPEN_AGENT_WORLD_INSTANCE_DIR", str(tmp_path / "instances"))
    settings = replace(Settings.for_data_root(tmp_path / "profile"), agent_runtime="core.mock",
                       sandbox_runtime=None, marketplace_url=None)

    async def exercise():
        listener = bind_local_port(0, 1)
        task = asyncio.create_task(serve(settings, listener, profile=None))
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                async with asyncio.timeout(30):
                    while not (records := list_instances()):
                        if task.done():
                            await task
                        await asyncio.sleep(.05)
                record, = records
                identity = await client.get(record["url"] + "/api/instance")
                assert identity.json() == record
                assert record["data_root"] == str(settings.data_root)
                response = await client.post(record["url"] + "/api/application/restart", headers={"X-OAW-Restart": "1"})
                assert response.status_code == 202
                assert await asyncio.wait_for(task, 30)
                assert list_instances() == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            listener.close()

    asyncio.run(exercise())
