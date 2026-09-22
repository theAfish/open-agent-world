# 插件部署接入 / Plugin deployment

插件作为后台依赖时，部署副本保留其节点、连接和配置，继续使用原运行时。要让用户直接使用第三方或新增插件的页面，插件需通过 **Plugin API 1.21** 声明 `NodeDeploymentDefinition`。发布器不按插件名称维护白名单，也不另建 UI：工程端与部署端加载同一个 `frontend.workspace` / `frontend.body` 组件。

旧插件没有声明时，不会自动公开其自定义页面；发布会给出错误，它仍可以留在后台运行。

## 最小接入

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
| `config_fields` | 只读公开配置字段，例如标题；未声明的连接、凭证等字段不下发 |
| `document_fields` / `summary_fields` | 读取和操作响应只包含这些顶层字段 |
| `document_actions` | 允许调用的已注册文档业务操作；通用文档替换不自动开放 |
| `downloads` | 允许的文档导出器；导出器负责只输出适合公开的内容 |
| `resource_actions={"query": {"rows"}}` | 调用原资源 handler，响应仅保留声明的顶层字段 |
| `execution=True` | 显式开放原节点执行的状态、启动与停止接口，包含其原有执行请求及响应 |

默认所有集合为空、执行关闭。字段筛选针对**顶层字段**；嵌套对象整体公开。不要把含密码、内部节点 ID 或宿主路径的对象作为公共字段。动作参数仍由插件 handler 验证，声明动作意味着允许它的业务效果；不要开放可任意改内部配置或读取路径的通用管理动作。导出内容、执行状态、错误信息也须由插件作者确认适合用户查看。插件本身依旧是宿主信任的代码，部署权限不是恶意插件隔离沙箱。

`host.listCards()` 在部署端只返回已发布节点；跨节点 `readDocument(id)` 也只能访问已开放文档。`updateConfig`、Agent 内部信息、文档转换、任意文件读取及管理接口不在此契约中，服务端不开放。需要这些功能的旧视图应将用户业务整理为原有文档/资源动作，不能仅隐藏设置按钮就认为完成接入。

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
2. 有前端的外部插件将源码置于 `plugins/<package>/frontend/`，保留 `plugin.json` 与 `index.tsx`。执行 `npm --prefix frontend run build`。仅安装 Python wheel 不会自动带入浏览器组件；新增前端插件需要重新构建。示例目录的前端也由构建器发现，普通工程未加载示例后端时不会出现示例卡片。
3. 保存 Legion 布局并按[部署流程](deployment.zh-CN.md)发布、复制到新目录。复制的是数据，交付时同时保留匹配的源码、前端构建、Python 依赖和插件外部资源。
4. 版本与公开权限在发布时固定。插件升级或开放范围改变后，更新插件版本号，在工程端重新验证并发布新副本；不要给正在运行的部署热装插件。启动会拒绝插件版本不匹配。

## English summary

Backend-only plugins remain operational in the copied profile. Custom public views opt in through `NodeTypeDefinition.deployment` (Plugin API 1.21), using `DeploymentSurface` for field projections and allowed document actions, exports, resource actions and optional execution. Existing React views and handlers are reused. The example above is executable in the deployed-workspace demo.

`host.deployment` is available only in deployment mode. Hide engineering controls in the original view and use the scoped host methods. Unpublished nodes, config updates and management APIs remain unavailable. Public field projections are top-level, and plugin authors remain responsible for handler arguments, nested content, exports, execution details and errors. This contract does not sandbox untrusted plugin code.

Sections reuse existing `WorkspaceSection` IDs; only visible, declared sections grant access. Install frontend source under `plugins/<package>/frontend`, rebuild, and publish a new deployment when adding or upgrading plugins. A Python-only installation does not install frontend assets. Preserve matching plugin versions and dependencies with each release.
