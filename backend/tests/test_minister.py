import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.agents.tools import build_scoped_tool_callables
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, RevisionConflictError
from backend.main import create_app
from backend.minister import INSTRUCTION, LEGACY_INSTRUCTION, MINISTER_TYPE, control
from backend.tests.conftest import create_node
from backend.tests.minister_runtime import runtime_services
from backend.world.models import CardCreate, CardPatch


def call(client, method, *args, **kwargs):
    async def run():
        return await method(*args, **kwargs)
    return client.portal.call(run)


def invoke(client, minister, action, **args):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    return call(client, provider.invoke_tool, minister["id"], f"operation:canvas_{action}", {"minister": minister["id"], **args})


def test_circle_queries_protect_secrets_search_and_reject_corner_cards(client):
    minister = create_node(client, MINISTER_TYPE, config={"control_radius": 400, "allow_canvas_edits": False})
    inside = create_node(client, "agent", name="Local helper", position={"x": 100, "y": 100},
                         size={"width": 96, "height": 96}, config={"api_key": "private-test-secret"})
    # Inside the enclosing square but outside the true circle, including card extents.
    corner = create_node(client, "text", name="Corner", position={"x": 350, "y": 350}, size={"width": 96, "height": 96})
    view = invoke(client, minister, "inspect")
    assert inside["id"] in {node["id"] for node in view["nodes"]}
    assert corner["id"] not in {node["id"] for node in view["nodes"]}
    assert "private-test-secret" not in str(view)
    assert view["allowed_operations"] == ["inspect"]
    assert invoke(client, minister, "inspect", query="HELPER")["total"] == 1
    assert client.get(f"/api/ministers/{minister['id']}/world", params={"query": "Corner"}).json()["total"] == 0
    with pytest.raises(PermissionDeniedError):
        invoke(client, minister, "move", node_id=inside["id"], position={"x": 120, "y": 120}, versions=view["versions"])


def test_small_tools_use_ids_live_scope_and_shared_revisions(client):
    first = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
    second = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
    agent = create_node(client, "agent", position={"x": 120, "y": 0}, size={"width": 96, "height": 96})
    observed = invoke(client, first, "inspect")["versions"]
    note = invoke(client, first, "create", type="text", name="Local note", position={"x": 200, "y": 200}, versions=observed)
    stale = invoke(client, second, "inspect")["versions"]
    invoke(client, first, "move", node_id=note["id"], position={"x": 250, "y": 200}, versions=stale)
    with pytest.raises(RevisionConflictError):
        invoke(client, second, "rename", node_id=note["id"], name="Stale", versions=stale)
    fresh = invoke(client, second, "inspect")["versions"]
    edge = invoke(client, second, "connect", source=agent["id"], target=note["id"], versions=fresh)
    invoke(client, first, "disconnect", edge_id=edge["id"], versions=invoke(client, first, "inspect")["versions"])
    observed = invoke(client, first, "inspect")["versions"]
    client.patch(f"/api/nodes/{note['id']}", json={"name": "Human edit"})
    with pytest.raises(RevisionConflictError):
        invoke(client, first, "rename", node_id=note["id"], name="Overwritten", versions=observed)
    assert client.get(f"/api/nodes/{note['id']}").json()["name"] == "Human edit"
    with pytest.raises(PermissionDeniedError):
        invoke(client, first, "move", node_id=note["id"], position={"x": 550, "y": 550}, versions=invoke(client, first, "inspect")["versions"])


