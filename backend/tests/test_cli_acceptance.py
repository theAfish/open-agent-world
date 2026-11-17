"""First-use commands cross a real process, HTTP, and persistent world boundary."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from backend.config import Settings
from backend.main import create_app


@pytest.fixture
def cli_server(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / "profile"), agent_runtime="core.mock",
                       sandbox_runtime=None, marketplace_url=None)
    application = create_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(application, log_config=None, proxy_headers=False,
                                           timeout_graceful_shutdown=10))
    failures = []

    def serve():
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"}

    def cli(*arguments, stdin=None, code=0):
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "open_agent_world.cli", "--endpoint", url, *arguments, "--json"],
            cwd=root, env=environment, input=stdin, text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        assert completed.returncode == code, completed.stdout + completed.stderr
        assert completed.stderr == ""
        assert "\x1b" not in completed.stdout
        return json.loads(completed.stdout)

    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if failures:
                raise failures[0]
            assert worker.is_alive() and time.monotonic() < deadline, "OAW did not start"
            time.sleep(.05)
        with httpx.Client(base_url=url + "/api", trust_env=False, timeout=15) as api:
            yield cli, api
    finally:
        server.should_exit = True
        worker.join(timeout=15)
        if worker.is_alive():
            server.force_exit = True
            worker.join(timeout=5)
        listener.close()
        assert not worker.is_alive(), "OAW did not stop"
        if failures:
            raise failures[0]


def test_first_use_creates_connects_edits_and_runs_cards(cli_server, tmp_path):
    cli, api = cli_server
    catalog = cli("types")
    assert {"text", "agent"} <= {item["id"] for item in catalog["node_types"]}

    body = tmp_path / "笔记.txt"
    body.write_text("第一版内容\n", encoding="utf-8")
    text = cli("create", "text", "--name", "项目笔记", "--text-file", str(body))
    shown = cli("show", "项目笔记")
    assert shown["card"]["id"] == text["id"]
    assert shown["text"]["content"] == "第一版内容\n"
    initial_revision = shown["text"]["revision"]
    revised = cli("edit", "项目笔记", "--text-file", "-", "--revision", str(initial_revision), stdin="第二版内容\n")
    assert revised["revision"] > initial_revision
    conflict = cli("edit", text["id"], "--text", "过期修改", "--revision", str(initial_revision), code=5)
    assert conflict["error"]["code"] == "revision_conflict"

    agent = cli("create", "agent", "--input", "-", stdin=json.dumps({"name": "研究助手"}, ensure_ascii=False))
    edge = cli("connect", "研究助手", "项目笔记", "--relationship", "read")
    assert edge["source"] == agent["id"] and edge["target"] == text["id"]
    result = cli("run", "研究助手", "请检查项目笔记", "--wait", "--timeout", "10")
    assert result["status"] == "succeeded"
    assert result["text"] == "Mock response: 请检查项目笔记"
    assert cli("result", result["run_id"]) == result

    assert api.get(f"/resources/{text['id']}/text").json()["content"] == "第二版内容\n"
    assert {text["id"], agent["id"]} <= {item["id"] for item in api.get("/nodes").json()}
    assert edge["id"] in {item["id"] for item in api.get("/edges").json()}


def test_noninteractive_removal_and_duplicate_names_return_json_errors(cli_server):
    cli, api = cli_server
    first = cli("create", "text", "--name", "同名笔记")
    second = cli("create", "text", "--name", "同名笔记")
    ambiguous = cli("show", "同名笔记", code=2)
    assert ambiguous["error"]["code"] == "ambiguous_selector"
    assert {item["id"] for item in ambiguous["error"]["candidates"]} == {first["id"], second["id"]}
    refused = cli("remove", first["id"], code=2)
    assert refused["error"]["code"] == "confirmation_required"
    assert api.get(f"/nodes/{first['id']}").status_code == 200


def test_first_use_names_and_conversation_commands(cli_server):
    cli, api = cli_server
    original = api.post("/nodes", json={"type": "agent"}).json()
    assert original["name"] == "New Agent"
    assert cli("show", "agent")["card"]["id"] == original["id"]

    notes = cli("create", "text")
    assert notes["name"] == "text"
    assert cli("create", "text")["name"] == "text-2"
    room = cli("create", "conversation", "room")
    assert room["name"] == "room"
    joined = cli("connect", "room", "agent")
    assert joined["source_name"] == "New Agent"
    assert joined["relationship"] == "participate"

    linked = cli("connect", "agent", notes["id"], "read")
    assert linked["relationship"] == "read"
    choices = cli("connect", "agent", notes["id"], code=2)
    assert choices["error"]["code"] == "relationship_required"
    assert {item["id"] for item in choices["error"]["candidates"]} == {"read", "read_edit"}

    first = cli("run", "room", "First shared message")
    # Conversation prompts include session context; compare its authoritative result.
    assert first["replies"][0]["status"] == "succeeded"
    assert first["text"] == cli("result", first["replies"][0]["run_id"])["text"]
    assert "First shared message" in first["text"]
    second = cli("chat", "room", "Second shared message")
    assert second["session_id"] == first["session_id"]
    history = cli("chat", "room", "--history")
    assert [message["content"] for message in history["messages"] if message["sender_kind"] == "user"] == [
        "First shared message", "Second shared message"]
    rejected = cli("run", notes["id"], "hello", code=2)
    assert rejected["error"]["code"] == "invalid_card_type"
    basic = cli("types", "basic")
    assert {item["id"] for item in basic["node_types"]} == {"agent", "conversation", "text", "image", "sandbox"}
