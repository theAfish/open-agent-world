from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from backend.agents import GoogleAdkAgentRuntime
from backend.errors import ResourceValidationError
from backend.persistence.database import Database
from backend.security.llm_settings import LlmSettingsStore
from backend.security.model_connections import CatalogEdit, ModelConnectionStore


def connection(cid="work", **changes):
    return dict(id=cid, name=cid, adapter="openai", base_url=f"https://{cid}.example/v1",
                api_key=f"secret-{cid}", models=[dict(id=cid, name="Assistant", model_id="same-model")], **changes)


def test_catalog_api_encrypts_keeps_and_clears_keys(client):
    initial = client.get("/api/settings/models").json()
    assert initial == dict(revision=0, connections=[], default_model=None)
    payload = {**initial, "connections": [connection(), connection("personal")], "default_model": "oaw:model:work"}
    response = client.put("/api/settings/models", json=payload)
    assert response.status_code == 200, response.text
    assert "secret-" not in response.text
    public = response.json()
    assert all(c["api_key_configured"] for c in public["connections"])
    kept = client.put("/api/settings/models", json=public).json()
    assert all(c["api_key_configured"] for c in kept["connections"])
    kept["connections"][0]["clear_api_key"] = True
    cleared = client.put("/api/settings/models", json=kept).json()
    assert not cleared["connections"][0]["api_key_configured"]
    assert cleared["connections"][1]["api_key_configured"]


def test_catalog_rejects_stale_edits_and_preserves_references(client):
    payload = dict(revision=0, connections=[connection()], default_model="oaw:model:work")
    saved = client.put("/api/settings/models", json=payload).json()
    assert client.put("/api/settings/models", json=payload).status_code == 409
    assert client.put("/api/settings/models", json={**saved, "connections": [], "default_model": None}).status_code == 422
    saved["connections"][0]["enabled"] = False
    assert client.put("/api/settings/models", json=saved).status_code == 422
    saved["default_model"] = None
    assert client.put("/api/settings/models", json=saved).status_code == 200


def test_validation_never_echoes_secret_input(client):
    payload = dict(revision=0, connections=[connection()], default_model="missing")
    response = client.put("/api/settings/models", json=payload)
    assert response.status_code == 422
    assert "secret-work" not in response.text
    payload["connections"][0]["base_url"] = "https://user:secret-work@example.com"
    response = client.put("/api/settings/models", json=payload)
    assert response.status_code == 422
    assert "secret-work" not in response.text


def test_connections_survive_restart_and_are_isolated(tmp_path):
    database_path = tmp_path / "state.db"
    db = Database(database_path)
    secrets = LlmSettingsStore(db, tmp_path)
    store = ModelConnectionStore(secrets)
    store.save(CatalogEdit(connections=[connection(), connection("personal")]))
    db.close()
    assert b"secret-work" not in database_path.read_bytes()
    assert b"secret-personal" not in database_path.read_bytes()
    db = Database(database_path)
    try:
        store = ModelConnectionStore(LlmSettingsStore(db, tmp_path))
        runtime = GoogleAdkAgentRuntime(None, model_connections=store)
        with ThreadPoolExecutor(max_workers=2) as executor:
            models = list(executor.map(runtime._adk_model, ["oaw:model:work", "oaw:model:personal"]))
        assert models[0].model == models[1].model == "openai/same-model"
        assert models[0]._additional_args == dict(api_base="https://work.example/v1", api_key="secret-work")
        assert models[1]._additional_args == dict(api_base="https://personal.example/v1", api_key="secret-personal")
        update = store.read().model_dump()
        update["connections"][0]["api_key"] = "replacement"
        store.save(CatalogEdit.model_validate(update))
        assert models[0]._additional_args["api_key"] == "secret-work"
        assert runtime._adk_model("oaw:model:work")._additional_args["api_key"] == "replacement"
        assert runtime._adk_model("oaw:model:personal")._additional_args["api_key"] == "secret-personal"
    finally:
        db.close()


def test_legacy_migration_preserves_native_routing_and_clears_old_key(tmp_path):
    db = Database(tmp_path / "state.db")
    try:
        secrets = LlmSettingsStore(db, tmp_path)
        secrets.save(base_url="https://legacy.example/v1", api_key="legacy-secret")
        store = ModelConnectionStore(secrets)
        draft = store.read().model_dump()
        assert draft["connections"][0]["api_key_configured"]
        assert draft["connections"][0]["auth_mode"] == "api_key"
        draft["connections"][0]["models"] = [dict(id="native", name="Native", model_id="gemini-3.7-flash")]
        store.save(CatalogEdit.model_validate(draft))
        runtime = GoogleAdkAgentRuntime(None, model_connections=store)
        assert runtime._adk_model("oaw:model:native") == "gemini-3.7-flash"
        assert runtime._adk_model("openai/old-model")._additional_args["api_key"] == "legacy-secret"
        draft = store.read().model_dump()
        draft["connections"][0]["clear_api_key"] = True
        store.save(CatalogEdit.model_validate(draft))
        assert secrets.read().api_key is None
        assert store.read().connections[0].auth_mode == "api_key"
        with pytest.raises(ResourceValidationError, match="needs an API key"):
            runtime._adk_model("openai/old-model")
    finally:
        db.close()


