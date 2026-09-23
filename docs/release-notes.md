## Download / 下载

Expand **Assets** below and choose your installer:

| Computer / 电脑 | File / 文件 |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Mac with Apple Silicon (M1/M2/M3/M4…) | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Mac with Intel processor | `Open-Agent-World-<version>-macos-x86_64.dmg` |

**Source code (zip / tar.gz) is for developers, not installation.**
**普通用户请下载安装包，不要选择 Source code。** `.sha256` files are optional integrity checks.

## Install and start / 安装与开始

1. Windows: run the `.exe`, then open **Open Agent World** from Start. Mac: open the `.dmg`, drag the app to **Applications**, then launch it.
2. In **Settings → Models**, add a model connection and credentials, then select a default model.
3. Follow the canvas tutorial. Open **Pack & Card Library** to collect cards and start building your world.

安装包自带 Python 和应用依赖，无需安装开发环境。首次使用模型需要配置模型服务；部分插件和 Sandbox 可能需要额外下载。

## Preview limitations / 预览版限制

- Windows packages are currently unsigned; Windows may show a publisher/reputation warning.
- macOS packages have ad-hoc signing, without Apple notarization. macOS may block opening them. See the installation guide; these are preview builds. Seatbelt is available locally, while the preferred Container VM requires macOS 26+ on Apple Silicon and Apple Container 0.6.0+.
- Updates are manual: download the new installer. Application data is stored separately from the installed app.

[Installation guide / 安装说明](https://github.com/theAfish/open-agent-world/blob/main/docs/install.md)
