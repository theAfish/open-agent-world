# 用 MatCreator 开始材料研究

MatCreator 将研究 Agent、对话、计算环境、知识和产物组织在一个工作区。
先从一个小任务开始：让它检查计算环境，生成一份结构文件，再核对结果。

## 准备工作区

1. 在 Library 安装 MatCreator 及安装计划列出的依赖卡包。等待依赖准备完成，按提示重启。
2. 开启 MatCreator 卡包，把 **MatCreator research** 加入牌组并放到画布。
3. 打开 Legion 的 **Workspace mode**。
4. 在 **Settings → Models** 配置模型，并在 MatCreator Agent 中选择它。该 Agent 使用 ADK/LiteLLM 支持的模型连接，不能使用仅由 TypeSafe 适配器提供的连接。
5. 打开连接的 Sandbox，选择运行环境、检查诊断并启动。

MatCreator 的 Agent 运行依赖由卡包准备。科学计算软件、模型权重、材料数据库、许可文件和远程计算账号仍需在选定的计算环境中配置。安装卡包不代表这些资源已经就绪。

模型密钥放在 OAW 模型设置中，计算凭据通过相应环境设置管理；不要写入任务说明或模板。

## 完成一个小任务

在 Conversation 中选择 MatCreator Agent，发送：

> 先检查可用的科学计算环境。为一个小型铜超胞制定计划，生成结构后核对原子数，并发布结构文件；缺少软件时先告诉我。

默认 **normal** 模式先给出计划，确认后执行。检查计算规模、所需资源和输出，再批准计划；**flash** 模式会直接委派工作。

运行时在 **Agent graph** 查看任务历史，在 Conversation 查看消息与工具活动。点击一条记录，在原位置展开详情；再次点击或按 Escape 收起。搜索可以定位任务，详情中的连接引用可以跳到相关记录。图中的位置不代表固定 Agent，也不表示依赖关系。

工作区中常用的区域是：

| 区域 | 用途 |
| --- | --- |
| Conversation | 讨论任务、确认计划、查看回复 |
| Skills & knowledge | 查看可使用的知识和技能 |
| Sessions | 创建或切换会话 |
| Files / File preview | 浏览 Sandbox 文件并预览内容 |
| Structure viewer | 检查支持的结构文件 |
| Research artifacts | 查看发布的产物及其版本 |

点击文件先打开预览；连接的 Structure viewer 成功解析结构后，会自动切换到结构视图。**Pinned** 状态不会跟随新文件。无效或不支持的结构保留在普通预览中。

任务结束后核对原子数、单位、结构和计算假设，再使用产物。运行成功、较低误差或 Agent 的肯定回复都不能代替科学检查。

## 继续工作与排查

- 切换 Sessions 可回到相应会话与执行历史；打开详情或选择记录不会改变调度。
- **Jobs → Agent** 显示当前会话的跟踪任务。暂停监控不会停止远程计算；取消需要计算服务确认。
- Agent 看不到资源时，检查连接及授权。把卡片放在同一 Legion 中不会自动授予权限。
- 缺少运行依赖时，从 Library 查看准备失败的原因并修复；不要反复把依赖安装到另一个 Python 环境。
- 已有工作区保留自己的布局。需要新布局时重新放置研究工作区，或使用 **Edit layout** 调整。

需要扩展或维护此卡包时，再查阅 [Pack 说明](../../plugins/matcreator/README.md)与[运行时契约](../../plugins/matcreator/docs/runtime.md)。
