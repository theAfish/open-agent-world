# Install Open Agent World

**English** | [简体中文](https://github.com/theAfish/open-agent-world/blob/dev/docs/release-notes.zh-CN.md)

## Download

Expand **Assets** below and choose your installer:

| Computer | File |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Mac with Apple Silicon | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Mac with Intel processor | `Open-Agent-World-<version>-macos-x86_64.dmg` |

**Source code (zip / tar.gz) is for developers, not installation.**
The `.sha256` files are optional integrity checks.

## Install and start

1. Windows: run the `.exe`, then open **Open Agent World** from Start. Mac: open the `.dmg`, drag the app to **Applications**, then launch it.
2. In **Settings → Models**, add a model connection and credentials, then select a default model.
3. Follow the canvas tutorial. Open **Pack & Card Library** to collect cards and start building your world.

Installers include Python and application dependencies. Model calls require a configured
service; some plugins and Sandbox environments download additional dependencies.

## Preview limitations

- Windows packages are currently unsigned; Windows may show a publisher/reputation warning.
- macOS packages have ad-hoc signing, without Apple notarization. macOS may block opening them. See the installation guide; these are preview builds. Seatbelt is available locally, while the preferred Container VM requires macOS 26+ on Apple Silicon and Apple Container 0.6.0+.
- Updates are manual: download the new installer. Application data is stored separately from the installed app.

[Installation guide](https://github.com/theAfish/open-agent-world/blob/dev/docs/install.md)
