"""Host bridge for Draft with AI: one model call, nothing saved, readable failures."""
import pytest
from fastapi import HTTPException

from backend.api import knowledge_bridge as bridge


@pytest.fixture
def transport(monkeypatch):
    async def check(*args):
        pass
    monkeypatch.setattr(bridge, "_check_node", check)
    monkeypatch.setattr(bridge, "_credentials", lambda *args: ("https://model.invalid", "secret", "model"))


def request(**extra):
    return bridge.SchemaDraftRequest(model="model", messages=[{"role": "user", "content": "draft one"}], **extra)


@pytest.mark.asyncio
async def test_a_turn_sends_samples_and_current_schema_and_returns_the_parsed_draft(monkeypatch, transport):
    seen = {}
    async def complete(base, key, model, messages, parse):
        seen["messages"] = messages
        return parse('{"reply": "Here.", "draft": {"name": "N", "system_prompt": "P", '
                     '"definition": {"properties": {"a": {"value": "number"}}}}}')
    async def forbidden(*args, **kwargs):
        raise AssertionError("a draft turn must not call a card action")
    monkeypatch.setattr(bridge, "_complete", complete)
    monkeypatch.setattr(bridge, "invoke_resource_action", forbidden)
    result = await bridge.run_schema_draft("kb", request(
        samples=[{"filename": "a.csv", "markdown": "| a |"}], current={"name": "Old"}), None)
    assert result["draft"]["definition"] == {"properties": {"a": {"value": "number"}}}
    assert '<sample_file name="a.csv">' in seen["messages"][0]["content"] and seen["messages"][-1]["content"] == "draft one"


@pytest.mark.asyncio
async def test_an_unusable_model_draft_is_reported_with_its_reason(monkeypatch, transport):
    async def complete(base, key, model, messages, parse):
        return parse('{"reply": "x", "draft": {"name": "N", "system_prompt": "P", "definition": {"properties": {"a": {"value": "date"}}}}}')
    monkeypatch.setattr(bridge, "_complete", complete)
    with pytest.raises(HTTPException) as error:
        await bridge.run_schema_draft("kb", request(), None)
    assert error.value.status_code == 502 and "Property a needs a value type" in error.value.detail


@pytest.mark.asyncio
async def test_a_conversation_that_does_not_end_with_the_user_is_refused(transport):
    with pytest.raises(HTTPException) as error:
        await bridge.run_schema_draft("kb", bridge.SchemaDraftRequest(
            model="model", messages=[{"role": "assistant", "content": "hi"}]), None)
    assert error.value.status_code == 422
