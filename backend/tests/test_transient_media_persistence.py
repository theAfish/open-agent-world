"""Transient tool observations stay live, but never enter durable Run/context data."""
import base64
import json

import pytest
from google.adk.flows.llm_flows.functions import _extract_multimodal_parts
from google.genai import types

from backend.agents.context import Checkpoint, ContextStatus
from backend.agents.media import ToolImage, VisualToolResult, adk_tool_result
from backend.runs.invocation_logs import archive_value
from backend.tests.conftest import create_node
from backend.tests.test_invocation_logs import native, request
from backend.tests.test_visual_tools import PNG


def visual_response():
    visual = VisualToolResult({'revision': 7, 'source': 'capture'}, (ToolImage(PNG, 'image/png'),))
    wire = adk_tool_result(visual)
    metadata, images = _extract_multimodal_parts(wire)
    part = types.Part.from_function_response(name='observe', response=metadata, parts=images)
    part.function_response.id = 'capture-1'
    return visual, wire, types.Content(role='user', parts=[part])


@pytest.mark.parametrize('form', ['raw', 'wire', 'request'])
def test_archive_omits_transient_image_bytes_with_explicit_marker(form):
    visual, wire, content = visual_response()
    value = {'raw': visual, 'wire': wire, 'request': {'contents': [content]}}[form]
    safe, missing = archive_value(value)
    assert base64.b64encode(PNG).decode() not in json.dumps(safe)
    assert missing and 'revision' in json.dumps(safe)
    assert content.parts[0].function_response.parts[0].inline_data.data == PNG


def test_user_images_and_signed_thoughts_are_not_classified_as_tool_observations():
    user = types.Part.from_bytes(data=PNG, mime_type='image/png')
    thought = types.Part(text='Signed thought', thought=True, thought_signature=b'opaque-signature')
    safe, missing = archive_value({'contents': [types.Content(role='user', parts=[user]),
                                              types.Content(role='model', parts=[thought])]})
    assert not missing
    assert base64.b64encode(PNG).decode() in json.dumps(safe)
    assert 'Signed thought' in json.dumps(safe)
    assert base64.b64encode(b'opaque-signature').decode() in json.dumps(safe)


@pytest.mark.parametrize('signed', [False, True, 'mixed'])
def test_context_persists_observation_metadata_without_mutating_live_history(client, signed):
    agent = create_node(client, "agent")["id"]
    _, _, content = visual_response()
    call = types.Part.from_function_call(name='observe', args={})
    call.function_call.id = 'capture-1'
    parts = [call]
    if signed == 'mixed':
        call.thought = True
        call.text = 'Private reasoning attached to a public call'
        call.thought_signature = b'opaque-signature'
    elif signed:
        parts.insert(0, types.Part(thought=True, text='Signed thought', thought_signature=b'opaque-signature'))
    contents = [types.Content(role='model', parts=parts).model_dump(mode='json', exclude_none=True),
                content.model_dump(mode='json', exclude_none=True)]
    checkpoint = Checkpoint(contents=contents, measured_tokens=1200, measured_estimate=1000)
    before = json.dumps(checkpoint.contents)
    store = client.app.state.services.contexts
    store.save(agent, 'context', checkpoint, ContextStatus())
    saved = store.load(agent, 'context')
    assert base64.b64encode(PNG).decode() not in json.dumps(saved.contents)
    assert json.dumps(checkpoint.contents) == before and checkpoint.measured_tokens == 1200
    assert saved.measured_tokens is None
    assert 'revision' in json.dumps(saved.contents) and '7' in json.dumps(saved.contents)
    for item in saved.contents:
        types.Content.model_validate(item)
    if signed:
        assert 'thought_signature' not in json.dumps(saved.contents)
        assert 'historical data' in json.dumps(saved.contents)
        assert 'capture-1' in saved.contents[0]['parts'][0]['text']
        assert 'Private reasoning attached' not in saved.contents[0]['parts'][0]['text']
    else:
        assert saved.contents[0] == checkpoint.contents[0]
        assert saved.contents[1]['parts'][0]['function_response']['id'] == 'capture-1'


@pytest.mark.parametrize('reason', ['image', 'split_credential'])
def test_exact_recovery_checkpoint_is_unavailable_when_persistence_changes_request(native, reason):
    _, _, _, _, checkpoints = native
    req = request()
    if reason == 'image':
        req.contents.append(visual_response()[2])
    else:
        req.contents[0].parts = [types.Part(text='sk-sensitive-'), types.Part(text='key')]
    checkpoints.start('agent', 1, 0, req)
    saved = checkpoints.latest('agent')
    assert not saved['replayable'] and saved['contents_json'] is None
    identity = checkpoints.invocations.start_model('agent', 1, 0, 1, req)
    row = checkpoints.invocations.records('models')[identity]
    if reason == 'image':
        assert not row['request_complete'] and row['unavailable_request_paths']
    assert base64.b64encode(PNG).decode() not in json.dumps(row)
    assert 'sk-sensitive-' not in json.dumps(row)
