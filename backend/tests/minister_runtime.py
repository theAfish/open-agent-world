"""Deterministic tool-using provider for Minister integration tests; never a production fallback."""
import asyncio
from dataclasses import replace

from backend.agents import AgentEvent, AgentEventType, MockAgentRuntime
from backend.agents.tools import build_scoped_tool_callables
from backend.runs import RunStatus
from backend.config import Settings
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from backend.tests.plugin_support import install_test_plugin


class MinisterTestRuntime(MockAgentRuntime):
    def __init__(self, capabilities):
        super().__init__(capabilities)
        self.arrivals = 0
        self.both_inspected = asyncio.Event()
        self.resume = asyncio.Event()
        self.results = []
        self.auto_resume = False
        self.browser_barrier = asyncio.Barrier(2)
        self.setup_prompts = []
        self.after_chat_created = None

    async def execute(self, config, context, runtime_input):
        definitions = await self._provider.list_tools(context.agent_id)
        tools = {tool.__name__: tool for tool in build_scoped_tool_callables(self._provider, context.agent_id, definitions)}
        prompt = runtime_input.prompt.rsplit("to the latest message: ", 1)[-1]
        if prompt in {"帮我配置个聊天环境？", "现在试试", "我需要你去配置一个我跟agent聊天的地盘", "你好"}:
            # Scripted planning fixture: exercises real control/routing, not a
            # claim that a deterministic provider proves language-model planning.
            async for event in self.chat_setup(config, context, runtime_input, tools, prompt):
                yield event
            return
        view = await tools["canvas_inspect"](minister=context.agent_id)
        result = None
        tool_name = 'canvas_rename'
        if prompt.startswith("race:"):
            if self.auto_resume:
                await self.browser_barrier.wait()
            else:
                self.arrivals += 1
                if self.arrivals == 2:
                    self.both_inspected.set()
                await self.resume.wait()
            node_id = prompt.split(":", 1)[1]
            result = await tools["canvas_rename"](minister=context.agent_id, node_id=node_id,
                name=config.name + " edited", versions=view["versions"])
        elif prompt.startswith("rename "):
            _, node_id, name = prompt.split(" ", 2)
            result = await tools["canvas_rename"](minister=context.agent_id, node_id=node_id,
                name=name, versions=view["versions"])
        elif prompt.startswith('delete:'):
            tool_name = 'canvas_delete'
            result = await tools[tool_name](minister=context.agent_id, node_ids=[prompt.split(':')[1]], versions=view['versions'])
        elif prompt.startswith('glue:'):
            tool_name = 'canvas_organize'
            _, source, target = prompt.split(':')
            result = await tools[tool_name](minister=context.agent_id, operation='glue', node_ids=[source], target_id=target, versions=view['versions'])
        elif prompt.startswith('resize:'):
            tool_name = 'canvas_update'
            result = await tools[tool_name](minister=context.agent_id, updates=[{'node_id': prompt.split(':')[1],
                'patch': {'size': {'width': 140, 'height': 120}}}], versions=view['versions'])
        if result is not None:
            self.results.append(result)
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_COMPLETED,
                             {"name": tool_name, "response": result})
            text = f"Canvas action: {result['error']['code']}" if isinstance(result, dict) and result.get("ok") is False else "Renamed the card."
            if result.get('status') == 'confirmation_required' if isinstance(result, dict) else False:
                text = 'Please review the affected cards and connections in my panel. Nothing has been deleted yet.'
            elif tool_name != 'canvas_rename' and not (isinstance(result, dict) and result.get('ok') is False):
                text = 'Canvas changes applied.'
        else:
            text = "Inside my circle: " + ", ".join(node["name"] for node in view["nodes"])
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE, {"text": text})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {"text": text}, run_status=RunStatus.SUCCEEDED)

    async def chat_setup(self, config, context, runtime_input, tools, prompt):
        self.setup_prompts.append((config.system_instruction, runtime_input.prompt))
        if prompt == "你好":
            text = "你好，可以开始聊天。"
        else:
            view = await tools["canvas_inspect"](minister=context.agent_id)
            if "create" not in view["allowed_operations"]:
                text = "我目前只能查看这片区域。请开启画布编辑权限，我再配置独立的对话区和参与聊天的 Agent。"
            else:
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {"text": "计划：创建独立对话区，连接参与聊天的 Agent，留出空间，并检查它是否已加入会话。"})
                participant = next((node for node in view["nodes"] if node["type"] == "agent"), None)
                origin = view["principal"]["position"]
                if participant is None:
                    participant = await tools["canvas_create"](minister=context.agent_id, type="agent", name="Chat helper",
                        position={"x": origin["x"] + 180, "y": origin["y"] + 180}, versions=view["versions"])
                    view = await tools["canvas_inspect"](minister=context.agent_id)
                chat = next((node for node in view["nodes"] if node["type"] == "conversation"), None)
                if chat is None:
                    chat = await tools["canvas_create"](minister=context.agent_id, type="conversation", name="Agent 对话区",
                        position={"x": origin["x"] - 280, "y": origin["y"]}, versions=view["versions"])
                if self.after_chat_created:
                    await self.after_chat_created()
                check = await tools["canvas_inspect"](minister=context.agent_id, source_id=participant["id"], target_id=chat["id"])
                allowed = next((item for item in check.get("connection_options", []) if item["relationship"] == "participate" and item["permitted"]), None)
                if allowed:
                    result = await tools["canvas_connect"](minister=context.agent_id, source=allowed["source"], target=allowed["target"],
                        relationship=allowed["relationship"], versions=check["versions"])
                    self.results.append(result)
                verified = await tools["canvas_inspect"](minister=context.agent_id, query=chat["id"])
                ready = verified["nodes"][0]["chat_readiness"]
                text = (f"已配置独立的 Agent 对话区，参与者是 {participant['name']}。可以发送消息；尚未验证模型回复。"
                        if ready["routing_ready"] else "尚未完成：对话区已创建，但参与者未连接，暂时无法与 Agent 聊天。")
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE, {"text": text})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {"text": text}, run_status=RunStatus.SUCCEEDED)


def runtime_services(tmp_path):
    registry = load_plugin_registry()
    install_test_plugin(registry, "test.minister", lambda registration: registration.register_runtime_provider("test.minister", MinisterTestRuntime))
    return create_services(replace(Settings.for_data_root(tmp_path), agent_runtime="test.minister"), plugins=registry)
