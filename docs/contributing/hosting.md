# Host separate workspaces for multiple users

Hosted mode supports administrator-created accounts, password login and standard
OIDC. After login, users enter a canvas or a published Workspace. Each user has a
separate backend instance, database and file directory. Personal desktop and source
installations still default to no login; shared-password deployment also remains available.

This setup serves a small group on one Linux server, with one default application
instance per account. It has no public registration, shared workspaces across users,
autoscaling or automatic release upgrades. Administrators manage accounts through
the server CLI; there is no browser administration API.

## Prepare the installation

Use Linux with running systemd 249+, Python 3.11+ and the Node/uv tools required by
the source installation. Ubuntu 24.04 LTS is recommended. Docker is not required.

Install the application in an administrator-managed `/opt/open-agent-world` and
keep hosted data in `/srv/oaw-hosting`. Do not put the application or Python under
`/home`: `ProtectHome` hides it from services. For uv-managed Python, set
`UV_PYTHON_INSTALL_DIR=/opt/oaw-python` before installation, or use system Python.
Set `UV_LINK_MODE=copy` to avoid permission changes through shared cache hard links.

Complete the [source setup](../getting-started.md), including approved plugins, then run:

```bash
cd /opt/open-agent-world
umask 022
uv sync --project backend --locked --inexact --extra hosting --extra adk --extra litellm
npm --prefix frontend run build
backend/.venv/bin/python scripts/hosted.py prepare-runtime
```

`--inexact` preserves plugin dependencies. `prepare-runtime` prepares the shared
runtime for administrator-reviewed source Packs without changing personal workspaces.
The application, dependencies, interpreter and their parent directories must be
root-owned and not writable by group or other users. After verifying these paths
are dedicated to this installation:

```bash
sudo chown -R root:root /opt/open-agent-world
sudo chmod -R go-w /opt/open-agent-world
sudo install -d -m 700 -o root -g root /srv/oaw-hosting
```

Apply the same ownership and write restrictions to `/opt/oaw-python` if used.
The installer checks shared application and interpreter permissions; it does not
download dependencies or repair code ownership. Administrative commands use
`sudo`; services run as separate non-root accounts.

## Create accounts and instances

```bash
sudo backend/.venv/bin/python scripts/hosted.py init \
  --directory /srv/oaw-hosting --origin https://oaw.example.com

sudo backend/.venv/bin/python scripts/hosted.py user-add \
  --directory /srv/oaw-hosting --user alice --display-name Alice

sudo backend/.venv/bin/python scripts/hosted.py profile-add \
  --directory /srv/oaw-hosting --user alice --mode builder --name 'Alice workspace'
```

`user-add` prompts privately for a password of at least 12 characters and its
confirmation. Never put passwords in commands, URLs or source files. Use
`--oidc-only` for an account that will be bound to an external identity. Each user
receives a stable internal UUID.

### Open a published Workspace after login

A release can include several Legion workspaces. Select them and set their order
in **Publish application**; the first workspace opens after login. The title menu
only lists published workspaces. Template export and per-user provisioning preserve
this order and remap each workspace's layout to the new user's cards.
The existing template size limit of 101 nodes applies to the combined selection.

Publish a Legion in an engineering workspace, stop that backend, then export a template:

```bash
sudo backend/.venv/bin/python scripts/hosted.py template-export \
  --source /srv/oaw-engineering --release RELEASE_ID --output /srv/oaw-hosting/research-template.json

sudo backend/.venv/bin/python scripts/hosted.py user-add \
  --directory /srv/oaw-hosting --user bob

sudo backend/.venv/bin/python scripts/hosted.py profile-add \
  --directory /srv/oaw-hosting --user bob --mode runtime \
  --application research --name 'Research assistant' --template /srv/oaw-hosting/research-template.json
```

The template uses the Legion portable format. Each instance gets new card IDs and
a database. It excludes engineering conversation history, runs, attachment
directories, credential storage and unrelated cards. It includes the selected
cards' current documents, instructions and initial resources: review these for
private content before exporting. Custom plugins define their portable document
projection through `document.capture` / template handlers. Known embedded
credentials and host-directory fields are rejected.

The Legion must contain its dependencies; links to cards outside it prevent
export. Republish after changing the source workspace. Model API keys are not
copied, but the public model catalog is retained. Configure instance credentials
separately; models using `environment` authentication read their variables from
the instance's `worker.env`.