def test_preflight_self_participation_private_chat_and_readiness(client):
    minister = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": False})
    private = client.post(f"/api/ministers/{minister['id']}/chat").json()
    chat = create_node(client, "conversation", name="Agent chat area", position={"x": 180, "y": 150}, size={"width": 96, "height": 96})
    def inspect_pair():
        return invoke(client, minister, "inspect", source_id=minister["id"], target_id=chat["id"])
    view = inspect_pair()
    assert view["principal"]["id"] == minister["id"]
    assert private["conversation_id"] not in str(view)
    assert "writable_config_fields" not in str(view) and "edits_allowed" not in str(view)
    assert "only have permission to inspect" in view["permission"]
    assert view["connection_options"][0]["permitted"] is False
    assert {"read", "participate", "execute_manage", "read_edit"} <= {item["id"] for item in view["relationship_types"]}
    assert next(node for node in view["nodes"] if node["id"] == chat["id"])["chat_readiness"]["routing_ready"] is False
    assert client.patch(f"/api/nodes/{minister['id']}", json={"config": {"allow_canvas_edits": True}}).status_code == 200
    view = inspect_pair()
    assert view["connection_options"][0]["permitted"]
    edge = invoke(client, minister, "connect", source=minister["id"], target=chat["id"], relationship="participate", versions=view["versions"])
    after = inspect_pair()
    ready = next(node for node in after["nodes"] if node["id"] == chat["id"])["chat_readiness"]
    assert ready["routing_ready"] and not ready["reply_observed"]
    assert ready["participants"][0]["id"] == minister["id"]
    assert after["connection_options"][0]["existing_edge_id"] == edge["id"]
    # A live edge alone does not mean a human-kicked participant can respond.
    client.app.state.services.conversations.remove_participant(chat["id"], ready["session_id"], minister["id"])
    assert not next(node for node in inspect_pair()["nodes"] if node["id"] == chat["id"])["chat_readiness"]["routing_ready"]
    invoke(client, minister, "disconnect", edge_id=edge["id"], versions=after["versions"])


def test_preflight_and_commit_reject_external_consequences_and_live_revocation(client):
    minister = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
    chat = create_node(client, "conversation", size={"width": 96, "height": 96})
    outside = create_node(client, "text", position={"x": 5000, "y": 0})
    assert client.post("/api/edges", json={"source": chat["id"], "target": outside["id"], "relationship": "conversation_notes"}).status_code == 201
    view = invoke(client, minister, "inspect", source_id=minister["id"], target_id=chat["id"])
    assert view["connection_options"][0]["permitted"] is False
    with pytest.raises(PermissionDeniedError):
        invoke(client, minister, "connect", source=minister["id"], target=chat["id"], relationship="participate", versions=view["versions"])
    clean = create_node(client, "conversation", size={"width": 96, "height": 96})
    view = invoke(client, minister, "inspect", source_id=minister["id"], target_id=clean["id"])
    assert view["connection_options"][0]["permitted"]
    client.patch(f"/api/nodes/{minister['id']}", json={"config": {"allow_canvas_edits": False}})
    with pytest.raises(PermissionDeniedError):
        invoke(client, minister, "connect", source=minister["id"], target=clean["id"], relationship="participate", versions=view["versions"])


def test_existing_ministers_receive_current_goal_harness_without_overwriting_preferences(client):
    minister = create_node(client, MINISTER_TYPE, config={"system_instruction": LEGACY_INSTRUCTION})
    services = client.app.state.services
    card = services.world.get_card(minister["id"])
    assert services.run_manager._agent_config(card).system_instruction == INSTRUCTION
    assert card.config["system_instruction"] == LEGACY_INSTRUCTION
    client.patch(f"/api/nodes/{card.id}", json={"config": {"system_instruction": "Use Chinese."}})
    assert services.run_manager._agent_config(services.world.get_card(card.id)).system_instruction.endswith("Host preferences:\nUse Chinese.")


def test_minister_cannot_acquire_other_tools_or_reconfigure_other_ministers(client):
    minister = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
    other = create_node(client, MINISTER_TYPE)
    sandbox = create_node(client, "sandbox")
    client.post("/api/edges", json={"source": minister["id"], "target": sandbox["id"], "relationship": "execute"})
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = call(client, provider.list_tools, minister["id"])
    assert {tool.name for tool in tools} == {"canvas_inspect", "canvas_create", "canvas_move", "canvas_rename", "canvas_connect", "canvas_disconnect", "canvas_update", "canvas_delete", "canvas_organize"}
    assert next(p for t in tools if t.name == "canvas_move" for p in t.parameters if p.name == "position").python_type is dict
    assert next(p for t in tools if t.name == "canvas_move" for p in t.parameters if p.name == "versions").python_type is dict
    with pytest.raises(PermissionDeniedError):
        invoke(client, minister, "move", node_id=other["id"], position={"x": 10, "y": 10}, versions=invoke(client, minister, "inspect")["versions"])
    callable_tools = {tool.__name__: tool for tool in build_scoped_tool_callables(provider, minister["id"], tools)}
    result = call(client, callable_tools["canvas_create"], minister=minister["id"], type="text", name="Bad", position={}, versions={}, config={"api_key": "secret-input"})
    assert result["ok"] is False and "secret-input" not in str(result)
    with pytest.raises(PermissionDeniedError):
        call(client, provider.invoke_tool, minister["id"], "operation:canvas_inspect", {"minister": other["id"]})


