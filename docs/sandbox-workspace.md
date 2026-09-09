# Sandbox configuration and window workspace

The compact card and inspector report runtime, readiness, folder, access and network policy, with Start/Stop and shortcuts to the window's Workspace and Settings tabs. The inspector's collapsed Configuration section provides the same runtime/workspace, resource-limit and environment editors as Window settings. Unsaved settings and environment drafts follow the Sandbox between these surfaces; they are transient and are not saved to browser storage. Save commits through the same authoritative API, while Reset or Reload discards the corresponding draft. Workspace shows Files on the left, a file preview at upper right and the terminal at lower right. Both dividers support pointer dragging and arrow keys. History stays inside the terminal; presets, Skill resources, diagnostics and recovery remain in Window settings. Opening, closing or reopening a window never submits a command or changes runtime lifecycle.

New, copied and summoned Sandboxes start stopped. Managed storage is prepared through the existing Start lifecycle. The selected runtime remains pinned after first start. Runtime, workspace, network and resource-limit changes require Stop → Save → Start. Ordinary environment changes apply to the next command without a restart.

## Configuration and authority

Sandbox-local variables use the existing `EnvironmentProfile` document model, editor and private credential bindings, scoped directly to the Sandbox node. No hidden Environment cards are created. Under Settings → Environment variables, choose Value or Secret, enter the value, and save. Secret references and encrypted bindings are managed automatically in the same save. Configured secrets stay unchanged when their input is blank. Secret input is local to the mounted editor and is cleared on reload or closing it; ordinary drafts still follow the Sandbox across surfaces.

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

## Validation

From the repository root, run focused API/status tests with
`backend/.venv/Scripts/python -m pytest backend/tests/test_sandbox_workspace_ui.py backend/tests/test_sandbox_network_contract.py -k 'not real_'`.
From `frontend`, run `npm test`, `npm run build`, and
`npm run test:e2e -- sandbox-card.spec.ts execution-configuration.spec.ts`.
The Sandbox layout browser scenario mocks runtime/file responses; configuration
scenarios use the isolated local API. Neither proves a native isolation boundary.

Real workspace tests require `OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1` and
`OAW_TEST_SANDBOX_RUNTIME=windows` or `wsl:<installed distribution>`; select
`backend/tests/test_sandbox_workspace_ui.py::test_real_local_workspace_scenario`.
Run native tests with an ordinary user token outside restricted tool sandboxes.
See [network acceptance setup](sandbox-networking.md#real-runtime-acceptance)
for the separate networking prerequisites and verification boundaries. Preserve
per-run logs, failures, skips and unresolved platform evidence in `.outputs/` or CI.
