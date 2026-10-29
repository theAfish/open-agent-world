import json
import pytest

from backend.agents.media import ToolImage, VisualToolResult
from backend.tests.test_visual_tools import PNG
from test_codex import Capabilities, runtime, config, collect


class ImageCapabilities(Capabilities):
    async def invoke_tool(self, agent_id, capability_id, arguments):
        return VisualToolResult({'filename': 'capture.png'}, (ToolImage(PNG, 'image/png'),))


@pytest.mark.asyncio
async def test_codex_subprocess_receives_image_and_activity_does_not(tmp_path):
    provider = runtime(tmp_path, ImageCapabilities())
    cfg = config(tmp_path)
    await provider.create_agent(cfg)
    events = await collect(provider, cfg, 'tool')
    messages = [json.loads(line) for line in (tmp_path / 'protocol.jsonl').read_text().splitlines()]
    response = next(message['result'] for message in messages if message.get('id') == 'tool-2')
    assert response['contentItems'][1]['type'] == 'inputImage'
    assert response['contentItems'][1]['imageUrl'].startswith('data:image/png;base64,')
    assert 'base64' not in str(events)