def test_radius_and_revocation_are_live_without_reconfiguring_running_provider(client, monkeypatch):
    minister = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
    note = create_node(client, "text", position={"x": 280, "y": 0}, size={"width": 96, "height": 96})
    facade = control(client.app.state.services, minister["id"], editing=True)
    observed = invoke(client, minister, "inspect")["versions"]
    async def fail_update(*args):
        raise AssertionError("A live grant change must not reconfigure the running provider")
    monkeypatch.setattr(type(client.app.state.services.run_manager), "update_agent", fail_update)
    assert client.patch(f"/api/nodes/{minister['id']}", json={"config": {"control_radius": 200}}).status_code == 200
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, note["id"], {"x": 60, "y": 60}, observed)
    assert client.patch(f"/api/nodes/{minister['id']}", json={"position": {"x": 280, "y": 0}}).status_code == 200
    assert note["id"] in {n["id"] for n in invoke(client, minister, "inspect")["nodes"]}
    assert client.patch(f"/api/nodes/{minister['id']}", json={"config": {"allow_canvas_edits": False}}).status_code == 200
    with pytest.raises(PermissionDeniedError):
        call(client, facade.move_card, note["id"], {"x": 290, "y": 20}, observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("human_edit", [False, True])
async def test_two_real_runs_share_versions_and_report_conflicts(tmp_path, human_edit):
    services = runtime_services(tmp_path)
    try:
        first = await services.create_card(CardCreate(type=MINISTER_TYPE, name="North", config={"allow_canvas_edits": True}))
        second = await services.create_card(CardCreate(type=MINISTER_TYPE, name="South", config={"allow_canvas_edits": True}))
        note = await services.create_card(CardCreate(type="text", position={"x": 100, "y": 100}, size={"width": 96, "height": 96}))
        runtime = services.run_manager.default_provider()
        runs = await asyncio.gather(*(services.run_manager.start_run(node.id, f"race:{note.id}") for node in (first, second)))
        await asyncio.wait_for(runtime.both_inspected.wait(), timeout=5)
        if human_edit:
            await services.update_card(note.id, CardPatch(name="Human wins"))
        runtime.resume.set()
        await asyncio.gather(*(services.run_manager.wait_execution(run.run_id) for run in runs))
        conflicts = [result for result in runtime.results if isinstance(result, dict) and result.get("ok") is False]
        assert len(conflicts) == (2 if human_edit else 1)
        assert all(result["error"]["code"] == "revision_conflict" for result in conflicts)
        if human_edit:
            assert services.world.get_card(note.id).name == "Human wins"
        assert all(services.run_manager.get_run(run.run_id).status == "succeeded" for run in runs)
    finally:
        await services.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    CardPatch(config={"allow_canvas_edits": False}),
    CardPatch(config={"control_radius": 200}),
    CardPatch(position={"x": 1000, "y": 0}),
])
async def test_host_can_revoke_one_running_minister_without_interrupting_another(tmp_path, patch):
    services = runtime_services(tmp_path)
    try:
        ministers = [await services.create_card(CardCreate(type=MINISTER_TYPE, name=name,
                     config={"allow_canvas_edits": True})) for name in ("North", "South")]
        note = await services.create_card(CardCreate(type="text", position={"x": 100, "y": 100}, size={"width": 96, "height": 96}))
        runtime = services.run_manager.default_provider()
        runs = await asyncio.gather(*(services.run_manager.start_run(node.id, f"race:{note.id}") for node in ministers))
        await asyncio.wait_for(runtime.both_inspected.wait(), timeout=5)
        await services.update_card(ministers[0].id, patch)
        runtime.resume.set()
        await asyncio.gather(*(services.run_manager.wait_execution(run.run_id) for run in runs))
        errors = [result["error"]["code"] for result in runtime.results if isinstance(result, dict) and result.get("ok") is False]
        assert errors == ["permission_denied"]
        assert services.world.get_card(note.id).name == "South edited"
        assert all(services.run_manager.get_run(run.run_id).status == "succeeded" for run in runs)
    finally:
        await services.shutdown()
        services.close()


