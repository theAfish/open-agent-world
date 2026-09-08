# Sandbox configuration and window workspace

The compact card and inspector report runtime, readiness, folder, access and network policy, with Start/Stop and shortcuts to the window's Workspace and Settings tabs. Workspace shows Files on the left, a file preview at upper right and the terminal at lower right. Both dividers support pointer dragging and arrow keys. History is a tab inside the terminal; configuration, environment variables, presets, Skill resources, diagnostics and recovery live in Settings. Switching tabs preserves the selected file and unsaved inputs. Opening, closing or reopening a window never submits a command or changes runtime lifecycle.

New, copied and summoned Sandboxes start stopped. Managed storage is prepared through the existing Start lifecycle. The selected runtime remains pinned after first start. Runtime, workspace, network and resource-limit changes require Stop → Save → Start. Ordinary environment changes apply to the next command without a restart.

## Configuration and authority

Sandbox-local variables use the existing `EnvironmentProfile` document model, editor and private credential bindings, scoped directly to the Sandbox node. No hidden Environment cards are created. Add ordinary values or secret references, save, then bind secrets under Settings → Environment variables.

An `environment.default` connection points from an Environment Profile to a Sandbox. At most one default is allowed. It remains a live reference. The resolution order is:

1. Use the linked profile as the base, if present.
2. An explicitly selected invocation profile replaces that base.
3. Sandbox-local variables override the chosen base, case-insensitively.
4. Host-owned environment, startup and isolation controls remain protected by the existing validator.

The effective-configuration view identifies each variable's source. Secrets show binding status only. A linked profile is shared with executions authorized for that Sandbox, even when an Agent has no separate `environment.use` connection. An Agent's private Environment equipment cannot be attached as shared defaults. Explicit invocation selectors still require that Agent's own live grants.

Manual requests use the trusted local API boundary. Agent `execute_command`, `run_skill_script` and `copy_skill_resource` use current capabilities. Execution authority does not grant configuration or credential-binding authority. Manual execution never fabricates an Agent identity.

Commands capture current documents, configuration and secret values at admission. Profile edits, disconnection and credential changes affect subsequent commands. Resolved secrets are injected into the isolated process and descendants, never `os.environ` or transport-helper environments. They are excluded from receipts, node documents and templates. Stream fragments containing secrets are suppressed until the complete bounded result can be redacted. Executed code receiving a secret **can read it**; output masking does not protect a credential from that code.

## Files and Skills

`GET /api/sandboxes/{id}/files` enumerates logical roots. `operation=list|preview|download`, `root` and a forward-slash relative `path` address only those roots:

| Root | Authority |
| --- | --- |
| `workspace` | The configured external folder or managed workspace; configured read/write access |
| `resource:<node-id>` | A currently attached resource, with its mount access mode |
| Skill bundle | Inspected separately from its authorized node document; not a general filesystem root |

The workspace root expands automatically; nested directory expansion is lazy. Refresh reloads visible directories while retaining expansion and selection. Starting the Sandbox, completing a command or changing its folder also refreshes files. Responses contain at most 300 entries, with truncation reported. Previews are limited to 1 MiB and downloads/copies to 16 MiB. UTF-8 text and PNG/JPEG/GIF previews are supported; other content has an unsupported state. Loading, empty, denied, missing and oversized states are explicit. The file tree is never continuously polled. Attached resources can open their existing resource editor.

POSIX operations walk no-follow directory descriptors. Windows operations hold non-delete-sharing handles to path components and reject reparse points, junctions, device/ADS paths and ambiguous names. While an external workspace is running, browsing respects the existing backend workspace pin. Ordinary workspace hardlinks are rejected; explicitly attached resources retain their established hardlink contract. Backend locks and pins outlive cancelled filesystem-worker requests. WSL filesystem workers read the runtime's effective Linux roots and never reclaim another worker's live cgroup.

The Skill panel distinguishes the selected document revision, available files, cached materialization, whether the cache matches the current revision and an active execution. Inspection never mounts or materializes a bundle. Cached content is not authority. Each execution retains the existing lazy, read-only, command-scoped mount outside the workspace; a different Agent cannot reuse another Agent's cached Skill path.

**Copy into workspace** and the Agent `copy_skill_resource` tool share the same scoped write operation. Both require current access, a safe destination in an existing writable directory and explicit overwrite. Copying makes those bytes workspace content shared under the Sandbox's existing workspace authority. Bundle availability does not imply an installed interpreter or package; use diagnostics and supply an appropriate interpreter when executing a Skill.

## Networking and diagnostics

| Backend | Modes | Enforcement |
| --- | --- | --- |
| Windows AppContainer | Disabled (default), Enabled | Outbound Internet capability plus fixed per-profile WFP destination blocks; requires the narrow elevated broker, with no private/server capability or loopback exemption |
| Linux / WSL2 | Disabled (default), Enabled | slirp4netns in an isolated network namespace with public IPv4 egress enforcement; retained seccomp, host/alias denial and cgroup cleanup |

Enable public outbound networking using **Stop → Save → Start**. Missing networking components are reported separately from offline runtime availability. Mount isolation, dropped capabilities, resource limits and deadlines remain enforced. No privileged host socket is mounted. The execution contract remains arbitrary executable/argv, independent of network protocol. See [network policies, prerequisites, lifecycle and real acceptance results](sandbox-networking.md).