def test_legacy_connection_without_a_key_defaults_to_key_entry(tmp_path):
    db = Database(tmp_path / "state.db")
    try:
        secrets = LlmSettingsStore(db, tmp_path)
        secrets.save(base_url="https://legacy.example/v1")
        connection = ModelConnectionStore(secrets).read().connections[0]
        assert connection.auth_mode == "api_key"
        assert not connection.api_key_configured
    finally:
        db.close()


def test_missing_disabled_and_unbound_models_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-environment-key")
    db = Database(tmp_path / "state.db")
    try:
        store = ModelConnectionStore(LlmSettingsStore(db, tmp_path))
        item = connection()
        item.pop("api_key")
        store.save(CatalogEdit(connections=[item]))
        with pytest.raises(ResourceValidationError, match="needs an API key"):
            store.resolve("oaw:model:work")
        with pytest.raises(ResourceValidationError, match="missing"):
            store.resolve("oaw:model:missing")
        draft = store.read().model_dump()
        draft["connections"][0]["enabled"] = False
        store.save(CatalogEdit.model_validate(draft))
        with pytest.raises(ResourceValidationError, match="disabled"):
            store.resolve("oaw:model:work")
    finally:
        db.close()


def test_environment_authentication_uses_the_connection_variable_not_a_saved_key(tmp_path, monkeypatch):
    db = Database(tmp_path / "state.db")
    try:
        store = ModelConnectionStore(LlmSettingsStore(db, tmp_path))
        store.save(CatalogEdit(connections=[connection()]))
        draft = store.read().model_dump()
        draft["connections"][0]["auth_mode"] = "environment"
        draft["connections"][0]["environment_variable"] = "WORK_CONNECTION_KEY"
        store.save(CatalogEdit.model_validate(draft))
        monkeypatch.setenv("WORK_CONNECTION_KEY", "server-only-secret")
        adapter, model_id, base_url, api_key = store.resolve("oaw:model:work")
        assert (adapter, model_id, base_url) == ("openai", "same-model", "https://work.example/v1")
        assert api_key == "server-only-secret"
    finally:
        db.close()


@pytest.mark.parametrize("auth_mode", ["none", "environment"])
@pytest.mark.parametrize("legacy", [False, True])
def test_entered_key_activates_without_a_separate_mode_change(tmp_path, auth_mode, legacy):
    db = Database(tmp_path / "state.db")
    try:
        store = ModelConnectionStore(LlmSettingsStore(db, tmp_path))
        item = connection("legacy" if legacy else "work", auth_mode=auth_mode)
        item.pop("api_key")
        if legacy:
            item["adapter"] = "legacy"
        store.save(CatalogEdit(connections=[item]))
        edit = store.read().model_dump()
        edit["connections"][0]["api_key"] = "entered-directly"
        saved = store.save(CatalogEdit.model_validate(edit))
        assert saved.connections[0].auth_mode == "api_key"
        assert store.resolve("oaw:model:" + item["id"])[3] == "entered-directly"
        assert "entered-directly" not in saved.model_dump_json()
        if legacy:
            assert store.legacy_options()["api_key"] == "entered-directly"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_adk_sends_each_connection_key_to_its_own_endpoint(tmp_path):
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append((self.path, self.headers.get("Authorization"), body["model"]))
            response = json.dumps(dict(id="test", object="chat.completion", created=1, model="gpt-4o-mini",
                                       choices=[dict(index=0, message=dict(role="assistant", content="ok"), finish_reason="stop")],
                                       usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = Database(tmp_path / "state.db")
    try:
        store = ModelConnectionStore(LlmSettingsStore(db, tmp_path))
        connections = [connection(), connection("personal")]
        for item in connections:
            item["base_url"] = f"http://127.0.0.1:{server.server_port}/{item['id']}/v1"
            item["models"][0]["model_id"] = "gpt-4o-mini"
        store.save(CatalogEdit(connections=connections))
        runtime = GoogleAdkAgentRuntime(None, model_connections=store)

        async def call(cid):
            model = runtime._adk_model("oaw:model:" + cid)
            request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hello")])])
            return [response async for response in model.generate_content_async(request, stream=False)]

        responses = await asyncio.wait_for(asyncio.gather(call("work"), call("personal")), timeout=20)
        assert all(result[0].content.parts[0].text == "ok" for result in responses)
        assert sorted(captured) == [("/personal/v1/chat/completions", "Bearer secret-personal", "gpt-4o-mini"),
                                    ("/work/v1/chat/completions", "Bearer secret-work", "gpt-4o-mini")]
    finally:
        db.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