Administrators configure environment-authenticated connections through the
template. Hosted browsers cannot create, edit or discover models through such
connections, preventing environment credentials from being sent to user-selected
addresses. Canvas users can still supply their own API keys.

Before publishing a runnable template, configure its model connection with
`environment` authentication and a variable name. Then add the key to the new
instance's `worker.env` before starting it. Setting a variable does not convert an
`api_key` connection; deployed Workspace pages do not expose model settings.

Instances created from the same template retain separate chats and files.
`profile-add` refuses to overwrite an existing instance. Restarting neither
reimports the template nor clears data. Automatic data migration to new releases
is not implemented; preserve existing instances and arrange explicit migrations.

## Start the services

Generate and inspect the systemd units, install them, then start the target:

```bash
sudo backend/.venv/bin/python scripts/hosted.py systemd --directory /srv/oaw-hosting
sudo cat /srv/oaw-hosting/systemd/oaw-hosted/*.service
sudo backend/.venv/bin/python scripts/hosted.py install-systemd --directory /srv/oaw-hosting
sudo systemctl enable --now oaw-hosted.target
sudo systemctl status oaw-hosted-gateway.service
```

`systemd` only generates files. `install-systemd` requires stopped services,
validates configuration and directories, creates dedicated accounts, sets
permissions, installs units and runs daemon-reload. It does not start services.
Regenerate after configuration changes. The gateway defaults to `127.0.0.1:8080`;
change it with `--port` during generation. If using `--name` for multiple
installations, use the same name during generation and installation.

Each user has a stable Linux UID, data directory and service. systemd creates a
`0600` Unix socket accessible only to the gateway account and passes its file
descriptor to the worker. Workers do not listen on TCP. They still validate the
gateway's short-lived identity assertions. Canvas and deployed Workspace modes
share this boundary.