def test_attached_chat_uses_durable_conversation_and_equipment_cleanup(tmp_path):
    services = runtime_services(tmp_path)
    with TestClient(create_app(services.settings, services=services)) as client:
        minister = create_node(client, MINISTER_TYPE)
        chat = client.post(f"/api/ministers/{minister['id']}/chat").json()
        assert chat == client.post(f"/api/ministers/{minister['id']}/chat").json()
        conversation, session = chat["conversation_id"], chat["session_id"]
        response = client.post(f"/api/conversations/{conversation}/sessions/{session}/messages",
                               json={"content": "Inspect", "mention_agent_ids": [minister["id"]]})
        assert response.status_code == 202, response.text
        runs = services.run_manager.list_runs(agent_id=minister["id"])
        for run in runs:
            call(client, services.run_manager.wait_execution, run.run_id)
        # Completion persistence is a separate, existing run tail.
        async def messages():
            for _ in range(100):
                items = services.conversations.list_messages(conversation, session)
                if any(item.sender_kind == "agent" for item in items):
                    return items
                await asyncio.sleep(.01)
            raise AssertionError("The Minister reply was not persisted")
        assert any("Inside my circle" in item.content for item in client.portal.call(messages))
    services.close()
    restarted = runtime_services(tmp_path)
    with TestClient(create_app(restarted.settings, services=restarted)) as client:
        assert client.post(f"/api/ministers/{minister['id']}/chat").json() == chat
        assert len(restarted.conversations.list_messages(conversation, session)) >= 2
        assert client.delete(f"/api/nodes/{minister['id']}").status_code == 200
        assert client.get(f"/api/nodes/{conversation}").status_code == 404
    restarted.close()


def send_and_read_reply(client, conversation_id, session_id, participant_id, content):
    services = client.app.state.services
    before = {message.id for message in services.conversations.list_messages(conversation_id, session_id)}
    response = client.post(f"/api/conversations/{conversation_id}/sessions/{session_id}/messages",
                          json={"content": content, "mention_agent_ids": [participant_id]})
    assert response.status_code == 202, response.text
    async def read_reply():
        for _ in range(300):
            messages = services.conversations.list_messages(conversation_id, session_id)
            final = [message for message in messages if message.id not in before and message.sender_kind == "agent" and message.is_final]
            if final:
                return final[-1].content
            await asyncio.sleep(.01)
        raise AssertionError("No durable agent reply arrived")
    return client.portal.call(read_reply)


