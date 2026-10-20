# Hosted administration and private application deployment

`/admin` separates application building/deployment, shared models and account
inventory. An administrator has a private builder profile, using the ordinary
canvas, Legion and Workspace layout tools. Desktop entry points are unchanged.

## Operator installation on the existing Linux host

Update the application and built frontend; restart the gateway to load the new
admin API. Existing user workers do not need to restart for gateway assignment.
The gateway creates `control/deployments.sqlite3` with private permissions.
Use the existing root-owned application and hosting directories:

```sh
cd /opt/open-agent-world
backend/.venv/bin/python -m backend.hosting.provisioner engineering \
  --directory /srv/oaw-hosting --application /opt/open-agent-world --user admin
backend/.venv/bin/python -m backend.hosting.provisioner enable \
  --directory /srv/oaw-hosting --application /opt/open-agent-world
```

`engineering` is an explicit operator action for an existing enabled admin. It
refuses to replace an assigned workspace. `enable` checks kernel network-filter
support and installs a fixed-purpose root systemd timer. Existing differing
timer units require operator inspection. The timer is opt-in; personal installs
do not run it.

## Administration workflow

1. Open the engineering canvas from `/admin`. Build or instantiate a Legion,
   put its dependencies inside that Legion, and save its Workspace layout.
2. Use **Publish application**. The saved release must still match the current
   engineering configuration when deployment is submitted.
3. In **Build & deploy**, select the release, enabled target accounts and
   **Workspace only** or **Canvas · testing**. Each target gets new private data.
4. Wait until **Ready**, then explicitly **Set entry**. No existing account is
   switched by the deployment operation itself. Switching does not stop old
   background jobs; old browser requests are rejected by their pinned instance
   ID and old WebSockets close. Switch back by selecting the original profile.

Selected portable documents/configuration cross the publication boundary;
conversation history, attachments, shared state, profile files and credentials
do not. A published Legion must be self-contained. Shared model connections and
the operator's `worker-common.env` are inherited by new workers. Initialization
uses the same operator-selected services factory, including its plugin registry.

## Authority and recovery

Only authenticated administrators with the correct Origin can enqueue a
deployment or select another account's entry. A browser cannot choose a worker
URL, filesystem path, Linux username, application directory, runtime factory,
shell command or service name. Admin template capture is routed only to that
admin's own engineering worker. Ordinary users cannot access the admin API.

The root reconciler consumes durable UUID-keyed intents. It creates new service
identities and fixed units only. Plugin/template initialization runs under the
new workspace's UID in the ordinary restricted worker filesystem/network
environment. A successful bootstrap is registered in the gateway only afterward.
No existing worker is stopped, copied, re-owned or overwritten.

Requests are idempotent by request UUID and payload. A preparing job found after
an interrupted reconciler becomes **interrupted**, never blindly replayed.
Failed/interrupted jobs retain their artifacts for operator inspection. Check
`journalctl -u oaw-hosted-provisioner.service` and the UUID-named initialization
unit before retrying. The UI always shows retained deployment history. There is
no automatic deletion or migration of older workspaces; operators must manage
retention. This POC accepts up to 10 pending jobs, 4 MiB templates and 100 retained
profiles.

Account creation/role/password changes and infrastructure settings remain
operator actions. Web settings implement shared models, shared Sandbox
environment variables and application/entry selection. Do not expose arbitrary
process environment or host settings to workspace users.

## Shared execution environment

The administration **Settings** button and `/admin?section=environment` open the
existing `SettingsPanel` at **Sandbox → Global environment variables**.
Administrators use the same panel from their engineering canvas. The
`sandboxSettingsSource` adapter selects personal storage or the administrator
endpoint; loading, editing, saving and error recovery share one UI and workflow.
Host runtime/path controls remain personal-only. Saved secret fields remain blank; leaving a
configured secret blank preserves it, changing it replaces it, and removing its
last variable deletes its binding. Revision checks reject concurrent stale saves.

Enable delivery during offline installation or upgrade:

```sh
backend/.venv/bin/python scripts/hosted.py shared-environment-enable \
  --directory /srv/oaw-hosting --origin http://127.0.0.1:8080
```

Regenerate/install the systemd units as for shared models, then start the gateway
and workers. This establishes `shared_environment_origin` on existing profiles
and on profiles created later, independently of shared models. Subsequent
settings saves do not need restarts or individual user configuration.

The gateway encrypts secrets at rest in the control directory. A registered,
enabled worker authenticates with its own identity and receives secrets encrypted
for that worker. Each configuration resolution fetches one coherent snapshot;
failed refreshes block execution rather than retaining revoked credentials.
The existing Sandbox execution resolver injects the values and includes secrets
in its output-redaction path. Nothing is copied into user databases, templates,
or the host's process environment. Running commands retain their initial values.
Precedence is deployment defaults, workspace defaults, selected/linked
Environment Profile, then local Sandbox values (case insensitive).

These are credentials shared **with all authorized Sandbox executions**. Hidden
input fields and exact-value output redaction do not prevent user code from
using, transforming, writing or transmitting injected credentials. Do not put
cloud management credentials or secrets that users must never possess here.
Host paths, interpreter/loader controls and reserved environment names remain
unsupported. This setting does not configure ECS/systemd, Alibaba resources,
host-side Pack processes or unrelated integrations such as DeepL.

## Shared publication contracts

The Alibaba branch includes dev's Plugin API 1.36 publication contracts. The
complete MatCreator research layout reuses its ordinary card components and SDK
business handlers, including Graph, Jobs, knowledge, structure preview and
artifacts. The cloud composition loads the original Pack and its research preset;
the earlier hosted-only preset is no longer offered for new instances.

Plugin API 1.37 provides the provider-neutral subprocess environment contract.
Hosted services select a minimal environment; the Pack uses this public contract
without a cloud-specific runtime subclass. Personal process defaults are retained.

Publish checks all visible pages before creating a release. Document-backed
plugins may explicitly publish their business document; other plugin operations
retain explicit capability permissions. This does not expose arbitrary editor
APIs or make undeclared custom plugins automatically publishable. See
[plugin deployment](../plugin-deployment.md).

Existing releases and user workspaces are snapshots, not references to the latest
preset. Updating application code does not rewrite their layout or account entry.
Publish and deploy a new release to adopt the full layout; the admin chooses
when each account switches and can switch back without replacing private data.

Hosted publication retains the source Sandbox's explicit networking, memory,
process and command timeout policy. This is separate from portable Legion
acquisition, which still strips machine-specific settings. Host bindings and
private environment values remain excluded from the published template.

Published conversations reuse the normal Run activity, tool detail and public
reasoning handlers. Access requires a published conversation or its recorded
delegated Run ancestry. Raw model request logs and unrelated Runs remain private.

The 2026-10-20 ECS rollout now assigns all three test accounts to independent
full MatCreator Workspaces; admin remains in its engineering profile. Actual
browser sign-in, Graph/Jobs/knowledge views, attachment structure rendering,
cross-user rejection and admin-route rejection were checked. Shared model
settings and original profile data were preserved. A real Alibaba Sandbox
command imported the five configured scientific libraries, completed a small
NumPy calculation, reached public HTTPS and released its VM. This does not
establish an end-to-end research run with a real model.