Services use `ProtectSystem=strict`, `ProtectHome`, private temporary directories,
empty Linux capabilities, privilege escalation restrictions and process visibility
limits. Workers receive their own read-only configuration and writable data
mappings; the original hosting directory and other workers' sockets are hidden.
Workers cannot rewrite configuration or `worker.env`. The gateway owns the account
database and routing. Default memory limits are 512 MiB for the gateway and 2 GiB
per worker. Adjust `MemoryMax`/`TasksMax` with `systemctl edit UNIT` for your workload;
these are limits, not capacity guarantees. See [systemd service sandboxing](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
and [socket activation](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html).

Serve HTTPS with Caddy or Nginx, for example:

```caddyfile
oaw.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Configure DNS and certificates. Expose only necessary HTTPS/HTTP and restricted
administration ports. Preserve the original Host: the gateway validates Host and
Origin and does not trust browser forwarding headers. Login rate limits use the
actual socket peer, so users behind one reverse proxy share its source limit.

For Nginx, set `client_max_body_size` to at least `48m` to accommodate a 32 MiB
knowledge-base file in base64 JSON. Set `proxy_read_timeout` to at least `3600s` for
Sandbox startup, execution and Python installation during cold starts. Workers
still control operation deadlines; other gateway API reads time out after 300
seconds. A timeout does not prove work stopped. Check its state before retrying.

Node actions and resource operations accept up to 48 MiB; ordinary configuration
JSON retains a 4 MiB limit and hosted path checks. Oversized requests return 413.
Progress uploads for session attachments and PDF imports carry the workspace ID,
and abort when login expires or the workspace changes. Users select files again
after logging in or refreshing.

Before adding users or changing configuration, stop `oaw-hosted.target`, run the
account/instance commands, then regenerate, install and start services. Existing
UUIDs, passwords, signing keys, sessions and files are preserved. The installer
can convert old HTTP worker routes to Unix sockets, saving the original routes
in `control/hosting.before-systemd.json` without reinitializing data. Stop any old
deployment first; old Compose files are no longer used.

```bash
sudo journalctl -u oaw-hosted-gateway.service -n 100
sudo systemctl list-units 'oaw-hosted-worker-*'
sudo systemctl restart oaw-hosted.target
```

Administrators review and install application and plugin code. Browsers cannot
install plugins, read host directories, relocate storage, enable local Codex or
choose a local Sandbox. For a remote Sandbox adapter, set
`OAW_HOSTED_SERVICES_FACTORY=package.module:factory` in `worker.env`. Its
`factory(settings)` returns `ApplicationServices`. Install the adapter in the
shared environment; it must use instance data and approved plugins without
enabling host command execution.

`IPAddressDeny` blocks link-local addresses and Alibaba metadata at
`100.100.100.200`. Installation checks cgroup BPF filtering using a temporary
loopback listener and fails if unsupported; it does not contact real metadata.
Also block outbound access to management services for your network. Do not attach
a server-wide administration RAM role; give remote Sandbox credentials only the
adapter's required permissions. This setup hosts trusted application code for a
small group, not arbitrary untrusted code inside service processes.

## Connect an OIDC provider

Write `/srv/oaw-hosting/control/oidc.json`, stop services, then regenerate and install
systemd configuration to set gateway-only read permissions. Keep secrets out of
logs and version control:

```json
{
  "issuer": "https://YOUR_ISSUER",
  "discovery_url": "https://YOUR_DISCOVERY/.well-known/openid-configuration",
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "session_secret": "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS",
  "public_origin": "https://oaw.example.com",
  "scopes": ["openid", "profile"],
  "token_endpoint_auth_method": "client_secret_basic"
}
```

Register `https://oaw.example.com/auth/oidc/callback` with the provider. Follow its
documentation for scopes and token endpoint authentication; some CIAM setups use
`USER_API` and `client_secret_post`. OAW uses Authorization Code + PKCE and checks
signatures, issuer, audience, nonce, state and expiry. Verify integration with the
actual provider.

Bind a verified issuer/subject to an internal account:

```bash
sudo backend/.venv/bin/python scripts/hosted.py identity-link \
  --directory /srv/oaw-hosting --user bob \
  --issuer https://YOUR_ISSUER --subject VERIFIED_SUBJECT
sudo systemctl restart oaw-hosted-gateway.service
```

Accounts are not merged by email or display name; unknown identities receive no
workspace automatically. Explicitly binding a new provider identity to the same
internal UUID preserves data ownership. OIDC tokens are sent neither to workers
nor browser localStorage. The browser receives an HttpOnly random session cookie;
the server stores its hash.

## Administration and backups

Application code and Pack dependencies are administrator-managed. Installation
and updates perform full verification. Ordinary startup checks receipts, version
compatibility and top-level loading paths without rescanning every dependency
file. Keep application and dependency directories read-only for users; users have
no Pack installation API access.

To investigate damaged installations, run
`python -m open_agent_world.pack verify --data-root /path/to/profile` with the
backend interpreter. It performs full verification without importing plugin
factories, installing or repairing code. Password hashing, authentication and
data isolation are unaffected by the startup optimization.

```bash
sudo backend/.venv/bin/python scripts/hosted.py user-list --directory /srv/oaw-hosting
sudo backend/.venv/bin/python scripts/hosted.py user-disable --directory /srv/oaw-hosting --user bob
sudo backend/.venv/bin/python scripts/hosted.py user-enable --directory /srv/oaw-hosting --user bob
sudo backend/.venv/bin/python scripts/hosted.py password --directory /srv/oaw-hosting --user alice
```

Logout, password changes and disabling accounts revoke sessions. Existing
WebSockets are checked once per second. Submitted background work is not
automatically cancelled by logout. Sessions default to one hour and OIDC sessions
also respect token expiry. Passwords use salted scrypt hashes; failed logins have
persistent rate limits.

Stop services before backing up all of `control` and `instances`, together with
matching code/dependency versions and systemd configuration. Keep account data,
signing keys, user files and encrypted credentials together. Never expose the
control directory as a static site or commit it. `.oaw-hosting/` is ignored;
other deployment directories should be outside the repository.

## Local integration debugging

Explicitly initialize with `--allow-local-http` and `http://127.0.0.1:8080`, set
`--upstream http://127.0.0.1:8001` during `profile-add`, then run:

```bash
backend/.venv/bin/python scripts/hosted.py worker \
  --config .oaw-hosting/instances/PROFILE_ID/worker.json --port 8001
backend/.venv/bin/python scripts/hosted.py gateway \
  --config .oaw-hosting/control/hosting.json --port 8080
```

These same-account processes are for development. Multi-user servers use separate
systemd accounts and private data mappings. Workers always require gateway-signed
assertions, including on loopback.

Automated tests cover account storage, OIDC, instance routing, cross-user rejection,
template isolation, deployment API boundaries and frontend account switching.
Real servers, identity providers and remote Sandboxes need target-environment checks.

On a prepared Linux test installation, run
`sudo backend/.venv/bin/python scripts/test-hosted-systemd.py` to create temporary
accounts and services, check routing, sockets, permissions, revocation and restart
persistence, and clean up its own test resources. This is neither a cloud-provider
integration test nor a load test.
