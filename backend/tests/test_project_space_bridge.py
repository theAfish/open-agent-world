"""Host model orchestration: direct Markdown, reuse, and recoverable failures."""
import asyncio

import pytest

from backend.api import knowledge_bridge as bridge


@pytest.fixture
def transport(monkeypatch):
    async def check(*args):
        pass
    monkeypatch.setattr(bridge, "_check_node", check)
    monkeypatch.setattr(bridge, "_credentials", lambda *args: ("https://model.invalid", "secret", "model"))
    monkeypatch.setattr(bridge, "_builders", lambda *args: (
        lambda prompt, markdown=None, review=False: [{"role": "user", "content": markdown}], lambda value: value))


@pytest.mark.asyncio
async def test_batch_only_executes_new_tasks_and_reads_all_markdown_windows(monkeypatch, transport):
    calls, messages = [], []
    async def invoke(services, node, action, request):
        args = request.arguments
        calls.append((action, args))
        if action == "projection_prepare":
            return {"projections": [
                {"id": "new", "project_id": "a", "project_name": "Report A", "disposition": "created"},
                {"id": "old", "project_id": "b", "disposition": "reused"},
                {"id": "busy", "project_id": "c", "disposition": "running"}]}
        if action == "projection_prompt":
            later = bool(args.get("offset"))
            return {"markdown": "Supplement" if later else "Report\n", "has_more": not later,
                    "expected_revision": 1}
        if action == "projection_save":
            assert args == {"projection_id": "new", "data": {"value": 42}, "expected_revision": 1}
            return {"projection": {"id": "new", "status": "completed"}}
        raise AssertionError(action)
    async def complete(base, key, model, prompt, parse):
        messages.extend(prompt)
        return {"value": 42}
    monkeypatch.setattr(bridge, "invoke_resource_action", invoke)
    monkeypatch.setattr(bridge, "_complete", complete)
    result = await bridge.run_project_space("kb", bridge.ProjectSpaceRequest(
        project_ids=["a", "b", "c"], space_id="space", model="model"), None)
    assert messages == [{"role": "user", "content": "Report\nSupplement"}]
    assert [item["id"] for item in result["projections"]] == ["new", "old", "busy"]
    assert [name for name, _ in calls] == ["projection_prepare", "projection_prompt", "projection_prompt", "projection_save"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["oversized", "cancelled"])
async def test_unread_or_cancelled_material_never_saves_a_success(monkeypatch, transport, failure):
    calls = []
    async def invoke(services, node, action, request):
        calls.append((action, request.arguments))
        if action == "projection_prepare":
            return {"projections": [{"id": "new", "project_id": "a", "disposition": "created"}]}
        if action == "projection_prompt":
            return {"markdown": "text", "has_more": failure == "oversized", "expected_revision": 1}
        if action == "projection_fail":
            return {"projection": {"id": "new", "status": "failed", "error": request.arguments["error"]}}
        raise AssertionError(action)
    async def complete(*args, **kwargs):
        assert failure == "cancelled"
        raise asyncio.CancelledError()
    monkeypatch.setattr(bridge, "invoke_resource_action", invoke)
    monkeypatch.setattr(bridge, "_complete", complete)
    monkeypatch.setattr(bridge, "EXTRACT_MAX_CHARS", 4)
    request = bridge.ProjectSpaceRequest(project_ids=["a"], space_id="space", model="model")
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await bridge.run_project_space("kb", request, None)
    else:
        result = await bridge.run_project_space("kb", request, None)
        assert result["projections"][0]["status"] == "failed"
        assert "all Markdown windows" in result["projections"][0]["error"]
    assert [name for name, _ in calls].count("projection_fail") == 1
    assert not any(name == "projection_save" for name, _ in calls)


@pytest.mark.asyncio
async def test_optional_postprocessing_uses_revision_guard_and_user_instruction(monkeypatch, transport):
    messages = []
    async def invoke(services, node, action, request):
        if action == "projection_review_prompt":
            return {"markdown": "raw measurements", "has_more": False, "expected_revision": 3}
        assert action == "projection_revise"
        assert request.arguments == {"projection_id": "original", "data": {"value": 10},
            "expected_revision": 3, "note": "Normalize units"}
        return {"projection": {"id": "new-version"}}
    async def complete(base, key, model, prompt, parse):
        messages.extend(prompt)
        return {"value": 10}
    monkeypatch.setattr(bridge, "invoke_resource_action", invoke)
    monkeypatch.setattr(bridge, "_complete", complete)
    result = await bridge.run_projection_review("kb", bridge.ProjectionReviewRequest(
        projection_id="original", model="model", instruction="Normalize units"), None)
    assert result["projection"]["id"] == "new-version"
    assert messages[-1] == {"role": "user", "content": "Normalize units"}
