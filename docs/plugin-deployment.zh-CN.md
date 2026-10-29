# 插件部署接入

插件的现有页面和已注册业务操作默认可以直接发布，无需额外声明 `NodeDeploymentDefinition`。项目拥有者（多用户项目的 admin 或个人使用者）选择工作区，并确认配置与数据访问范围符合设计。发布器保留说明性提醒，工程端与部署端复用同一个 `frontend.workspace` / `frontend.body` 组件。

部署端按所选工作区的节点范围校验请求，密钥和私有指令留在服务端，全局管理接口保持关闭。`NodeDeploymentDefinition` 可作为可选的字段或操作裁剪规则，不再是发布的前置条件。

## 文档型卡片：一次声明，复用原接口（Plugin API 1.36）

开发者仍然只实现原有卡片组件、`NodeDocumentDefinition`，以及
`host.readDocument` / `host.documentAction` / `host.documentDownloadUrl`。
若完整文档、操作响应和下载内容都适合交给使用用户，只需加：

```python
deployment=NodeDeploymentDefinition.from_document()
# 只读版本：NodeDeploymentDefinition.from_document(read_only=True)
```

无需另写 Workspace 组件、HTTP 接口或部署适配器，也不必重复列出文档字段和操作名。
宿主在发布时将现有字段、操作和下载列表固定进版本；只读模式仅选取标记
`read_only` 的操作，编辑模式选取已注册操作。宿主通用的 `replace` 不会自动开放。
搜索、详情等操作保留原有响应结构、revision 校验与持久化。

未声明的卡片默认复用已注册的文档操作、下载、资源业务操作和计算能力。
上面的简写可用于显式收窄页面权限，尤其是 `read_only=True`；包含私有业务字段时可使用下方精细声明。
发布决策归项目拥有者，部署端统一执行工作区范围检查与凭据保护。

该简写同时启用 `shared_sections=True`：原有 `WorkspaceSection` 可直接参与布局组合，
无需逐个重复声明。分区共用整卡业务权限，隐藏分区只改变展示，不隐藏整卡数据。
如需各分区不同权限，继续使用显式 `sections`。

管理员把现有卡片组成 Legion、保存布局，然后点击“发布应用”。发布前检查会一次列出
不兼容的页面，确认后才创建版本；真正发布时仍会重新校验。个人工程模式、已有布局和
正在运行的部署不因这次发布而改变。

## 精细接入

从 `open_agent_world.plugin_api` 导入接口，将声明加入原有 `NodeTypeDefinition`：

```python
from open_agent_world.plugin_api import DeploymentSurface, NodeDeploymentDefinition

# NodeTypeDefinition(..., frontend={"workspace": "notes"}, document=existing_document,
deployment = NodeDeploymentDefinition(
    surface=DeploymentSurface(
        config_fields={"heading"},
        document_fields={"text"},
        document_actions={"save"},
        downloads={"text"},
    ),
)
# ... deployment=deployment)
```

`save` 和 `text` 必须已在原 `NodeDocumentDefinition.actions` / `downloads` 中注册。声明只决定开放范围，实际处理仍调用原 handler，保留 revision 冲突检测与持久化。插件 descriptor 的 `plugin_api_version` 设置为 `"1.21"` 或更高。

原 React 组件通过 `host.deployment` 判断是否在部署中；工程端该值为 `undefined`：

```tsx
const canSave = !host.deployment || host.deployment.document_actions.includes("save");
// 用 canSave 显示原保存按钮；工程配置控件仅在 !host.deployment 时显示。
const snapshot = await host.readDocument();
await host.documentAction("save", { text: "Hello" }, snapshot.revision);
```

完整可运行实现：[Python 声明和处理函数](../examples/deployed-workspace/plugins/demo/oaw_deployment_demo.py)、[共用的 React 页面](../examples/deployed-workspace/plugins/demo/frontend/index.tsx)。启动[部署示例](../examples/deployed-workspace/README.md)后，打开“插件笔记”，保存文本并下载。

## 开放范围

