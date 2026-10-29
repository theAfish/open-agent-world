# 安装 Open Agent World

## 下载

展开发行页的 **Assets**，选择该发行版实际提供、适合你电脑的安装包：

| 电脑 | 文件 |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Apple Silicon Mac | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Intel Mac | `Open-Agent-World-<version>-macos-x86_64.dmg` |

**Source code (zip / tar.gz) 是开发者使用的源码。** 运行应用请选择安装包。`.sha256` 文件用于校验下载完整性。

## 安装与启动

1. Windows：运行 `.exe`，从开始菜单打开 **Open Agent World**。Mac：打开 `.dmg`，把应用拖入 **Applications**，然后启动。
2. 使用 **Connect AI（连接 AI）**，或在 **Settings → Models（设置 → 模型）** 添加连接并选择默认模型。
3. 选择 **Start Tutorial（开始教程）**，或按照[第一个任务](https://github.com/theAfish/open-agent-world/blob/dev/docs/user-guide/first-team.md)操作。打开 **Pack & Card Library（卡包与卡牌库）** 收集、放置卡片。

安装包包含 Python 和应用依赖。调用模型需要配置服务；部分卡包和 Sandbox 环境首次使用时还需要下载依赖。

## 平台与更新

签名、公证和自动更新取决于发行构建。遇到系统警告时，先阅读该发行版说明，核对发布者与下载来源。可以安装新版安装包进行手动更新；应用数据与安装目录分开保存。

源码支持 macOS 本地 Seatbelt 和可选 Container VM。后者需要 Apple Silicon、macOS 26+ 与 Apple Container 0.6.0+。使用 Sandbox 前，应核对该发行版实际包含和验证的运行时。

链接文档跟随 **dev**。要确认某个正式版本的行为，请阅读对应 tag 的文档或该安装包内置帮助。

[安装指南](https://github.com/theAfish/open-agent-world/blob/dev/docs/install.md)
