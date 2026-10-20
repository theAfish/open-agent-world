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
operator actions. The current web settings implement shared models and
application/entry selection; the navigation can accommodate further explicitly
designed settings. Do not expose arbitrary process environment or host settings
to workspace users.

## Follow-up in dev

The general Workspace publication boundary belongs in dev, not in the Alibaba
adapter. Reuse card components and existing SDK/business handlers, deriving
access from authenticated ownership, the published node set and operation
permissions. Developers should not need a second set of Workspace endpoints.
The current explicit publication contracts do not yet cover all engineering
pages; improving the common defaults and authorization remains separate work.
