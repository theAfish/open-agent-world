"""Conversation CLI reuses the GUI transcript and durable delivery ownership."""
from types import SimpleNamespace

import pytest

from backend.tests.test_cli_acceptance import cli_server
from open_agent_world.cli.chat import _wait_conversation, handle_chat
from open_agent_world.cli.client import Client, CliError


def arguments(card, message=None, **changes):
    return SimpleNamespace(**{"card": card, "message": message, "message_file": None, "session": None,
                              "agent": None, "new": False, "history": False, "timeout": 5,
                              "json": True, "plain": True, **changes})


def card(api, type, name):
    response = api.post("/nodes", json={"type": type, "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def connect(api, agent, conversation):
    response = api.post("/edges", json={"source": agent["id"], "target": conversation["id"], "relationship": "participate"})
    assert response.status_code == 201, response.text


def test_conversation_turn_is_saved_and_history_never_resends(cli_server, monkeypatch):
    _, api = cli_server
    agent = card(api, "agent", "Researcher")
    room = card(api, "conversation", "Discussion")
    connect(api, agent, room)
    client = Client({"url": str(api.base_url)})

    reply, kind, code = handle_chat(client, arguments("Discussion", "你好，请回复"))
    assert (kind, code) == ("chat", 0)
    assert reply["replies"][0]["agent_id"] == agent["id"]
    assert reply["replies"][0]["status"] == "succeeded"
    assert "你好，请回复" in reply["text"]
    path = f"/conversations/{room['id']}/sessions/{reply['session_id']}/messages"
    persisted = api.get(path).json()
    assert [item["sender_kind"] for item in persisted] == ["user", "agent"]
    assert persisted[0]["id"] == reply["message_id"]
    assert persisted[1]["run_id"] == reply["replies"][0]["run_id"]

    history, _, code = handle_chat(client, arguments("Discussion", history=True))
    assert code == 0
    assert history["messages"] == persisted
    assert api.get(path).json() == persisted

    inputs = iter(["再聊一句", "/exit"])
    post_timeouts = []
    request = client.request

    def record_timeout(method, path, *args, **kwargs):
        if method == "POST" and path.endswith("/messages"):
            post_timeouts.append(client.timeout)
        return request(method, path, *args, **kwargs)

    def next_message(_):
        # A previous wait can finish with a nearly exhausted per-request budget.
        client.timeout = .001
        return next(inputs)

    monkeypatch.setattr(client, "request", record_timeout)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", next_message)
    assert handle_chat(client, arguments("Discussion", json=False)) == (None, "chat", 0)
    assert post_timeouts == [30]
    transcript = api.get(path).json()
    assert [item["content"] for item in transcript if item["sender_kind"] == "user"] == ["你好，请回复", "再聊一句"]


def test_multiple_agents_require_a_choice_and_explicit_session_is_respected(cli_server):
    _, api = cli_server
    atlas = card(api, "agent", "Atlas")
    river = card(api, "agent", "River")
    room = card(api, "conversation", "Review")
    connect(api, atlas, room)
    connect(api, river, room)
    client = Client({"url": str(api.base_url)})
    with pytest.raises(CliError) as error:
        handle_chat(client, arguments("Review", "Please reply"))
    assert error.value.error["code"] == "agent_required"

    session = api.post(f"/conversations/{room['id']}/sessions", json={"title": "Focused", "participant_ids": [river["id"]]}).json()
    reply, _, code = handle_chat(client, arguments("Review", "Only River", session="Focused"))
    assert code == 0
    assert reply["session_id"] == session["id"]
    assert [item["agent_id"] for item in reply["replies"]] == [river["id"]]
    user = api.get(f"/conversations/{room['id']}/sessions/{session['id']}/messages").json()[0]
    assert user["mention_agent_ids"] == [river["id"]]


def test_unconnected_agent_and_wrong_card_fail_before_sending(cli_server):
    _, api = cli_server
    agent = card(api, "agent", "Researcher")
    room = card(api, "conversation", "Room")
    card(api, "text", "Notes")
    client = Client({"url": str(api.base_url)})
    with pytest.raises(CliError) as error:
        handle_chat(client, arguments("Room", "hello", agent="Researcher"))
    assert error.value.error["code"] == "agent_not_connected"
    session = api.get(f"/conversations/{room['id']}").json()["sessions"][0]
    assert api.get(f"/conversations/{room['id']}/sessions/{session['id']}/messages").json() == []
    with pytest.raises(CliError) as error:
        handle_chat(client, arguments("Notes", "hello"))
    assert error.value.error["code"] == "not_chat_card"


def test_agent_direct_chat_and_missing_message(cli_server):
    _, api = cli_server
    agent = card(api, "agent", "Researcher")
    client = Client({"url": str(api.base_url)})
    data, _, code = handle_chat(client, arguments("Researcher", "hello"))
    assert code == 0
    assert data["replies"][0]["agent_id"] == agent["id"]
    assert data["text"] == "Mock response: hello"
    with pytest.raises(CliError) as error:
        handle_chat(client, arguments("Researcher"))
    assert error.value.error["code"] == "message_required"


def test_wait_ignores_an_old_reply_and_a_queued_delivery(monkeypatch):
    message = {"id": "mine", "sequence": 10}
    pages = [
        {"deliveries": [{"message_id": "mine", "agent_id": "agent", "status": "queued", "claimed_run_id": None}],
         "active_runs": [], "run_summaries": {"old": {}}},
        {"deliveries": [{"message_id": "mine", "agent_id": "agent", "status": "claimed", "claimed_run_id": "new"}],
         "active_runs": [{"run_id": "new"}], "run_summaries": {"new": {}}},
        {"deliveries": [], "active_runs": [], "run_summaries": {"new": {}}},
    ]
    seen = []

    def request(method, path, **query):
        seen.append(path)
        if path.endswith("/timeline"):
            return pages.pop(0)
        if path.endswith("/result"):
            return {"run_id": "new", "agent_id": "agent", "status": "succeeded", "text": "Current reply", "error": None}
        old = path.endswith("/old")
        return {"run_id": "old" if old else "new", "agent_id": "agent", "caller_id": "room", "context_id": "session",
                "created_at": "2026-01-01" if old else "2026-01-02", "status": "succeeded" if old or not pages else "running",
                "lifecycle": {"delivery_message_ids": ["earlier" if old else "mine"]}}

    monkeypatch.setattr("open_agent_world.cli.chat.time.sleep", lambda _: None)
    data, code = _wait_conversation(SimpleNamespace(request=request, timeout=30), {"id": "room", "name": "Room"},
                                   {"id": "session"}, {"message": message}, {"id": "agent", "name": "Researcher"}, 5)
    assert code == 0 and data["text"] == "Current reply"
    assert sum(path.endswith("/timeline") for path in seen) == 3
    assert seen[-1] == "/runs/new/result"
