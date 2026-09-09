# Shared sandbox Python

Normal sandbox backends select a persistent Python venv under the OAW data root:
`runtime/python/venv`. Python commands, Skill scripts and the sandbox shell PATH
use this environment. It excludes system site-packages, user site-packages,
backend `PYTHONPATH`, and the backend project venv. Windows has a managed copy of
the base interpreter and standard library in `runtime/python/base`; sandbox ACLs
never grant access to the backend or host Python installation.

Workspaces and resource mounts retain their existing isolation. The Python
runtime is read-only inside each sandbox. Packages persist across commands,
sandboxes, backend restarts and plugin removal. Existing Python processes may
retain modules they have already imported; a new command sees installed packages.

Windows and Linux venvs are not binary-compatible. WSL distributions each share
one Linux venv under `runtime/platforms/<distribution-key>/runtime/python/venv`.
There are no per-sandbox or per-plugin environments.

## Installing missing dependencies

Agents with an execute relationship receive `install_python_packages`. For example:

```json
{"sandbox": "my_sandbox", "requirements": ["numpy>=2", "pillow"]}
```

After a missing import, call this tool and retry the Python command. Skills do not
need complete dependency declarations. The manual API is
`POST /api/sandboxes/{id}/python/packages` with `{"requirements": [...]}`.
Start the sandbox once to select its execution platform before installing.

The manager serializes mutations with an OS file lock, including across backend
processes. A lock wait exceeding 60 seconds reports a busy error; retry the call.
Package installs have a 30-minute wall-clock limit; interpreter setup has a
10-minute limit. The WSL transport budget covers lock acquisition, setup, installer
bootstrap and package installation. uv retains its connect/read timeouts for
stalled network I/O. Progress is written live to `runtime/python/install-output.log`;
`runtime/python/install.log` retains outcomes, elapsed time and output tails,
including failures and timeouts. Cancellation drains active
mutations before releasing ownership. Runtime execution itself does not wait for
an installation once the venv has been initialized.

`uv` creates the environment when available. Otherwise the standard-library venv
module creates the initial environment and bootstraps a standalone `uv` into
`runtime/python/tools`. This first-use fallback requires Python venv/ensurepip
support and package-index access. Subsequent installs use the standalone binary,
a clean base interpreter, and an explicit target prefix, so shared-package Python
startup hooks do not run in the backend during installation.

This minimal implementation accepts index package names, extras and version
constraints, and installs wheels only. Source builds, local paths, URLs and raw
installer options are unsupported. Python commands and installed CLI entry points
automatically use the shared venv, including calls inside shells and subprocesses.
After installation the manager retargets launcher interpreter references without
executing installed package code on the host. Existing environments are repaired
on their next preparation/execution; no package reinstall or venv activation is
needed. Direct `pip install` inside a sandbox cannot mutate the read-only
runtime; use the manager tool. Package downloads are an explicit host-managed
operation; the sandbox's own network policy remains unchanged.

## Plugin discovery and bootstrap

Startup scans the project `plugins/` directory, additional directories listed in
`OPEN_AGENT_WORLD_PLUGIN_DIRS` (OS path separator), and installed entry points.
Trusted plugin code must remain importable independently of its sandbox runtime
dependencies. Registry registration reconstructs the catalog on every startup;
it is not treated as a new environment installation.

Local plugin `pyproject.toml` files may declare:

```toml
[tool.open-agent-world.runtime]
python = ["numpy>=2", "pillow"]
```

Packaged plugins can declare the equivalent
`PluginDescriptor(python_requirements=("numpy>=2", "pillow"), ...)`.
The TOML declaration takes precedence when loading a local package. Normal
`project.dependencies` continue to describe trusted plugin/backend dependencies;
they are not copied into sandbox Python automatically.

Discovery persists plugin identity, declaration digest, first initialization time,
state, completed platform digests and the latest error in `runtime/plugins.sqlite3`.
After application initialization a background task prepares declared requirements
for available execution platforms. Plugins appear in the catalog immediately.
States are `discovered`, `environment_pending`, `environment_ready`, and
`environment_failed`.

`GET /api/runtime/python/plugins` exposes bootstrap states/errors.
`POST /api/runtime/python/plugins/reconcile` retries failed or changed requirements
from the current registry. Restart to discover newly added plugin directories or
updated plugin code. Each startup reconciles again; receipts checked under the
mutation lock skip unchanged successful installs. Failed/interrupted preparation
is retried. Removing a plugin removes its discovery row without uninstalling
packages. Runtime reset/cleanup is a separate future management operation.
