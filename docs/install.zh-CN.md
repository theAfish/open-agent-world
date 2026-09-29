# 下载与安装

[English](install.md) | **简体中文**

打开 [GitHub Releases](https://github.com/theAfish/open-agent-world/releases)，选择一个已发布版本，展开 **Assets**。如果还没有已发布版本，则暂时没有公开安装包。

| 电脑 | 下载文件 |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Apple Silicon Mac | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Intel Mac | `Open-Agent-World-<version>-macos-x86_64.dmg` |

选择安装包；**Source code (zip / tar.gz)** 是源码。Mac 可在“关于本机”查看芯片类型；`.sha256` 文件是可选校验信息。

## Windows

运行安装程序，然后从开始菜单启动 **Open Agent World**。安装包包含 Python、应用依赖和前端，无需自行安装 Node.js、Rust 或下载源码。缺少 WebView2 时，安装期间可能需要下载。

早期构建未签名。如果 Windows 显示信誉警告，先确认文件来自本仓库的 Release，再决定是否继续。受管理设备可能需要管理员协助。

## macOS 预览版

打开 DMG，把 **Open Agent World** 拖入 **Applications** 后启动。当前构建使用临时签名，未经 Apple 公证。
若系统阻止打开，确认下载可信后，在 **系统设置 → 隐私与安全性** 查看是否提供“仍要打开”。不要全局关闭系统安全功能。若提示应用损坏且无法打开，反馈版本和 Mac 架构。

macOS 当前没有本地 Sandbox 运行时。桌面预览版不代表支持依赖该运行时的插件或操作。

## Linux / WSL2

目前通过源码安装，在浏览器中使用。先安装 Git、Python 3.11 或更新版本、uv，以及包含 npm 的 Node.js 20 或更新版本，再运行：

```bash
git clone https://github.com/theAfish/open-agent-world.git
cd open-agent-world
bash scripts/setup.sh
bash scripts/start.sh
```

安装脚本会安装应用依赖并构建前端。后续启动只需 `bash scripts/start.sh`，按 Ctrl+C 停止。
WSL2 用户需要在 Linux 发行版中安装工具并运行命令；本地 Sandbox 还需要
[隔离运行环境（英文）](sandbox-workspace.md)中的前提。源码开发见[从源码运行](getting-started.zh-CN.md)。

## 第一次使用

1. 在 **Settings → Models** 添加连接、凭据和模型，并选择默认模型。
2. 跟随画布首次使用教程，或通过 **帮助（?）→ 教程** 重放。
3. 打开 **Pack & Card Library** 收集卡片、加入当前牌组，再放到画布上。

继续阅读[中文使用入门](user-guide/index.zh-CN.md)。配置模型前也可以浏览画布；真实模型调用依赖你配置的服务，部分插件和 Sandbox 环境首次使用时需要下载依赖。

## 更新与排错

未配置自动更新的版本需要从 Releases 手动下载安装新版本。用户数据独立保存，升级和卸载会保留数据。
常见问题见[排错指南（英文）](user-guide/troubleshooting.md)。

反馈问题时提供操作系统、CPU 架构、发布版本和错误信息，分享日志前移除凭据。
