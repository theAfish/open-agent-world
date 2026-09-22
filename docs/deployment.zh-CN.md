# 发布与部署锁定应用

想先体验成品？运行 `python examples/deployed-workspace/run.py`，浏览器会打开无需 API Key 的本地部署示例。密码为 `oaw-demo-2026`，详见[示例说明](../examples/deployed-workspace/README.md)。

[文档目录](README.zh-CN.md) · [English](deployment.md)

工程师在原工作区配置模型连接、Agent、工具和 Legion 布局，然后发布应用。部署程序生成独立的数据副本，并启动只提供用户功能的服务。用户登录后直接进入固定布局；画布、连线、模型连接、插件设置和管理 API 均不向用户开放。

原工作区仍然是工程配置端。修改它不会改变已部署的数据副本。

## 最便捷的本机部署

首次从源码使用时，在项目根目录完成安装：

```powershell
./scripts/setup.ps1
./scripts/start.ps1
```

Linux / WSL2 使用 `bash scripts/setup.sh` 和 `python3 scripts/start.py`。前端修改后运行 `npm --prefix frontend run build`。部署运行时只需要已经安装好的 Python 环境与前端构建产物，不需要启动 Vite。

1. 配置模型连接、Agent、工具与资源，确认普通工作区中可以正常运行。
2. 打开 Legion 的 **Workspace mode / 工作区模式**，布置用户需要的页面并保存布局。移出布局的卡片仍在后台运行，但不会出现在用户页面的底部栏。
3. 点击 **发布应用**，填写应用名称。如果用户需要直接执行终端命令，勾选“允许用户执行 Sandbox 终端命令”。已有终端分区却未开放权限时，发布检查会提示修改。
4. 点击 **创建发布版本**，复制生成的命令。先结束任务、停止 Sandbox，然后关闭工程配置程序；开发模式还需要停止启动它的终端进程。
5. 在项目根目录运行复制的命令，例如：

```powershell
python scripts/deploy.py --source 'C:\Users\YourName\AppData\Local\OpenAgentWorld' --release RELEASE_ID --open
```

请使用发布面板生成的真实目录与版本 ID。开发 profile、重定位后的存储目录和正式应用目录可能不同。Linux / macOS 使用 `python3`。

程序会：

- 检查工程配置自发布以来是否发生改变；有变化时要求重新发布。
- 对源目录加锁、检查数据库，并复制和校验应用数据。
- 将副本放在源目录旁的 `<源目录名>.deployments/<版本 ID>`，可用 `--output` 指定新的独立目录。
- 启动 `http://127.0.0.1:38474`，显示访问密码，并用 `--open` 打开浏览器。

**保存终端显示的访问密码。** 它只在创建时显示，部署文件中保存带随机盐的密码摘要。也可以加 `--ask-password`，在终端私密输入至少 12 个字符的自定义密码。密码不需要放进 URL、源码或命令行参数。

默认端口被占用时程序会报错，使用 `--port 38475` 选择端口。Ctrl+C 正常停止运行服务。

## 重启、更新与回滚

重启已部署应用：

```powershell
python scripts/deploy.py --serve 'D:\OAW-Deployments\customer-v1' --open
```

这里填写创建部署时打印的 **Deployment data** 目录。`--serve` 使用现有数据，不重新复制；聊天记录、上传的附件、任务和工作文件会保留。重启后用户需要重新登录。

忘记或需要更换密码时，先停止运行服务，再运行 `python scripts/deploy.py --serve '部署目录' --reset-password --ask-password`。不加 `--ask-password` 时会生成并显示新密码。加 `--prepare-only` 可只更新密码、不启动服务。

更新时，在原工程工作区修改配置、验证、创建新的发布版本，再部署到一个**新目录**。先在另一个端口验证新版本，再停止旧服务并切换访问入口。既有发布记录只是部署配方，实际快照在部署命令执行时生成；源配置变化后不能用旧配方重新制作旧快照。

回滚时，停止新服务，用 `--serve` 启动保留的旧部署目录。各版本的数据独立：切回旧版本不会自动合并新版产生的会话、文件或任务，也不会撤销外部系统中的操作。需要保留数据时先安排导出/迁移，避免在两个版本上同时写入。

