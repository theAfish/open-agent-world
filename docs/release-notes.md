# Open Agent World v0.7.1

## What's new since v0.6.0

- Redesigned Pack & Card Library with shared artwork, grouped cards, animated Pack previews, and clearer installation and restart feedback.
- Workspace switching and publishing, scoped Sandbox sessions, and administrator-managed Pack installation for hosted workspaces.
- CLI Harness execution providers with declared environments, resource validation, and reuse of OAW model connections.
- New installable Office Skills and Time & Count Packs; improvements to Knowledge Base schema editing and scientific workflows in AtomSculptor and XRD.
- Conversation attachments up to 512 MiB, streamed files in scoped execution workspaces, visible upload errors, and delivered files that remain available after an Agent completes.
- More reliable interrupted-task recovery, hosted upgrades, managed scientific runtimes, and backend reconnection.
- Keep the shared PDF engine on PyMuPDF 1.x, including prebuilt Intel macOS wheels.

[Full changelog](https://github.com/theAfish/open-agent-world/compare/v0.6.0...v0.7.1)

## Install Open Agent World

## Download

Expand **Assets** on the release page and choose an installer provided for your computer:

| Computer | File |
| --- | --- |
| Windows x64 | `Open-Agent-World-<version>-windows-x64.exe` |
| Mac with Apple Silicon | `Open-Agent-World-<version>-macos-arm64.dmg` |
| Mac with Intel processor | `Open-Agent-World-<version>-macos-x86_64.dmg` |

**Source code (zip / tar.gz) is for developers.** Use an installer to run the app.
The `.sha256` files let you check download integrity.

## Install and start

1. Windows: run the `.exe`, then open **Open Agent World** from Start. Mac: open the `.dmg`, drag the app to **Applications**, then launch it.
2. Connect a model through **Connect AI**, or add a connection in **Settings → Models** and select a default model.
3. Choose **Start Tutorial**, or follow [your first task](https://github.com/theAfish/open-agent-world/blob/dev/docs/user-guide/first-team.md). Open **Pack & Card Library** to collect and place cards.

Installers include Python and application dependencies. Model calls need a configured
service; some Packs and Sandbox environments download additional dependencies.

## Platform and update notes

Signing, notarization and updater availability depend on the release build. Read
that release's notes before responding to an operating-system warning. Verify the
publisher and download source. You can update manually by installing a newer
release; application data is stored separately from the installed app.

The source supports local macOS Seatbelt and an optional Container VM. The latter
requires macOS 26+ on Apple Silicon and Apple Container 0.6.0+. Check which runtimes
are included and verified in your release before using a Sandbox.

The linked documentation follows **dev**. For version-specific behavior, read the
docs at the release tag or the help bundled with the installed app.

[Installation guide](https://github.com/theAfish/open-agent-world/blob/dev/docs/install.md)
