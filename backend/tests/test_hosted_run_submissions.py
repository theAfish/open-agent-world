from contextlib import AsyncExitStack

import httpx
import pytest

from backend.hosting.gateway import create_gateway
from backend.hosting.worker import WorkerConfig, create_worker_app
from backend.tests.test_hosted_gateway import ORIGIN, fixture_data


@pytest.mark.asyncio
@pytest.mark.parametrize("enqueue", [False, True])
async def test_run_submission_keys_respect_hosted_profile_and_agent_boundaries(tmp_path, enqueue):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    workers = {
        profile.upstream: create_worker_app(WorkerConfig(
            profile.profile_id, profile.user_id, profile.application_id, profile.mode,
            ORIGIN, profile.secret, tmp_path / f"worker-{index}", agent_runtime="core.mock",
        )) for index, profile in enumerate(profiles)
    }

    async def dispatch(request):
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        return await httpx.ASGITransport(app=workers[origin]).handle_async_request(request)

    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(dispatch))
    sessions = [store.create_session(user["id"]) for user in users[:2]]
    headers = [
        {"Origin": ORIGIN, "X-OAW-Instance": profile.profile_id, "Idempotency-Key": "same-run-request"}
        for profile in profiles
    ]
    agent_id = "shared-agent-id"
    url = f"/api/agents/{agent_id}/run"
    body = {"prompt": "Same request in separate profiles", "enqueue": enqueue}
    async with AsyncExitStack() as stack:
        for worker in workers.values():
            await stack.enter_async_context(worker.router.lifespan_context(worker))
        await stack.enter_async_context(gateway.app.router.lifespan_context(gateway.app))
        client = await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN,
        ))

        run_ids = []
        for session, supplied in zip(sessions, headers):
            client.cookies.set("oaw_session", session)
            # Deliberately use the same Agent ID in the two profile databases.
            agent = await client.post("/api/nodes", json={"type": "agent", "id": agent_id}, headers=supplied)
            assert agent.status_code == 201, agent.text
            assert agent.json()["id"] == agent_id
            first = await client.post(url, json=body, headers=supplied)
            assert first.status_code == 202, first.text
            run_id = first.json()["run_id"]
            run_ids.append(run_id)
            replay = await client.post(url, json=body, headers=supplied)
            assert replay.status_code == 202, replay.text
            assert replay.json() == {"accepted": True, "agent_id": agent_id, "run_id": run_id}

        alice_run, bob_run = run_ids
        assert alice_run != bob_run
        assert (await client.get(f"/api/runs/{alice_run}")).status_code == 404
        wrong_profile = await client.post(url, json=body, headers=headers[0])
        assert wrong_profile.status_code == 409
        assert wrong_profile.json()["error"]["code"] == "instance_changed"

        other_agent = await client.post("/api/nodes", json={"type": "agent"}, headers=headers[1])
        assert other_agent.status_code == 201, other_agent.text
        other_id = other_agent.json()["id"]
        other_run = await client.post(f"/api/agents/{other_id}/run", json=body, headers=headers[1])
        assert other_run.status_code == 202, other_run.text
        assert other_run.json()["run_id"] not in run_ids

        client.cookies.set("oaw_session", sessions[0])
        assert (await client.get(f"/api/runs/{bob_run}")).status_code == 404
        store.revoke_session(sessions[0])
        assert (await client.post(url, json=body, headers=headers[0])).status_code == 401