**Check execution environment** runs through the selected Sandbox and reports working directory, configured access, configuration readiness, common tool availability/versions and network policy. **Test connectivity** probes a user-entered HTTP(S) destination with `curl` through that same Sandbox policy, without URL credentials or disabling certificate verification. It distinguishes setup failure, DNS failure, TLS verification failure, connection failure, HTTP authentication refusal and missing tools. Offline mode reports disabled without making a request. Diagnostics never install packages or modify system settings.

## Console and recovery

The terminal uses multiline input and closed stdin, not a PTY. Run submits the current command; Ctrl/Cmd+Enter is its keyboard shortcut. Separate commands do not retain `cd`, `export` or shell-session state. Explicit interactive requests such as `read`, `set /p`, terminal editors and `ssh -tt` are rejected; other commands that require prompts may fail on EOF or reach the configured timeout. Full interactive-session detection is not possible for arbitrary programs.

Named presets load ordinary command text and use the same execution path. They never execute on opening a card/window. Do not put secret values into command text or presets. Use environment references.

One command occupies a Sandbox at a time. The window and inspect tool show its caller; competing submissions return a busy error. **Cancel command** terminates its process tree and leaves the Sandbox ready. **Stop Sandbox** terminates execution and stops/revokes the runtime's workspace access. **Reset runtime cache** requires a stopped Sandbox and removes host-managed Skill materializations, preserving workspace outputs and external folders.

The host retains the latest 20 command receipts, with at most 64 KiB per output stream, exit code, duration, caller and terminal status. Live backend output remains bounded by the existing 2 MiB limit. Drafts and sidebar width use the existing surface store. Receipts belong to the live node identity and are not portable state. Copies/templates carry requirements, never credential bindings or past executions. A disconnected manual HTTP request does not stop its admitted command; explicit cancellation remains separate. Backend restart recovery marks unrecoverable running receipts interrupted, without resubmission.

## Validation performed on 2026-09-07

This section records the earlier workspace update, before enabled networking was implemented. Current networking acceptance and remaining platform verification gaps are in [Sandbox networking](sandbox-networking.md#real-runtime-acceptance).

All commands ran from the repository root. No packages or runtime dependencies were installed.

The final combined backend suite passed **408 tests, with no skips**:

```powershell
$env:OAW_TEST_SANDBOX_RUNTIME='windows'
$env:OAW_TEST_WSL_DISTRO='Ubuntu-24.04'
$env:OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS='1'
backend/.venv/Scripts/python -m pytest backend/tests tests -o addopts='' -ra -q -p no:cacheprovider --basetemp .tmp/pytest-all-native-security-final
```

This includes mock/API coverage, real Windows filesystem handles/junctions/hardlinks, disposable Windows AppContainer execution, environment/secret propagation, scoped Skill materialization, process limits, timeout and cancellation. It also includes the existing real WSL filesystem/resource-limit test and the new WSL test proving IP/Unix sockets stay blocked and enabled networking is rejected before dispatch. Ordinary runs leave the native tests opt-in; an earlier default run passed 396 tests with 11 native cases skipped, before the final extra network rejection regression was added.

The local workflow also passed separately against the existing **Ubuntu-24.04 WSL2** distribution:

```powershell
$env:OAW_TEST_SANDBOX_RUNTIME='wsl:Ubuntu-24.04'
backend/.venv/Scripts/python -m pytest backend/tests/test_sandbox_workspace_ui.py::test_real_local_workspace_scenario -o addopts='' -q -p no:cacheprovider --basetemp .tmp/pytest-wsl-final-scenario
```

This scenario configures an external workspace and linked/local defaults, browses its roots, runs diagnostics, executes a manual command and a Skill through live Agent capabilities, previews/downloads both generated files, reconnects to unchanged receipts, browses during a running command, cancels while retaining readiness, and clears cache without deleting outputs. The Windows variant passed in the combined suite. No LLM was used to choose the Agent tool invocation.

Frontend validation:

- `npm --prefix frontend test -- --reporter=dot`: **152 passed** across 27 files.
- `npm --prefix frontend run build`: passed TypeScript checking and Vite production build.
- With `PLAYWRIGHT_CHANNEL=msedge`, `npm --prefix frontend run test:e2e -- sandbox-card.spec.ts execution-configuration.spec.ts equipment-surfaces.spec.ts`: **4 passed**. The Sandbox runtime/file responses in `sandbox-card.spec.ts` are mocked; configuration and equipment cases use the real local API. The browser test verifies inspector/window separation, actual downloaded bytes, pointer resizing, preview error states, console preservation, close/reopen and browser reload without re-execution. Light/dark screenshots were inspected.
- `git diff --check`: passed.

Native tests ran outside the tool's restricted token so disposable AppContainer profiles and the existing WSL distribution were accessible. No separate native Linux host was available: the Linux implementation was exercised inside real WSL. HTTP authentication/failure classification uses mocked command results; the bundled backends remain offline and no external service authentication was attempted. Vite reports its existing large-bundle warning and Python reports dependency deprecation warnings.

Real tests found and verified fixes for Windows workspace-handle sharing and cancellation status, and WSL file-worker recovery during concurrent browsing. Enabled networking remains unsupported because a shared host namespace would expose trusted control services. The implementation does not add PTY/ConPTY, remote-job orchestration, dependency installation, a provider framework or an IDE.
