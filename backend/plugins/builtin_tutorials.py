"""Core cards use the same public tutorial contract as third-party Packs."""
from backend.plugins.tutorials import TutorialDefinition, TutorialStep


def bilingual(en: str, zh: str) -> dict[str, str]:
    return {"en": en, "zh-CN": zh}


AGENT_TUTORIALS = (TutorialDefinition(
    id="getting-started", title=bilingual("Meet your Agent", "认识智能体"),
    summary=bilingual("Choose a model, give it instructions, then connect its tools and conversation.", "选择模型、设置指令，再连接工具与对话。"),
    steps=(
        TutorialStep(id="configure", title=bilingual("Give it a role", "设置角色"),
            body=bilingual("Open the Agent card and set its instructions and model. Model connections are managed in **Settings → Models**.", "打开智能体卡片，设置指令和模型。模型连接可在**设置 → 模型**中管理。")),
        TutorialStep(id="connect", title=bilingual("Connect its resources", "连接所需资源"),
            body=bilingual("Drag a port to a Conversation, Text or Sandbox card and choose the capability. Open the Conversation to send a message when you are ready.", "从端口拖线到对话、文本或沙盒卡片，选择允许使用的能力。准备好后，打开对话发送消息。")),
    ),
    document=bilingual("## Agent cards\n\nInstructions define the Agent's role. Its selected model provides responses. Connections grant access to specific resources and tools; placing two cards nearby does not grant access.\n\nUse a Conversation to communicate with connected Agents. Model setup and sending messages remain your choice.", "## 智能体卡片\n\n指令定义智能体的角色，选定的模型提供回复。连线授予特定资源和工具的使用权限；仅将两张卡片放在一起不会授予权限。\n\n通过对话与连接的智能体交流。模型配置和消息发送由你操作。"),
),)

CONVERSATION_TUTORIALS = (TutorialDefinition(
    id="getting-started", title=bilingual("A shared conversation", "共享对话"),
    summary=bilingual("Connect an Agent and use this card as your conversation space.", "连接智能体，用这张卡片进行交流。"),
    steps=(
        TutorialStep(id="connect", title=bilingual("Connect an Agent", "连接智能体"), body=bilingual("Connect an Agent to this Conversation and choose the communication capability. The Agent needs a configured model to reply.", "将智能体连接到这张对话卡片，选择通信能力。智能体需要配置模型才能回复。")),
        TutorialStep(id="talk", title=bilingual("Start a conversation", "开始交流"), body=bilingual("Open the card, enter your message and send it. You can keep the Conversation open beside other cards in a Legion Workspace.", "打开卡片，输入消息并发送。你也可以在 Legion 工作区中将对话与其他卡片并排打开。")),
    ),
),)

TEXT_TUTORIALS = (TutorialDefinition(
    id="getting-started", title=bilingual("Give your Agent reference material", "为智能体提供参考资料"),
    summary=bilingual("Write a note and connect it to an Agent when you want to share it.", "写下笔记，需要共享时再将它连接到智能体。"),
    steps=(
        TutorialStep(id="edit", title=bilingual("Write your note", "编写笔记"), body=bilingual("Open the Text card to edit its content. Give it a useful name so it is easy to find on the canvas.", "打开文本卡片编辑内容，起一个便于在画布中识别的名称。")),
        TutorialStep(id="share", title=bilingual("Choose access", "选择访问权限"), body=bilingual("Connect the Agent to this card and choose the required read or write capability. The connection controls what the Agent can do with your text.", "将智能体连接到这张卡片，选择所需的读取或写入能力。连线决定智能体能够如何使用文本。")),
    ),
),)

SANDBOX_TUTORIALS = (TutorialDefinition(
    id="getting-started", title=bilingual("Prepare a Sandbox", "准备沙盒"),
    summary=bilingual("Review its environment and files before allowing an Agent to execute commands.", "允许智能体执行命令前，先检查环境与文件配置。"),
    steps=(
        TutorialStep(id="environment", title=bilingual("Review the environment", "检查环境"), body=bilingual("Open the Sandbox and review its runtime, working directory and file access. Start it when your configuration is ready.", "打开沙盒，检查运行时、工作目录与文件访问配置。配置完成后再启动。")),
        TutorialStep(id="connect", title=bilingual("Connect execution tools", "连接执行工具"), body=bilingual("Connect an Agent to the Sandbox and choose the execution capability. Check the Sandbox status and output while the Agent works.", "将智能体连接到沙盒，选择执行能力。智能体工作时可在沙盒中检查状态和输出。")),
    ),
),)
