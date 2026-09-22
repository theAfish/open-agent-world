"""Local scripted demo provider. No model, network request, or tool execution."""
from backend.agents.mock import MockAgentRuntime
from backend.agents.models import AgentEvent, AgentEventType
from backend.plugins.registry import PluginDescriptor
from backend.runs.models import RunStatus
from pydantic import BaseModel
from open_agent_world.plugin_api import (
    DeploymentSurface, NodeDeploymentDefinition, NodeTypeDefinition, PackDefinition,
    NodeDocumentDefinition, NodeDocumentAction, NodeDocumentDownload,
)


class NotesConfig(BaseModel):
    heading: str = "Workspace notes"
    internal_connection: str = "PRIVATE-DEMO-CONNECTION"


class NotesDocument(BaseModel):
    text: str = "Try saving a note. This is the same plugin view used in the engineering Workspace."
    internal_note: str = "PRIVATE-DEMO-DOCUMENT"


def save_note(value, arguments):
    text = arguments.get("text")
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("Text must be a string of at most 10000 characters")
    return {**value, "text": text}


class DemoRuntime(MockAgentRuntime):
    async def execute(self, config, context, runtime_input):
        # Deliberately do not echo the composed prompt: it contains internal context.
        text = (
            "你好！你的消息已经通过锁定应用到达了后台助手。\n\n"
            "这是无需 API Key 的本地演示，我会返回这段预设回复。\n\n"
            "你可以继续体验：\n"
            "1. 新建对话，发送消息，再刷新页面查看保留的记录。\n"
            "2. 在右侧切换「使用指南」和「交付清单」。\n"
            "3. 退出登录，重新输入体验密码进入。\n\n"
            "正式应用可在工程端接入真实模型后重新发布，用户仍然使用这样的简洁页面。"
        )
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                         {"text": text, "final": True})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                         {"text": text}, run_status=RunStatus.SUCCEEDED)


class DemoPlugin:
    descriptor = PluginDescriptor(id="example.deployment-demo", version="0.2.0",
                                  plugin_api_version="1.21", name="Deployment demo")

    def register(self, registration):
        registration.register_node_type(NodeTypeDefinition(
            id="example.deployment-notes", label="Workspace notes", description="Deployable plugin example",
            icon="NotebookPen", color="#609b86", deck_id="example.deployment-demo", deck_label="Deployment demo",
            deck_icon="NotebookPen", default_name="Workspace notes", default_size=(360, 260),
            default_status="idle", statuses=frozenset({"idle"}), config_model=NotesConfig,
            surfaces={"preview": True, "inspector": True, "workspace": True},
            frontend={"body": "notes", "workspace": "notes"},
            document=NodeDocumentDefinition(model=NotesDocument,
                actions={"save": NodeDocumentAction(save_note)},
                downloads={"text": lambda value: NodeDocumentDownload("notes.txt", value["text"].encode(), "text/plain")}),
            deployment=NodeDeploymentDefinition(surface=DeploymentSurface(
                config_fields={"heading"}, document_fields={"text"}, document_actions={"save"}, downloads={"text"})),
        ))
        registration.register_pack(PackDefinition(id="example.deployment-demo", name="Deployment demo",
                                                 cards=("example.deployment-notes",)))
        registration.register_runtime_provider(
            "example.deployment-demo", lambda capability_provider, **options: DemoRuntime(capability_provider))


def create_plugin():
    return DemoPlugin()
