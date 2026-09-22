# Published applications

Try the ready-to-run, key-free example with `python examples/deployed-workspace/run.py`. It opens <http://127.0.0.1:38475>; the demo password is `oaw-demo-2026`. See the [example instructions](../examples/deployed-workspace/README.md).

[Documentation](README.md) · [简体中文 / complete deployment guide](deployment.zh-CN.md)

Publish a saved Legion workspace as a locked application. The operator service runs an independent profile copy and exposes a small authenticated business API. It does not mount the editor, management APIs, model settings, or world event stream. Source-profile edits do not affect an existing deployment.

## Quick start

1. Run the normal setup script and configure your connections, agents, resources and Legion workspace.
2. Save the layout, choose **Publish application**, name the app and explicitly enable terminal access if needed.
3. Create a release and copy its command. Finish work, stop Sandboxes and shut down the engineering backend before running that command from the repository root:

```bash
python3 scripts/deploy.py --source /path/to/engineering-profile --release RELEASE_ID --open
```

On Windows use `python`; the publication panel supplies the actual profile path and release ID. The command verifies the configuration, copies and checks the stopped profile, prints a generated access password and serves the app at `http://127.0.0.1:38474`. Save the password. Use `--ask-password` for a private password prompt, `--output /new/directory` for a custom destination, and `--port` for another port.

Restart without resetting runtime data:

```bash
python3 scripts/deploy.py --serve /path/to/deployment --open
```

To rotate a forgotten password, stop the service and run the same command with `--reset-password --ask-password`. Without `--ask-password`, a new password is generated and printed. Add `--prepare-only` to rotate without starting the service.

Publication records are recipes; the data snapshot is made by the deployment command. Configuration changes after publication require a new release. Existing destinations are never overwritten. Failed copies remain in explicitly named `.partial-*` directories and are not served automatically.

## Server hosting

Create the deployment on the target host under its service account. Use `--secure-cookie --prepare-only` to prepare an HTTPS deployment, then run it with `--serve`. Put an HTTPS reverse proxy in front of the runtime port:

```caddyfile
assistant.example.com {
    reverse_proxy 127.0.0.1:38474
}
```

The runtime has its own password login; never proxy the engineering API or distribute the host control-plane credential. Secure cookies require the HTTPS URL. See the [systemd example](../deploy/oaw-runtime.service). Customize its account and paths. Backend environment authentication requires credentials in the service environment. Native Sandbox prerequisites still apply; generic container packaging does not replace them.

## Releases, data and scope

- Prepare each update in a new directory, verify it on another port, then switch the user entry point. Keep the old directory and matching code/build for rollback. Rollback does not merge conversations/files/tasks from the newer deployment or undo external effects.
- Each deployment is one shared application with one access password. Sessions, tasks and files are shared between its users; this release has no tenant isolation or SSO.
- The snapshot includes the entire source profile (including existing history, files and encrypted credentials) to preserve cross-Legion dependencies. Only published surfaces are projected through the operator API. Keep the deployment directory host-private.
- Windows credentials remain account-bound. Prepare and run under the same host/account. Cross-host migration requires rebinding credentials, plugins and external dependencies.
- Known external workspace bindings require `--allow-external-workspaces`. They remain shared external folders, outside the snapshot and rollback. Review plugin-specific dependencies separately.
- Supported public surfaces: Conversation and its sections, Sandbox files/preview and explicitly allowed terminal, Text, Image, `oaw.tasks` task boards, and Agent status. Third-party and future plugins opt in through `NodeDeploymentDefinition`, reusing existing views and handlers; see [plugin deployment](plugin-deployment.md). Undeclared custom surfaces (including currently unadapted MatCreator views) fail publication and may remain as hidden backend dependencies.
- The deployed UI reuses Legion Workspace, WorkspaceSection and the existing business components. Layout splits are fixed; users can switch tabs without losing drafts. Only engineering controls are omitted. Text uses the original editor in read-only mode. Hidden/unplaced cards have no fallback inspector. Graph structure and configuration changes are rejected even through internal Agent graph operations.
- Graph/configuration snapshots do not freeze normal business state: conversations, authorized documents, tasks and workspace files remain writable. Plugin versions are checked at startup. Preserve the release's code checkout/build as well as its data.

Stop the service before backing up the entire deployment directory, matching code, keys and external data. Runtime logs are under `logs/launcher.log`. See the [Chinese guide](deployment.zh-CN.md) for troubleshooting and detailed operational steps.

## Verification

```bash
python -m pytest backend/tests/test_deployments.py backend/tests/test_control_plane.py
npm --prefix frontend run build
npm --prefix frontend run test:e2e:deployment
```

The browser suite uses a disposable mock-Agent deployment. It validates UI and API boundaries, not native OS Sandbox isolation.