| 声明 | 行为 |
| --- | --- |
| `document="read"` / `"edit"` | 明确公开完整文档业务接口；发布时展开已注册读取或全部操作，以及下载列表 |
| `config_fields` | 只读公开配置字段，例如标题；未声明的连接、凭证等字段不下发 |
| `document_fields` / `summary_fields` | 读取和操作响应只包含这些顶层字段 |
| `document_actions` | 允许调用的已注册文档业务操作；通用文档替换不自动开放 |
| `downloads` | 允许的文档导出器；导出器负责只输出适合公开的内容 |
| `resource_actions={"query": {"rows"}}` | 调用原资源 handler，响应仅保留声明的顶层字段 |
| `execution=True` | 显式开放原节点执行的状态、启动与停止接口，包含其原有执行请求及响应 |
| `file_preview=True` | 文件查看器复用 `host.readFile`；查看器和来源都必须已发布，并具有原有预览连接授权 |

默认所有集合为空、执行关闭。字段筛选针对**顶层字段**；嵌套对象整体公开。不要把含密码、内部节点 ID 或宿主路径的对象作为公共字段。动作参数仍由插件 handler 验证，声明动作意味着允许它的业务效果；不要开放可任意改内部配置或读取路径的通用管理动作。导出内容、执行状态、错误信息也须由插件作者确认适合用户查看。插件本身依旧是宿主信任的代码，部署权限不是恶意插件隔离沙箱。

`host.listCards()` 在部署端只返回已发布节点；跨节点 `readDocument(id)` 也只能访问已开放文档。`updateConfig`、Agent 内部信息、文档转换、任意文件读取及管理接口不在此契约中，服务端不开放。需要这些功能的旧视图应将用户业务整理为原有文档/资源动作，不能仅隐藏设置按钮就认为完成接入。

Agent Graph 和 Jobs 复用原有连接来源接口、会话校验和任务控制；发布它们意味着公开已连接
来源的业务投影，不会公开其配置。成果文件集支持列出、预览和下载已引用的版本，
保留策略管理、全局文件浏览和向其他 Sandbox 复制仍留在工程端。
结构查看器只跟随已发布的 Conversation / Sandbox 文件来源，Sandbox 读取限定 `workspace` 根。
MatCreator 原有研究布局直接使用这些宿主能力，知识图谱使用上述文档简写。

## 原有 WorkspaceSection

通过 `sections={"results": DeploymentSurface(...), ...}` 声明已有的分区 ID，无需定义新布局：

```python
deployment = NodeDeploymentDefinition(
    surface=DeploymentSurface(config_fields={"heading"}),
    sections={
        "results": DeploymentSurface(document_fields={"results"}),
        "input": DeploymentSurface(document_fields={"input"}, document_actions={"submit"}),
    },
)
```

整卡发布合并 `surface` 和当前可见分区权限；单独发布分区只授予该分区权限。隐藏分区不贡献权限，抽出的分区由自身布局位置授予权限。`surface=None` 表示只允许发布已声明分区，不允许整卡发布。多个可见分区的权限按节点合并；分区不是不同用户之间的隔离边界。

部署端不挂载未授权的 `WorkspaceSection` 子组件，避免隐藏控件仍发起请求。放在父组件中的加载逻辑仍需依据 `host.deployment` 限制。整卡公用的读取或操作不要误放进仅某个私有分区应拥有的权限。

## 安装、构建与升级

1. 按[现有插件安装流程](plugins.md#package-structure-and-discovery)安装 Python 包及依赖，声明需要的 `requires_plugins`，在工程端验证原 Workspace。
2. 发布带前端的外部插件时，独立构建卡包 UI，将编译产物放入 [`.oawpack`](pack-distribution.md)，并在发布 Workspace 前安装到工程配置中，无需重新构建宿主前端。仅安装 Python wheel 不会带入浏览器组件。开发 OAW 源码工程本身时，`npm --prefix frontend run build` 也会准备工程内的源码卡包前端。
3. 保存 Legion 布局并按[部署流程](deployment.md)发布、复制到新目录。复制的是数据，交付时同时保留匹配的源码、前端构建、Python 依赖和插件外部资源。
4. 版本与公开权限在发布时固定。插件升级或开放范围改变后，更新插件版本号，在工程端重新验证并发布新副本；不要给正在运行的部署热装插件。启动会拒绝插件版本不匹配。

## 后续阅读

按[部署示例（英文）](../examples/deployed-workspace/README.md)验证接入，或查阅完整的[插件 API（英文）](plugins.md)。
