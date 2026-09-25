# OAW demo media / 演示素材

Captured on 2026-09-14 from the running OAW web app in Microsoft Edge, at 1600 × 1000. GIFs are 1280 × 800 and loop automatically.

2026-09-14 实机录制。截图为 1600 × 1000，动图为 1280 × 800，可直接用于 README。

| Scene / 场景 | Animation / 动图 | Still / 截图 |
| --- | --- | --- |
| Research workspace / 研究工作区总览 | — | [Light / 浅色](world-overview.png) · [Dark / 深色](world-overview-dark.png) |
| Connect agents / 连接智能体 | [7.4 s](connect-cards.gif) | [PNG](connect-cards.png) |
| Tasks and dependencies / 任务与依赖 | [7.9 s](task-dependencies.gif) | [PNG](task-dependencies.png) |
| Connected structure viewer / 文件与三维结构联动 | [7.3 s](structure-viewer.gif) | [PNG](structure-viewer.png) |
| Legion on the canvas / Legion 画布分组 | — | [Light / 浅色](legion-canvas.png) · [Dark / 深色](legion-canvas-dark.png) |
| Legion Workspace / Legion 工作区 | — | [Light / 浅色](legion-workspace.png) · [Dark / 深色](legion-workspace-dark.png) |

The four Legion screenshots were captured on 2026-09-21 at 1600 × 1000. They show the same five-card Research studio on the canvas and in Workspace mode: the brief, conversation and Task Board occupy live panels while the two Agents remain available in the Legion. Messages and task progress are prepared example data.

四张 Legion 截图于 2026-09-21 从真实界面截取，尺寸为 1600 × 1000。同一个 Research studio 在画布上包含五张卡片，工作区中展示项目说明、对话和任务板，两个 Agent 仍保留在 Legion 内。消息和任务进度均为预先准备的示例数据。

## Recording notes / 录制说明

- These are real UI captures with prepared sample cards, tasks, and structure files in a separate local profile. No generated or reconstructed interface imagery is used.
- The recording uses the local mock Agent runtime without model credentials. It demonstrates UI interactions and persisted state, not live model responses or Sandbox execution.
- GIFs are assembled from sequential browser screenshots with pauses at key actions. They are not execution-time benchmarks.
- Browser checks confirmed that the two-way connection persisted, task prerequisites blocked and then unlocked the next task, and the viewer followed file changes and responded to rotation. Both overview themes were visually checked.

素材来自独立演示工作区中的真实界面，使用预先准备的示例内容。录制展示连线、任务操作和文件预览，不代表真实模型回答或沙箱执行。动图在关键步骤适当停留，方便阅读。

## Recreating the scenes

To regenerate the Legion screenshots, run from `frontend` in PowerShell:

```powershell
$env:OAW_CAPTURE_README = '1'
node scripts/run-e2e.mjs e2e/readme-legion.spec.ts
Remove-Item Env:OAW_CAPTURE_README
```

The runner uses its isolated test profile. Without the environment variable, images go to test results. The host integration check verifies retained connections, live panel contents and the saved layout.

Use a fresh profile with the local mock runtime; see [Getting started](../../getting-started.md#try-without-model-credentials).

1. Place Researcher and Writer Agents, a Project brief Text card, a Team conversation, and a Research plan Task Board. Connect the Researcher to the brief with Read, both Agents to the Conversation with Participate, and the Writer to the board with Update progress. Record dragging a Researcher → Writer connection, selecting Two-way, and granting Communicate.
2. Add four tasks: Collect sources → Compare the options → Draft the report → Review and share. Start with the first completed and the second in progress. Open a wide Task Board workspace, switch between List and Dependencies, and complete the second task to show the third becoming ready.
3. Attach a silicon CIF and a water XYZ to a separate Conversation. Connect a Structure viewer with Follow opened files. Open both workspaces side by side, open the CIF, rotate the structure, and switch between attachments.

Keep all cards and the complete dependency graph in frame. Capture the overview in both themes, preserve readable text, and retain PNG alternatives to the animations.
