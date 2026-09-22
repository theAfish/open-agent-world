# 可直接体验的部署示例

一个无需 API Key、Docker 或 Sandbox 的本地应用：左侧对话，右侧使用指南、交付清单和插件笔记。后台使用预设回复助手，运行真实的发布、复制、密码登录和结构锁定流程。

页面直接使用原 Legion Workspace 的分栏、标签、对话与文本组件。工程编辑和设置入口被关闭，文档以原文本组件的只读形式呈现。

## 启动

在项目根目录执行（已完成项目安装）：

```powershell
python examples/deployed-workspace/run.py
```

启动后自动打开 <http://127.0.0.1:38475>，体验密码为 **`oaw-demo-2026`**。Linux/macOS 可将 `python` 换成 `python3`。脚本自动使用项目的 `backend/.venv`。

首次使用项目需先运行 Windows 的 `./scripts/setup.ps1` 或 Linux/macOS 的 `bash scripts/setup.sh`。如果尚无前端构建，执行 `npm --prefix frontend run build`。

登录后新建对话并发送任意消息，即可收到预设回复。切换文档标签、刷新页面或重新登录，体验已锁定的布局和保留的聊天记录。该助手不调用模型、不执行工具，每次回复相同。

打开“插件笔记”可编辑、保存并下载文本。这是通过通用插件部署契约接入的自定义节点，工程端和部署端使用同一 React 组件；内部连接字段和私有文档字段不向用户公开。插件作者可参考[接入文档](../../docs/plugin-deployment.md)。

如果已生成旧版示例，使用新的 `--data-root` 体验新插件，例如 `python examples/deployed-workspace/run.py --data-root .open-agent-world/examples/plugins-demo --port 38476`。插件已升级到 0.2.0，旧副本应配合原版本代码运行，脚本不会覆盖旧数据。

## 停止与再次体验

在启动终端按 **Ctrl+C** 停止。再次执行相同命令会使用原部署数据，保留聊天记录。默认仅监听本机回环地址。

```powershell
# 自选端口，不自动打开浏览器
python examples/deployed-workspace/run.py --port 38476 --no-open

# 创建另一份全新示例，不覆盖旧数据
python examples/deployed-workspace/run.py --data-root .open-agent-world/examples/demo-2 --port 38476

# 仅生成部署副本
python examples/deployed-workspace/run.py --prepare-only
```

默认数据位于 `.open-agent-world/examples/deployed-workspace/`（Git 已忽略）：

- `engineering/`：自动生成的工程配置源，包含助手连接和发布记录。
- `runtime/`：独立的锁定部署副本，包含登录信息与运行时聊天记录。

启动过程只在内存中调用工程 API 创建示例，不对外启动工程管理服务。生成后使用标准 `backend.deploy` 启动器提供已构建的前端。已有但未完整生成的目录不会被覆盖；遇到中断可选择一个新的 `--data-root`。

## 文件与实现

- `run.py`：配置示例、发布、生成部署副本并启动。
- `guide.md`、`checklist.md`：发布进应用的文档内容。
- `plugins/demo/`：仅本示例加载的预设回复运行时、插件笔记声明与共用前端组件。

文件内容在首次生成时写入工程数据；修改模板后使用新的 `--data-root` 体验新版。示例依赖当前项目和这里的插件源码，请保留它们，不要只搬走运行时数据库。

示例密码是公开的，仅用于本机体验。正式交付请按[中文部署文档](../../docs/deployment.zh-CN.md)配置真实模型并重新发布，使用独立生成的密码。应用内业务数据由访问者共享；本示例不包含多租户隔离。
