# 入门指南

[English](getting-started.md) | **简体中文**

[文档目录](README.zh-CN.md) / [配置（英文）](configuration.md)

## 环境要求与安装

克隆仓库后，所有命令均在仓库根目录运行。

- Python 3.11 或更新版本、uv，以及 Node.js 20 或更新版本。
- 本地沙盒执行支持 Windows 10/11 或 Linux（包括 WSL2）。
- macOS 可以运行应用，但没有本地沙盒运行时。

Windows PowerShell：

```powershell
./scripts/setup.ps1
./scripts/start.ps1
```

Linux、WSL2 或 macOS：

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

安装脚本会安装后端开发依赖、Google ADK 和 LiteLLM 适配器、前端依赖，并构建生产版前端。`start` 通过 Python 后端提供构建好的页面，并打开本地应用地址。按 Ctrl+C 停止启动器。修改前端后，使用 `npm --prefix frontend run build` 重新构建。

Shell 启动器直接使用已安装的后端环境，只有安装和重新构建时才需要 Node.js 和 uv。支持传递启动参数，例如 `bash scripts/start.sh --mode preview --profile demo` 或 `bash scripts/start.sh --port 38474`。无法自动打开浏览器时，可在同一台机器上手动打开输出的本地地址。使用 WSL2 时，请在 Linux 发行版内安装前置工具并运行这两个脚本。

Windows 桌面安装包自带 Python 运行时，并在独立的 Tauri 窗口中打开应用。安装包构建和开发配置目录详见[桌面安装与开发（英文）](desktop.md)。

应用安装与沙盒环境准备是两个独立步骤。放置卡牌不会自动准备执行环境。沙盒的前置条件和启动方法见[沙盒工作空间（英文）](sandbox-workspace.md)。

## 首次启动

1. 打开启动器输出的地址，进入 **Settings > Models（设置 > 模型）**。添加连接，按需输入 API 密钥，添加服务端模型 ID，选择默认模型并保存。见[模型配置（英文）](configuration.md#models-and-connections)。
2. 打开 **Pack & Card Library（卡包与卡牌库）**，打开已安装的卡包，将收集的卡牌加入当前牌组。全新安装时，卡包尚未打开，牌组为空。
3. 从底部托盘放置 Agent，设置指令和模型，并连接希望它使用的资源。[核心概念（英文）](concepts.md) 介绍了可用的关系类型。也可以跟随[交互教程](tutorial.zh-CN.md) 操作。

## 不配置模型凭据也能体验

需要结果可重复的本地调试时，可以使用 mock 运行时：

```powershell
./scripts/dev.ps1 -AgentRuntime mock
```

也可以使用跨平台启动器：

```bash
bash scripts/dev.sh --agent-runtime core.mock
```

mock 运行时是调试替代品，不是语言模型。真实的智能体回复需要配置模型服务。已有 Agent 如果明确选择了运行时，会保留原来的选择。

## 启动与问题排查

- 缺少依赖：先运行安装脚本，再启动应用。跨平台启动器会检查后端虚拟环境、Vite 和 Node。
- 模型认证或路由失败：检查连接是否启用、凭据来源、服务地址和准确的模型 ID，见[配置（英文）](configuration.md)。
- 底部托盘为空：在[卡牌库（英文）](card-library.md) 收集卡牌并选择牌组内容。
- 沙盒不可用：查看运行时诊断，安装提示的前置依赖，再刷新发现结果。OAW 不会自动改用无隔离的执行方式。
- `dev` 始终使用 `.open-agent-world/development/profiles/default`，独立于正式用户目录和 `OPEN_AGENT_WORLD_DATA_ROOT`。使用 `-Profile tutorial` / `--profile tutorial` 可切换到另一个开发配置目录。F3 支持带备份的选择性重置。
- Windows 的 `scripts/dev.ps1` 会持续等待后端就绪并报告进度，默认没有固定期限。可用 `-StartupTimeoutSeconds 300` 指定期限。跨平台启动器也会等待后端就绪或退出。

## 开发与验证

保持修改范围明确，并运行与受影响行为相匹配的检查。技术栈和职责边界见[架构（英文）](architecture.md)；插件贡献者应先阅读[插件（英文）](plugins.md)。

Windows：

```powershell
./scripts/verify.ps1
```

该命令运行后端测试、一次性原生 Windows 沙盒冒烟测试、前端测试和生产构建。`-SkipNativeSandbox` 会跳过原生冒烟步骤，因此不能据此确认操作系统隔离效果。

Linux / WSL 代码检查：

```bash
uv run --project backend pytest tests backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

原生 Linux HTTP 生命周期验收：

```bash
OAW_TEST_SANDBOX_RUNTIME=linux uv run --project backend pytest backend/tests/test_sandbox_system.py
```

Windows 上运行该测试前，将 `OAW_TEST_SANDBOX_RUNTIME` 设为 `windows` 或 `wsl:<已安装的发行版>`。`backend/tests/test_linux_sandbox.py` 中更广泛的 WSL 检查使用 `OAW_TEST_WSL_DISTRO`。原生测试需要可用的隔离运行时；普通测试或跳过的原生检查不能证明操作系统隔离有效。独立的网络验收见[沙盒网络（英文）](sandbox-networking.md#real-runtime-acceptance)。

开发服务器运行时，可执行 Windows 公共 API 场景：

```powershell
uv run --project backend python -m backend.scripts.http_acceptance_smoke
```

它会创建并清理一个包含四张卡牌的世界，验证资源和 mock Agent 工具，并检查 AppContainer 执行与连线权限撤销。

浏览器交互检查：

```bash
npm --prefix frontend run test:e2e
```

Playwright 使用已安装的 Chrome 和隔离测试服务器。浏览器检查验证 UI 行为，不能证明原生沙盒隔离有效。