运行服务期间可以重新打开原工程端继续配置。不要在同一个数据目录上启动第二个后端。

## 部署到服务器供浏览器使用

建议先在目标机器、目标服务账号下完成安装和工程配置，再生成部署副本。默认只监听回环地址；外部访问通过 HTTPS 反向代理转发到运行服务。

Linux 示例：

```bash
python3 scripts/deploy.py \
  --source /srv/oaw/engineering \
  --release RELEASE_ID \
  --output /srv/oaw/releases/customer-v1 \
  --secure-cookie --prepare-only

python3 scripts/deploy.py --serve /srv/oaw/releases/customer-v1
```

`--prepare-only` 只创建副本，便于交给系统服务管理。`--secure-cookie` 适用于 HTTPS 入口：浏览器不会通过普通 HTTP 发送登录 Cookie，请通过正式 HTTPS 域名访问。

Caddy 配置示例，替换域名并将 DNS 指向服务器：

```caddyfile
assistant.example.com {
    reverse_proxy 127.0.0.1:38474
}
```

只代理新运行服务的端口。运行服务使用自己的用户登录，不需要也不接受工程端的 `OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN` 来开放管理功能。不要将旧工程服务作为用户入口。

长期运行可使用 systemd。仓库提供 [服务文件示例](../deploy/oaw-runtime.service)，修改账号、仓库路径和部署目录后安装：

```bash
sudo cp deploy/oaw-runtime.service /etc/systemd/system/oaw-runtime.service
sudo systemctl daemon-reload
sudo systemctl enable --now oaw-runtime
journalctl -u oaw-runtime -f
```

示例中的 Python 环境、工作目录和数据目录必须属于运行账号。若模型使用后端环境变量认证，应在服务环境中提供相应变量；工程端启动终端里的变量不会自动出现在 systemd 服务中。

如果应用需要 Linux Sandbox，还要为运行账号准备 systemd user manager 与 cgroup-v2 内存/进程限制，参阅 [Sandbox 前提](sandbox-workspace.md)。长期无登录会话运行时通常需要管理员为该账号启用 lingering（例如 `sudo loginctl enable-linger oaw`），并实际验证 Sandbox 可以启动。

也提供 `--host 0.0.0.0` 用于受控网络或已有网关的环境。外部访问应使用 HTTPS。这里采用原生服务部署，保留现有 Windows AppContainer / Linux Bubblewrap、cgroup 等 Sandbox 机制；未将通用 Docker 容器视为这些执行隔离条件的替代品。

## 支持的用户页面

| 原工作区页面 | 发布后的能力 |
| --- | --- |
| Conversation 整卡与 Sessions / Conversation / Participants 分区 | 会话选择、新会话、消息、附件、参与者名称与运行状态；模型与指令不下发 |
| Sandbox 整卡与 Files / File preview 分区 | 浏览工作目录、预览文本/图片、下载；资源挂载列表与宿主路径不下发 |
| Sandbox Terminal 分区 | 发布时明确开启后执行命令，使用已有 Sandbox 隔离与配置 |
| Text / Image | 阅读文本、显示图片 |
| Task Board（`oaw.tasks`） | 查看/添加任务、更新状态、按已配置执行器启动/停止任务 |
| Agent 整卡 | 仅显示运行状态，不显示指令、模型、连线和配置 |

部署页面直接复用 Legion Workspace、WorkspaceSection 和现有的 Conversation / Sandbox / Task Board / Text / Image 组件。分栏比例与布局固定，标签可切换，切换标签保留草稿；主题和语言也沿用原控件。只移除布局编辑、返回画布、设置、未放置卡片入口与组件内的工程配置。文本沿用原文本组件，以只读方式显示。

第三方与新增插件可以通过 `NodeDeploymentDefinition` 声明公开字段、业务操作和原有 WorkspaceSection，部署时复用原组件和处理函数。未声明的自定义页面（包括尚未适配的 MatCreator 页面）会拒绝发布，但仍可作为后台依赖运行。详见[插件部署接入](plugin-deployment.md)，其中包含可保存、下载的完整示例。部署端通过 `/api/runtime-app/workspace` 调用原有业务处理函数，不挂载完整工程 API。

