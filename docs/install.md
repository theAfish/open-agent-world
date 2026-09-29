# Download and install

**English** | [简体中文](install.zh-CN.md)

Open [GitHub Releases](https://github.com/theAfish/open-agent-world/releases), select a published version, and expand **Assets**. If there are no published releases yet, installers are not publicly available yet.

| Your computer | Download |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Apple Silicon Mac | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Intel Mac | `Open-Agent-World-<version>-macos-x86_64.dmg` |

Choose an installer above, rather than **Source code (zip / tar.gz)**. On a Mac, check **About This Mac** for your chip type. A `.sha256` file is optional checksum information, not an installer.

## Windows

Run the installer, then launch **Open Agent World** from the Start menu. Python, application dependencies and the frontend are included; Node.js, Rust and a source checkout are unnecessary. WebView2 may download during installation if missing.

The initial builds are unsigned. If Windows shows a reputation warning, check that the file came from this repository's Release before deciding whether to continue. Managed devices may require administrator assistance.

## macOS preview

Open the DMG and drag **Open Agent World** into **Applications**, then launch it. Builds currently use ad-hoc signing and are not notarized by Apple. If macOS blocks opening, review **System Settings → Privacy & Security** for the blocked app and an **Open Anyway** option, if available and you trust this download. Do not disable system security globally. Report a damaged-app error with the release version and Mac architecture if it cannot be opened.

macOS 13+ can use the built-in **Seatbelt** Sandbox. On Apple Silicon with macOS 26+, install Apple `container` 0.6.0+ and run `container system start` to enable the preferred VM Sandbox. Scientific plugin dependencies are prepared on first use and may require a download. See [Sandbox workspace](sandbox-workspace.md) for the security and resource-limit differences.

## Linux / WSL2

Linux currently uses the source installation and opens the application in your browser. Install Git, Python 3.11 or newer, uv, and Node.js 20 or newer (including npm), then run:

```bash
git clone https://github.com/theAfish/open-agent-world.git
cd open-agent-world
bash scripts/setup.sh
bash scripts/start.sh
```

Setup installs application dependencies and builds the frontend. Later launches only need `bash scripts/start.sh`; stop with Ctrl+C. For WSL2, run these commands inside your Linux distribution. Local Sandbox execution also requires the isolation prerequisites described in [Sandbox workspace](sandbox-workspace.md).

## First use

1. In **Settings → Models**, add your connection, credentials, and model; choose a default.
2. Follow the first-use tutorial on the canvas, or replay it from **Help (?) → Tutorial**.
3. Open **Pack & Card Library**, collect cards, add them to your active deck, then place them on the canvas.

Continue with [Your first team](user-guide/first-team.md).

You can explore the canvas before configuring a model. Model calls require your configured service; some plugins and Sandbox environments download dependencies on first use.

## Updates and troubleshooting

Builds without automatic updates require downloading and installing the next
release manually. User data is stored separately and retained across upgrades and
uninstall. See [Troubleshooting](user-guide/troubleshooting.md) for common problems.

When reporting a problem, include your OS, CPU architecture, release version and the error shown. Remove credentials from any logs you share.
