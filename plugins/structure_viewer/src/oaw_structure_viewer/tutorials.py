"""Structure viewer help, using only the public declarative tutorial contract."""
from open_agent_world.plugin_api import TutorialDefinition, TutorialStep


def localized(en: str, zh: str) -> dict[str, str]:
    return {"en": en, "zh-CN": zh}


REFERENCE = localized(
    """## Connect a file source

| Source | What you can view | How to open it |
| --- | --- | --- |
| Sandbox | Structure files in its accessible file browser | Open the Sandbox window and click a file name. |
| Conversation | Structure file attachments in the current session | Open the Conversation window and click an attachment's name. The download button is separate. |

Drag a connection from the Structure viewer's port to the source card. Choose **Follow opened files**, then **Grant capability**. Open the viewer's detailed view or Window alongside the source window. The compact preview shows the file name; the 3D scene appears in the detailed view or Window.

You can connect more than one source. In **Following** mode, the most recently opened file among those connected sources is displayed. Connect to the Conversation or Sandbox containing an Agent's output file; an Agent itself is not a file source for this viewer.

## Display your first structure

For example, open **water.xyz** in a connected Sandbox, or click the same attachment's name in a connected Conversation. The viewer's toolbar shows the selected file and its source. Opening a file does not run a Sandbox command or send an Agent message. Dropping a file directly onto the 3D scene is not supported; open it in the connected source instead.

Drag inside the 3D scene to rotate, use the mouse wheel to zoom, and hover over an atom for element information. These gestures operate on the structure view. Changes in the viewer do not write back to the source file.

## Following, pinning and reloading

- Click **Following** to pin the current file. Opening other files then leaves this viewer on the pinned file.
- Click **Pinned** to resume following. The most recently opened file in the connected sources is selected.
- If the source file changes, click **Reload** to read it again. External changes are not watched automatically. For a newly uploaded Conversation attachment, click that attachment to open its new version.
- Disconnecting removes access to that source. Closing its window or switching Conversation sessions clears its opened-file context and pin. Reopen the file to view it again. Selection and pins do not survive a browser reload.

## Formats and limits

Supported: **CIF / MCIF**, **POSCAR / CONTCAR / .vasp**, **XYZ / EXTXYZ**, and **structure JSON**. JSON must contain crystal or molecule structure data; arbitrary JSON is not supported. This adapter displays one structure, not trajectories or compressed archives. Files must be at most **16 MiB**, with at most **20,000 input atoms**.

## If nothing appears

1. Check that the viewer and the intended Sandbox or Conversation have a **Follow opened files** connection.
2. Open both windows, then click the file or attachment name in the source. A download alone does not select the file.
3. Expand the viewer to its detailed view or Window. Check the file name and source shown in its toolbar.
4. If another file remains visible, click **Pinned** to resume following. If the file changed, use **Reload**.
5. Read any error in the viewer. Check the file format, size, atom count and source access; a matching extension alone does not make the contents a valid structure.

Tutorials can be closed at any time and reopened from **Help → Card tutorials & docs**.
""",
    """## 能连接什么

| 来源卡片 | 能查看的内容 | 怎样打开 |
| --- | --- | --- |
| 沙盒 | 文件浏览器中可访问的结构文件 | 打开沙盒窗口，点击文件名。 |
| 对话 | 当前会话中的结构文件附件 | 打开对话窗口，点击附件名称；下载按钮是另一个操作。 |

从结构查看器的连接端口拖线到来源卡片，选择**跟随打开的文件（Follow opened files）**，再点击**授予能力**。把查看器的详细视图或窗口与来源窗口一起打开。紧凑预览只显示文件名，三维结构在详细视图或窗口中显示。

可以同时连接多个来源。**跟随中**会显示这些来源里最近一次打开的文件。若要查看智能体生成的结构，请连接保存输出文件的对话或沙盒；智能体本身不是这个查看器的文件来源。

## 显示第一个结构

例如，在已连接的沙盒中打开 **water.xyz**，或点击已连接对话中的同名附件。查看器顶部会显示当前文件名及来源。打开文件不会执行沙盒命令，也不会向智能体发送消息。三维区域不接受直接拖入文件，请从连接的来源中打开。

在三维区域内拖动可旋转结构，滚轮可缩放；鼠标悬停在原子上可查看元素信息。这些手势操作的是结构视图，查看器中的修改不会写回原文件。

## 跟随、固定与重新加载

- 点击**跟随中**，固定当前文件。此后在来源窗口打开其他文件，查看器仍停留在已固定的文件上。
- 点击**已固定**，恢复跟随，显示连接来源中最近打开的文件。
- 原文件更新后，点击**重新加载**重新读取；查看器不会自动监听外部修改。若对话中重新上传了一个附件，请点击新附件，打开它的新版本。
- 断开连线会撤销该来源的访问。关闭来源窗口或切换对话会话，会清除该来源的打开记录与固定状态；再次打开文件即可继续查看。刷新浏览器也不会保留文件选择和固定状态。

## 支持格式与范围

支持 **CIF / MCIF**、**POSCAR / CONTCAR / .vasp**、**XYZ / EXTXYZ** 和**结构 JSON**。JSON 必须包含晶体或分子结构数据，任意 JSON 文件不能直接查看。当前展示单个结构，不支持轨迹或压缩文件。文件不超过 **16 MiB**，输入原子数不超过 **20,000**。

## 没有显示结构时

1. 检查查看器与目标沙盒或对话之间是否有**跟随打开的文件**连线。
2. 打开双方窗口，在来源中点击文件名或附件名称；仅下载文件不会选中它。
3. 将查看器展开为详细视图或窗口，确认顶部显示的文件名与来源。
4. 若一直停留在旧文件，点击**已固定**恢复跟随；文件内容更新后点击**重新加载**。
5. 阅读查看器中的错误提示，检查格式、大小、原子数量与来源访问权限；仅有正确扩展名不代表内容是有效结构。

教程可随时关闭，之后从**帮助 → 卡片教程与文档**重新打开。
""",
)

