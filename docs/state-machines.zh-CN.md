# 状态机：当前实现与职责边界

[English](state-machines.md)

每个 Agent 只有一个状态编排空间，其中包含 **SYSTEM 系统状态**和 **USER 用户状态**。Execution 是这个 Agent 内部的一组 SYSTEM-owned states，不是另一台状态机。它们共用引用、事件、条件、可视化和求值器输入，但写权限不同。系统状态可以被观察、被规则引用；用户不能直接赋值。指向系统状态的连线表示请求已有合法操作，真实状态仍由所属运行子系统的事实决定。

升级时识别未修改的旧 Agent 默认生命周期，直接将其归入 SYSTEM，避免复制出 `legacy_status`。已经重复的数据在初始化时合并，同时更新主显示、运行值和只读引用，移除五条重复的内置规则，保留历史版本。真正自定义的状态、修改过的规则及被其他对象赋值的用户状态组保留。

所有本地状态使用同一套所属对象坐标。旧的分组内坐标在打开时合并并处理跨组重叠，保存后写入独立的布局数据。连线复用主画布的边界计算，随拖动沿圆形状态边界调整端点；双向及多条连线错开，自环在圆周上分开起止点，并从节点外侧连接。

## 哪些节点有状态机

| 节点 | 默认定义 | 初始状态 | 编辑器 |
| --- | --- | --- | --- |
| Agent（包含声明 `core.agent` 的插件节点） | Execution · SYSTEM：idle、running、waiting、error | idle | 有 |
| Legion | available；不假定审批、规划等业务流程 | available | 有 |
| Sandbox | 无可编辑状态机；资源运行器管理 stopped/ready/running/error | stopped | 无 |
| 文本、图片、Conversation 等内置节点 | 无默认状态机 | 由节点类型决定 | 无 |

插件可在现有 `state_machine=...` 注册中声明 `ownership: "system" | "user"`（缺省为 user）。系统组提供 `owner`、稳定状态 ID、只读 `projection` 和合法 `commands`。没有权威运行生命周期的插件无需增加系统组，也无需实现新的接口。`core.agent` 始终保留 RunManager 的 Execution 声明，插件不能替换它。普通插件也可以只声明 `state_machine_editor=True`，从空白配置用户状态组。

编辑器能力由类型声明，`has_state_machine` 表示是否存在定义。旧数据中残留的图不会让 Sandbox 获得编辑入口。不存在全局通用的 Planning / Awaiting review 模板。

## Agent 默认转换

新建 Agent 默认只有 Execution 系统组，保留已有稳定组 ID `status`。下列映射属于 RunManager 的只读投影，不在用户规则列表中，不能改名、删除或重新指向其他状态。

| 运行事实 | 默认目标 |
| --- | --- |
| `agent.ready`：Agent 运行环境初始化完成 | idle |
| `agent.work_started`：至少一个 Run 处于 running | running |
| `agent.work_waiting`：仍有未结束 Run，但没有 running 的 Run | waiting |
| `agent.work_finished`：所有 Run 已结束，本次结束不是失败 | idle |
| `agent.runtime_failed`：所有 Run 已结束，最后结束的 Run 为 failed | error |

这里看的是同一 Agent 的全部 Run。一个 Run 结束时，只要另一个仍在执行，Agent 就保持 running。全部剩余 Run 都在等待时显示 waiting，即使等待已经释放执行名额。最后一个 Run 失败后默认进入 error；下一次任务启动或运行环境重新就绪，按图中的规则恢复。

业务状态由用户按需创建。直接点击“添加状态”即可创建 USER 状态，再将 Execution / Running 锚点连接到 Researching，无需先进入其他分组。编辑器自动使用 `state.entered` 和稳定状态 ID，不要求用户再选择 `agent.work_started`。退出使用 `state.exited`；`state.current` 在相关操作事件到来时检查当前状态，不引入轮询或 Timer。

## 编辑、保存和应用

1. 打开 Agent 或 Legion 的状态机。新卡片已有真实默认状态，活动版本会高亮当前状态。
2. 默认进入该 Agent 唯一的编排画布，所有 USER 分组与 SYSTEM 状态共存。SYSTEM 用底色、双线边框和标记区分，Execution 不再单独占用画布或分区。新 Agent 初始只有四个系统状态；首次“添加状态”才创建用户组，选中系统状态也不会禁用入口。分组菜单仅选择新增状态的归属，不隐藏其他本地状态。当前对象、展开成员和引用对象的全部状态自动显示在窗口内，无需逐个添加或显隐切换。
3. **保存更改**生成对象自己的草稿版本。拖动布局仅修改展示数据，不生成新的规则版本。
4. **应用已保存版本**将节点唯一实例切换到该版本。当前状态仍在新图中时保留；已被删除时回到对应组的新初始状态。条件计数和引用绑定重建，尚未派发的旧版本动作取消。已开始的外部动作不会因此撤销。

画布只展示状态与边，不生成 event 或 ANY 节点。Canonical edge 是可折叠的弱化背景，点击后才展开底层运行事实；系统状态仍不可改名、删除或直接赋值。事件匹配、条件、动作和诊断只在连线详情或主动打开的面板中呈现。

节点主状态只有一个 `default` 实例。应用失败会回滚版本、状态和绑定。禁用实例只暂停用户规则及待派发动作；系统投影继续响应真实运行事实。用户引用失效也不会冻结 Execution。Apply 前检查依赖，删除被其他定义引用的状态或 command 会报错。清除对象定义会恢复类型默认图。

辅助工作流没有 `status_entity_id`，不投影为卡片状态，可以使用独立 scope。它们继续显式绑定不可变版本；同一 scope 不会被保存动作自动改绑。这项通用能力供插件使用，内置 Agent 不再额外创建第二套“业务状态”。

