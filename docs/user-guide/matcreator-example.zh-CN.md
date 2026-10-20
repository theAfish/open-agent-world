# MatCreator：原生 Agent 研究工作区

[English documentation](../../plugins/matcreator/README.md) | **简体中文**

MatCreator 卡包提供专属 **MatCreator Agent** 和 **MatCreator research** Legion。
规划器、执行协调器和递归子 Agent 的源码由本项目的 MatCreator Pack 独立维护；计算、知识、产物和展示通过 OAW 卡片连接。
原来的拼装式 Legion、专用研究任务板和示例创建脚本已移除，卡包不再依赖 Barracks。

## 安装与部署

正常 OAW 安装、开发启动和桌面打包会自动准备 MatCreator 运行环境，无需单独运行安装脚本，
也无需填写卡片的 Python 路径。首次安装需要联网下载依赖，后续复用已验证的环境。
运行环境由卡包管理并与 OAW 的 Python 依赖隔离；主程序 Python 低于 3.12 时会自动准备所需解释器。

安装时只准备第三方依赖和 LiteLLM 的公开分词缓存，不再下载或安装外部 MatCreator。
执行内核位于 `plugins/matcreator/oaw_matcreator_engine/`，随 Pack 一起分发，修改后在下一个 worker 生效。
原始来源和适配范围见 [Pack 内核说明](../../plugins/matcreator/oaw_matcreator_engine/UPSTREAM.md)。

1. 重启后端并重建或重新加载前端。
2. 在 Library 打开 MatCreator 卡包，将 **MatCreator research** 加入牌组并放到画布。
3. 打开 Legion 的 **Workspace mode**。
4. 在 **Settings → Models** 设置默认模型，并配置连接的 Sandbox。

外部 Python 和 MatCreator checkout 覆盖项已移除；加载旧卡片时会忽略这两个字段。
安装和启动必须使用同一个 Python 环境。Windows 的 `scripts/dev.ps1` 已统一使用
`backend/.venv/Scripts/python.exe`，不会再通过 `uv run --with-editable` 创建临时后端环境。
若提示卡包安装不完整，错误中会列出后端 Python 和查找目录；先核对启动环境，避免反复安装到另一个环境。

模型密钥由 OAW 管理，不写入卡片或 Legion 模板。材料软件、环境变量与远程计算凭据配置在 Sandbox 中。
原生网页服务的远程任务监控不随卡片启动；Agent 应通过已连接工具检查远程任务，并保留任务 ID。

## 工作区布局

新版预设为 revision 2：

| 区域 | 内容 |
| --- | --- |
| 左侧 | **Agent graph**：主 Agent、规划器、执行器和子 Agent 的关系与状态 |
| 中间 | **Conversation**、**Skills & knowledge**、**Structure viewer**、**File preview**、**Research artifacts**、**Research files & compute** 标签 |
| 右侧上方 | **Sessions**，选择或创建会话 |
| 右侧下方 | **Files**，浏览 Sandbox 文件 |

执行图默认按历史纵向阅读，最多使用三条时间轨道；轨道表示记录中的时间重叠，不代表依赖。
较早完成的执行组会自动折叠，运行中、失败和等待处理的记录保持可见。展开 Execution 可查看内部 Step。

点击节点查看真实上游、下游和输出，点击来源引用可跳转到对应 Step，并可返回原节点。
**Dependency lens** 在同一面板显示局部依赖图，支持逐跳展开；**Step graph** 查看本次 Execution 的内部图。
复杂依赖可聚焦后返回历史。跨 Execution 引用默认只显示来源，不画贯穿整个历史的长线。
切换会话会切换图和原生执行状态；图的折叠、选择与布局不改变任务调度。

**Agent graph** 是 Core essentials 中的通用独立卡片。通过 **Observe execution**
将图卡连接到普通 Agent、Agent Barracks、Summoning 或其他执行来源，再在图卡中选择来源。
局部图中，实线表示控制依赖，虚线表示结果交付或产物读取，点线表示重试或重新规划；父子关系仅表示包含。
其他卡片或运行时可以使用[公共执行图接口](../developers/execution-graph.md)提供自己的数据。

默认 **normal** 模式先在会话中给出计划，确认后执行；**flash** 模式直接委派。
例如：“规划一个铜超胞研究，先检查科学计算环境，生成结构后核验原子数并发布产物。”
执行成功不等于科学结果已通过验收，仍需核查输出、单位与计算假设。

## 历史与溯源边界

Pack 0.7 需要公共插件接口 1.31。每次 Step 尝试使用独立 ID，重试不覆盖失败记录；计划和执行历史持久化后可恢复。
新 Run 尚未发布执行图时保留上一份详细记录并标明等待；Conversation 回合完成不会自动把未完成的 Step 标为成功。

数据依赖只来自显式交付的历史 Step 结果或成功读取的指定产物版本，不会根据编号、执行先后或自由文本猜测。
产物读取表示已交付给 Agent，不证明模型在推理中实际使用了它。跨会话来源和旧记录中未保存的溯源信息不会被补造。

历史按需渲染，局部图最多显示 160 个相关节点，达到限制时会提示并允许继续选择节点探索。
单份宿主投影上限为 10,000 节点、50,000 条边和 8 MiB；达到限制会明确提示，Pack 磁盘历史仍保留。

## 文件与标签联动

在右侧点击文件，会打开中间的 **File preview**。
如果已连接的 Structure viewer 成功读取并解析该结构，会自动切到 **Structure viewer**；
从会话附件打开结构时，临时文件预览弹窗也会随之关闭。
普通文本、无效结构和不受支持的文件继续使用普通预览。

结构视图固定为 **Pinned**、重新加载、后台刷新或重新连接时，不会自动切换标签。
切换标签保留会话草稿、文件选择和卡片内部状态。

联动通过本体公共 API `host.workspace.activate()` 实现，仅影响当前浏览器中打开的工作区。
它不会自动保存或重排布局，不会恢复用户隐藏的 section，也不会在编辑布局时抢走标签。
插件接入方式见[工作区设计文档](../developers/workspace-design.md#activate-content-after-an-operation)。

## 升级与验证范围

新部署使用新版布局。现有保存布局由用户管理，不会被预设更新覆盖；可以重新部署，或用 **Edit layout** 调整。
移除旧功能不会删除已有世界记录，也不会把旧的 OAW Agent 会话自动转换为 MatCreator 会话。

本地测试覆盖原生编排与模拟模型、会话隔离、权限撤销、产物核验、执行图、工作区标签联动和真实结构渲染。
这些测试不代替真实模型、Bohrium 或科学计算验收。