## 数据、凭证与执行边界

- 一个部署是一个共享应用，持密码的用户共享它的会话、任务和文件。当前不提供逐用户数据隔离、SSO 或租户管理。
- 为保留跨 Legion 的依赖，副本包含**整个源 profile** 的数据库、应用自有文件、已有历史和加密凭证，而不只是可见卡片。只有选定页面的数据会通过用户 API 下发。部署目录属于受信任的服务器数据，不是可以公开分发的静态网站目录。
- Windows 凭证加密绑定原账号。最便捷的方案是在同一机器、同一账号下生成与运行副本。跨机器、跨账号或跨操作系统交付需要重新配置凭证、插件、工具与工作目录，不能直接当作可移植安装包。
- 外部 Sandbox / 项目目录不属于应用自有数据。默认检测到已知外部工作目录时拒绝复制；确定要继续访问这些真实目录时，加 `--allow-external-workspaces`。这些目录不会随版本复制或回滚，源工程和部署可能读写同一份文件。插件自定义外部依赖也需要工程师逐项确认。
- Windows Sandbox 的副本会获得基于新目录的隔离身份，并移除副本内原隔离身份的权限。Linux Sandbox 必须已结束本机服务。部署不会通过放宽现有隔离策略来恢复执行。
- 后端不挂载画布、管理设置、插件管理或全局 WebSocket 接口；用户组件仅轮询公开业务数据。服务层同时锁定节点配置和连线，后台 Agent 也不能借画布工具修改已发布结构。普通会话、文档、任务与文件数据仍可按已授权能力更新。
- 保持运行代码、前端构建和插件与发布版本匹配。启动会检查插件版本。部署目录是数据快照，不含整个 Python/Node 依赖环境或源码；升级源码时应保留旧 checkout/构建以便完整回滚。

## 排错与备份

| 提示或现象 | 处理 |
| --- | --- |
| 数据目录正在使用 | 停止占用该 profile 的后端；仅关闭浏览器标签不会停止源码服务 |
| 发布后配置已改变 | 回工程端重新发布，再复制新命令 |
| 已存在目标目录 | 重启用 `--serve`；制作新版本用新的 `--output`，程序不会覆盖或合并 |
| 不支持的公开页面 | 从发布布局中移除该页面，保留后台卡片，或实现对应的用户页面适配 |
| 外部工作目录 | 使用部署专属目录，或明确接受共享后加 `--allow-external-workspaces` |
| 模型 / Sandbox 不可用 | 查看部署目录 `logs/launcher.log`；确认服务账号、凭证环境、运行时与网络前提 |
| 插件版本发生变化 | 使用原版本代码启动，或在新版本中重新验证和发布 |
| 登录后仍要求登录 | HTTPS 部署使用 HTTPS 地址；确认代理保留 Host，且浏览器允许同站 Cookie |

复制失败只保留名称带 `.partial-...` 的未完成目录，不将其自动投入运行。排除问题后可使用新的目标目录重试；人工确认后再清理未完成副本。

备份前停止运行服务，然后备份整个部署目录、运行代码版本，以及引用的外部数据。加密凭证需要一起保留对应密钥与原账号条件。不要只备份 SQLite 主文件而遗漏文件、附件或密钥。

## 开发验证

```bash
python -m pytest backend/tests/test_deployments.py backend/tests/test_control_plane.py
npm --prefix frontend run build
npm --prefix frontend run test:e2e:deployment
```

浏览器验证会启动独立的 mock Agent 部署，检查登录、对话、任务、标签草稿、移动布局和管理接口拒绝。它不替代原生 Sandbox 隔离验收。

Windows 浏览器测试默认使用已安装的 Edge；可通过 `PLAYWRIGHT_CHANNEL` 选择其它浏览器。其它系统默认使用 Playwright Chromium，需要先安装相应浏览器。Windows 原生副本隔离验收可设置 `OAW_DEPLOY_NATIVE=1` 后运行 `backend/tests/test_deployments.py` 中的 native 测试。