## 唯一状态来源与执行边界

| 数据 | 唯一职责/来源 |
| --- | --- |
| 系统状态、canonical 投影、command 声明 | 所属 runtime / 节点类型注册 |
| 用户状态、规则、条件、动作、引用 | 每个对象的不可变定义版本 |
| 节点当前状态、条件记忆、消费游标 | StateMachineStore 的节点实例，值保存在现有 StateStore |
| `Card.status`、`AgentInfo.status`、`operational_status` | 声明的系统运行状态 |
| `primary_state`、`status_label` | `status_entity_id` 选择的卡片主显示组 |
| `state_groups` | 所有本地组的当前状态与 ownership |
| 状态坐标、视口、展开信息 | 独立 presentation |
| 单次任务的 created/running/waiting/succeeded/failed/cancelled/interrupted | RunStore / RunManager |
| 执行名额、取消、超时、工具权限 | 现有运行与权限系统 |
| 文档内容、共享值、会话数据 | 节点文档与 scoped StateStore |

Run 的生命周期描述一次真实任务，不是另一份 Agent 卡片状态。把卡片状态命名为 idle 不会终止 Run，也不会释放执行名额。运行/停止按钮读取实际活动 Run 数和占用数，不再靠状态字符串判断。普通卡片更新不能绕过状态机直接改状态；状态字段也不再作为 Agent/Legion 可编辑配置暴露。兼容旧数据库的 status 字段不再是这些节点的运行权威。

## 事件、求值和动作

编辑器的事件选择来自后端目录 `/api/state-machines/events?card_id=…`：Agent 聚合活动、Run 生命周期、已授权能力调用、文档/资源操作、工作执行结果、状态进入和存储值变化。前端不维护工具名到事件的特殊映射。

运行事件写入 SQLite `operation_events`。Agent 活动通过同步入口按日志顺序求值，使运行 API 返回时状态已经更新；后台消费者处理其他事件与后续动作。两条入口共用同一求值器、游标和去重收据。模拟器也复用该求值器，只接受合成事件，不触发真实动作。

所有用户规则基于同一快照判断：已观察到的系统事实，以及转换前的用户状态。每个受影响组按定义顺序最多选择一条匹配规则，不同组可以同时转换；带额外 effects 的规则整体占用对应组。只有 command 的规则占用来源组，不占用系统目标组。系统投影、用户状态、收据、条件记忆、游标和 outbox 在同一事务内提交，之后才派发副作用。重复事件不会重复动作。enter/exit 事件标识权威实例和状态组，连锁深度上限为 32。

后续动作通过已有入口调用：能力代理、节点文档/资源操作、或 `RunManager.start_run()`。仍然检查权限、目标可用性和并发名额。调用者/目标可以是当前对象、指定对象、本次调用关联对象或本次产生的对象。外部副作用不属于 SQLite 事务；崩溃后不能确定结果的动作标记 uncertain，不自动重放。

指向系统状态的规则保存 `command: {entity_id, state_id, command_id, arguments}`，解析为现有 action/outbox，不生成系统 state effect。声明提供 operation ID、输入 schema、权限要求和可能结果。目前 Execution 暴露 Start work（`host:run`）；其他命令必须引用已有注册入口。诊断分别显示 USER、SYSTEM 和 COMMAND / accepted；操作被接受不代表目标系统状态已经发生。容器只能请求当前成员执行 Run；Agent 间调用仍需通信授权，派发和 admission 都由 broker 重新检查。

## Legion、模板与旧数据

Legion 保存自己的状态组，通过引用使用成员的权威定义。长期身份为 `(card_id, state_group_id, state_id)`；definition version 只作调试元数据。成员 Apply 后，只要 ID 保留，引用就继续有效；删除前会检查依赖。Legion 可以观察成员 Execution 并请求合法操作，但不能直接赋值。动态对象继续通过已有 invocation association 解析，不引入 Summoning 特例。

模板只携带用户组、用户规则（含系统 anchor/command 引用）、布局、稳定引用和启用意图。`system_groups` 只记录需要的组 ID，系统声明在目标类型中重新解析；缺少状态或 command 会在部署/Apply 报 incompatibility。现代模板保持 active→active、draft→draft，不复制当前状态、游标、条件记忆、收据、outbox 或 Run ID。

旧 `config.state_machine` 保持 disabled draft 安全策略。目标类型补入系统声明；旧可编辑组与系统 ID 冲突时，以 `legacy_…` 用户 ID 保留并重映射本地规则。已运行的旧定义迁移为不可变新版本，保留有效用户进度；声明升级检查依赖并取消尚未派发的请求。重复初始化保持幂等。

## 代码入口

- `backend/agent_state_machine.py`：Agent 事实词汇和默认图、Legion 默认图。
- `backend/plugins/registry.py`：类型声明、继承、验证及编辑器能力。
- `backend/state_machine.py`：图、条件、动作及主状态组 schema。
- `backend/state_machine_store.py`：定义版本、唯一节点实例、状态投影、引用和迁移。
- `backend/state_machine_runtime.py` / `state_machine_preview.py`：共享求值、日志消费、动作派发和模拟。
- `backend/runs/manager.py`：真实 Run、聚合事实、执行容量；不决定图的目标状态。
- `backend/world/store.py` / `services.py`：对外卡片投影和生命周期接入。
- `frontend/src/stateMachines/`：图、事件编辑、模拟、保存、应用及运行诊断。
- `frontend/src/state/worldStore.ts` / `cards/AgentCard.tsx`：消费后端状态、按执行计数控制按钮。