TUTORIALS = (
    TutorialDefinition(
        id="first-structure", revision=1,
        title=localized("View your first structure", "查看第一个结构"),
        summary=localized("Connect a Sandbox or Conversation, open a structure file, and explore it in 3D.",
                          "连接沙盒或对话，打开结构文件，再用三维视图观察它。"),
        steps=(
            TutorialStep(
                id="choose-source", title=localized("Choose where the file comes from", "先选文件来源"),
                body=localized(
                    "Connect a **Sandbox** to view files from its file browser, or a **Conversation** to view file attachments. You can connect both.\n\nFor a first try, use a **.cif**, **POSCAR** or **.xyz** file. To view an Agent's output, use the Conversation or Sandbox that contains the file.",
                    "连接**沙盒**，可以查看文件浏览器中的结构文件；连接**对话**，可以查看消息中的文件附件。两种来源也可以同时连接。\n\n第一次可以选一个 **.cif**、**POSCAR** 或 **.xyz** 文件。要看智能体的输出，就连接存放该文件的对话或沙盒。"),
            ),
            TutorialStep(
                id="connect", title=localized("Connect the viewer to the source", "连上来源，允许跟随文件"),
                body=localized(
                    "Drag from the **Structure viewer's connection port** to the Sandbox or Conversation. In the capability chooser, select **Follow opened files** and click **Grant capability**.\n\nThis connection lets the viewer read files you open in that source window. Repeat to connect another source if needed.",
                    "从**结构查看器的连接端口**拖线到沙盒或对话。在能力选择框中选择**跟随打开的文件（Follow opened files）**，点击**授予能力**。\n\n这条连线让查看器读取你在来源窗口中打开的文件。需要多个来源时，按同样的方法继续连接。"),
            ),
            TutorialStep(
                id="open-file", title=localized("Open a file in the source window", "在来源窗口中打开文件"),
                body=localized(
                    "Open the source window and the viewer's **detailed view or Window** side by side. In a Sandbox, click a structure file name; in a Conversation, click an attachment's name, not its download button.\n\nThe viewer shows the file name and source at the top, then loads the structure. Its compact preview only shows the file name. Open files through the source; dropping them onto the 3D scene is not supported.",
                    "把来源窗口与查看器的**详细视图或窗口**并排打开。在沙盒中点击结构文件名；在对话中点击附件名称，而不是下载按钮。\n\n查看器顶部会显示文件名与来源，随后加载结构。紧凑预览只显示文件名。请从来源中打开文件，三维区域不接受直接拖入。"),
            ),
            TutorialStep(
                id="explore", title=localized("Explore the structure", "旋转、缩放，查看原子"),
                body=localized(
                    "Drag inside the **3D scene** to rotate the structure and use the mouse wheel to zoom. Hover over an atom to see element information.\n\nFor example, open **water.xyz** to inspect a molecule, then open a **.cif** file to inspect a crystal. These viewing operations do not write back to the source file. Finish this chapter when you are ready to learn how to keep one file in view.",
                    "在**三维区域内**拖动可旋转结构，滚轮可缩放。鼠标悬停在原子上可查看元素信息。\n\n例如，先打开 **water.xyz** 观察分子，再打开 **.cif** 文件观察晶体。这些查看操作不会写回原文件。完成本章后，还可以了解如何固定当前文件。"),
            ),
        ),
        document=REFERENCE,
    ),
    TutorialDefinition(
        id="follow-and-reload", revision=1, after=("first-structure",),
        title=localized("Keep the right file in view", "跟随、固定与重新加载"),
        summary=localized("Control which opened file is displayed and refresh it after changes.",
                          "有多个文件时，控制显示哪一个，并在内容变化后重新读取。"),
        steps=(
            TutorialStep(
                id="pin", title=localized("Follow or pin a file", "跟随新文件，或固定当前文件"),
                body=localized(
                    "With **Following** active, the most recently opened file in any connected source is shown. To keep the current structure while opening other files, click **Following**: the button changes to **Pinned**.\n\nClick **Pinned** to resume following. Check the file name and source beside the buttons to confirm what you are viewing.",
                    "**跟随中**会显示所有已连接来源里最近打开的文件。想在打开其他文件时仍保留当前结构，就点击**跟随中**，按钮会变成**已固定**。\n\n点击**已固定**可恢复跟随。切换前后都可以检查按钮旁的文件名与来源，确认正在看哪个文件。"),
            ),
            TutorialStep(
                id="reload", title=localized("Refresh changed files", "文件变了，重新加载"),
                body=localized(
                    "After editing the source file, click **Reload** to read it again. External edits are not watched automatically. If you upload a new Conversation attachment, click that new attachment to select it.\n\nClosing the source window or switching Conversation sessions clears its opened-file context; reopen the file when you return. Disconnecting stops access to that source. Use **Read documentation** for formats and troubleshooting.",
                    "原文件修改后，点击**重新加载**重新读取；查看器不会自动监听外部修改。如果对话里上传了新的附件，请点击新附件打开它。\n\n关闭来源窗口或切换对话会话会清除打开状态，回来后重新打开文件即可；断开连线则停止读取该来源。支持格式和常见问题可通过**阅读文档**查阅。"),
            ),
        ),
        document=REFERENCE,
    ),
)