@pytest.mark.parametrize("use_existing_agent", [False, True])
def test_failed_chat_scenario_now_builds_a_routable_area_and_receives_a_reply(tmp_path, use_existing_agent):
    services = runtime_services(tmp_path)
    try:
        with TestClient(create_app(services.settings, services=services)) as client:
            minister = create_node(client, MINISTER_TYPE, name="Minister", position={"x": 39, "y": 3300},
                                   config={"system_instruction": LEGACY_INSTRUCTION, "allow_canvas_edits": False})
            participant = create_node(client, "agent", name="Chat helper", position={"x": 200, "y": 3300}, size={"width": 96, "height": 96}) if use_existing_agent else None
            private = client.post(f"/api/ministers/{minister['id']}/chat").json()
            reply = send_and_read_reply(client, private["conversation_id"], private["session_id"], minister["id"], "帮我配置个聊天环境？")
            assert "只能查看" in reply and "已配置" not in reply
            assert len(services.world.list_cards()) == (3 if use_existing_agent else 2)
            client.patch(f"/api/nodes/{minister['id']}", json={"config": {"allow_canvas_edits": True}})
            reply = send_and_read_reply(client, private["conversation_id"], private["session_id"], minister["id"], "现在试试")
            participant = participant or next(node.model_dump() for node in services.world.list_cards() if node.type == "agent")
            assert "已配置" in reply and "尚未验证" in reply and participant["name"] in reply
            assert not any(word in reply for word in ("edits_allowed", "writable_config_fields", "canvas_connect"))
            assert all(node.type != "text" for node in services.world.list_cards())
            area = next(node for node in services.world.list_cards() if node.type == "conversation" and not node.equipment)
            assert area.name == "Agent 对话区" and area.id != private["conversation_id"]
            assert area.position.x + area.size.width < minister["position"]["x"]  # Spaced apart, inside the circle.
            view = invoke(client, minister, "inspect", query=area.id)
            readiness = view["nodes"][0]["chat_readiness"]
            assert readiness["routing_ready"] and not readiness["reply_observed"]
            assert readiness["participants"][0]["id"] == participant["id"]
            assert "你好" in send_and_read_reply(client, area.id, readiness["session_id"], participant["id"], "你好")
            assert invoke(client, minister, "inspect", query=area.id)["nodes"][0]["chat_readiness"]["reply_observed"]
            recorded = services.run_manager.default_provider().setup_prompts
            assert recorded[1][0] == INSTRUCTION
            assert "帮我配置个聊天环境？" in recorded[1][1] and recorded[1][1].endswith("现在试试")
    finally:
        services.close()


def test_chat_setup_reports_partial_completion_when_an_actor_moves_the_participant(tmp_path):
    services = runtime_services(tmp_path)
    try:
        with TestClient(create_app(services.settings, services=services)) as client:
            minister = create_node(client, MINISTER_TYPE, config={"allow_canvas_edits": True})
            participant = create_node(client, "agent", position={"x": 100, "y": 0}, size={"width": 96, "height": 96})
            async def move_participant():
                await services.update_card(participant["id"], CardPatch(position={"x": 5000, "y": 0}))
            services.run_manager.default_provider().after_chat_created = move_participant
            private = client.post(f"/api/ministers/{minister['id']}/chat").json()
            reply = send_and_read_reply(client, private["conversation_id"], private["session_id"], minister["id"], "我需要你去配置一个我跟agent聊天的地盘")
            assert "尚未完成" in reply and "已配置" not in reply
            area = next(node for node in services.world.list_cards() if node.type == "conversation" and not node.equipment)
            assert not services.world.connections_to(area.id)
            assert not invoke(client, minister, "inspect", query=area.id)["nodes"][0]["chat_readiness"]["routing_ready"]
    finally:
        services.close()


def test_chat_setup_repairs_the_reported_leftover_area_without_duplicate_conversations(tmp_path):
    services = runtime_services(tmp_path)
    try:
        with TestClient(create_app(services.settings, services=services)) as client:
            minister = create_node(client, MINISTER_TYPE, position={"x": 39, "y": 3300}, config={"allow_canvas_edits": True})
            create_node(client, "text", name="聊天说明", position={"x": 290, "y": 3332}, size={"width": 96, "height": 96})
            area = create_node(client, "conversation", name="Agent 对话区", position={"x": 180, "y": 3450}, size={"width": 96, "height": 96})
            private = client.post(f"/api/ministers/{minister['id']}/chat").json()
            before = {node.id for node in services.world.list_cards()}
            reply = send_and_read_reply(client, private["conversation_id"], private["session_id"], minister["id"], "我需要你去配置一个我跟agent聊天的地盘")
            participant = next(node.model_dump() for node in services.world.list_cards() if node.type == "agent")
            assert "已配置" in reply and "尚未验证" in reply
            assert {node.id for node in services.world.list_cards()} == before | {participant['id']}
            ready = invoke(client, minister, "inspect", query=area["id"])["nodes"][0]["chat_readiness"]
            assert ready["routing_ready"]
            assert "你好" in send_and_read_reply(client, area["id"], ready["session_id"], participant["id"], "你好")
    finally:
        services.close()
