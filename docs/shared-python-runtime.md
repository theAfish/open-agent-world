# Shared sandbox Python

[Documentation](README.md)

Normal sandbox backends select a persistent Python venv under the OAW data root:
`runtime/python/venv`. Python commands and Skill scripts use this environment.
Windows AppContainer and macOS Seatbelt select the managed environment for
ordinary commands, shells, installed CLI entry points and Python commands alike.
They do not support `python_environment: "none"`; an unavailable environment
returns a preparation failure rather than falling back to the backend Python.
The environment excludes system/user site-packages, backend `PYTHONPATH`, and
backend project dependencies. Both platforms copy the backend's base Python
runtime into `runtime/python/base`, then create a separate venv. Windows copies
its interpreter, standard library and runtime DLLs. macOS copies its interpreter,
standard library and required Mach-O libraries, relocates library references and
signs modified binaries. Third-party packages and startup hooks are excluded.
Sandbox access is granted only to the managed copy, not the backend environment.

Workspaces and resource mounts retain their existing isolation. The Python
runtime is read-only inside each sandbox. Packages persist across commands,
sandboxes, backend restarts and plugin removal. Existing Python processes may
retain modules they have already imported; a new command sees installed packages.

Windows, Darwin and Linux venvs are not binary-compatible. WSL distributions each
share one Linux venv under `runtime/platforms/<distribution-key>/runtime/python/venv`.
Seatbelt uses the Python version of the running backend. A verified runtime copy
is cached under `~/Library/Application Support/OpenAgentWorld/shared-python/`
and each profile copies it below its own `runtime/python/base`. A source identity
receipt distinguishes the backend runtime from older downloaded CPython copies.
The existing wheel-cache path is retained. A profile reset requires copying the
base again, without downloading CPython. Apple Container VM remains a separate
Linux environment below `runtime/macos-container-python`; macOS binaries cannot
be used in that VM.

On Seatbelt, uv's wheel cache also lives in that shared directory. A new
profile still creates its own venv and installs its required packages, but
previously downloaded compatible wheels can be reused without fetching them
again. Changing the Python version or CPU architecture uses a different
distribution/cache directory.

Sandbox startup and inspection report Python preparation state. The host-owned
Pack bootstrap prepares declared dependencies using the same shared installer on
Windows and macOS. First-time Python setup copies the base runtime; missing
third-party dependencies use uv and its wheel cache. Existing packages in an OAW
venv are preserved during a macOS base-format migration when the Python ABI
matches. Package hooks are never executed on the host during migration.

Installed `.oawpack` manifests declare `runtime.sandbox.python`; discovery feeds
the same bootstrap used by bundled plugins. All enabled Pack requirements are
aggregated and checked for obvious conflicts before selection/enable. The shared
runtime performs a complete `uv` dry-run before installing under its mutation
lock. Interactive Agent installs include these Pack requirements too. Installation
and environment state remain separate, and failed preparation can be retried
without reinstalling the Pack. See [Pack distribution](pack-distribution.md).

## Installing missing dependencies

Agents with an execute relationship receive `install_python_packages`. For example:

```json
{"sandbox": "my_sandbox", "requirements": ["numpy>=2", "pillow"]}
```

After a missing import, call this tool, collect its final result, and retry the Python command. Skills do not
need complete dependency declarations. The manual API is
`POST /api/sandboxes/{id}/python/packages` with `{"requirements": [...]}`.
Start the sandbox once to select its execution platform before installing.

The manager serializes mutations with an OS file lock, including across backend
processes. A per-profile lock wait exceeding 60 seconds returns structured
`resource_busy` feedback to the Agent. The shared macOS runtime-copy lock serializes preparations across checkouts.
Inspect the installation
and wait before retrying the rejected command.
Package installs have a 30-minute wall-clock limit; most interpreter setup steps
have a 10-minute limit. The WSL transport budget covers lock acquisition, setup, installer
bootstrap and package installation. Progress is written live to `runtime/python/install-output.log`;
`runtime/python/install.log` retains outcomes, elapsed time and output tails,
including failures and timeouts. Cancellation drains active
mutations before releasing ownership. Commands check Python preparation before
launch; while installation invalidates launcher readiness, even shell commands
can encounter this preparation barrier. Host waiting does not use that barrier.

## Agent execution and waiting

`execute_command`, `run_skill_script`, and `install_python_packages` share the
Sandbox operation journal. Their `wait_seconds` observation budget defaults to
1 second (0–60). A quick operation returns its result; an unfinished operation
returns `status: "running"`, `operation_id`, and `command_id`. This is acceptance,
not success. Do not submit the same command again while it is running.

```json
{"sandbox": "my_sandbox", "operation_id": "returned-id", "wait_seconds": 30}
```

Pass this to `wait_sandbox_operation`. It waits on the host, returns the final
result or another running response, and never launches Python or a shell. The
Agent may do independent work between waits. `wait_seconds: 0` polls immediately.
Omitting `operation_id` performs a cancellable host timer; after it returns,
inspect the resource again. Elapsed time alone does not establish readiness.

`inspect_sandbox.active_commands` includes installations and their operation kind;
`shared_python` reports bounded installation output and the last observed install
state. Logs may describe an earlier operation: operation receipts, not old log
entries, establish current liveness. Pending installation waits also return this
progress observation. Final results use the existing command journal. Uncollected
operation results remain available even after newer commands finish; collected
results follow the journal's normal retention limit.

Cancelling a wait does not cancel execution. Use `cancel_command` with the returned
ID to cancel a particular operation, subject to the existing ownership checks.
Stopping an Agent Run also cleans up its owned pending operations. Package mutation
must drain before releasing its lock; cleanup can therefore remain pending. A
backend restart marks unfinished work interrupted and does not replay side effects.

Resource contention and environment preparation failures are operational tool
results. Nonzero exits, process timeouts, stdout and stderr remain command results.
Security isolation failures, unconfirmed cleanup, unexpected implementation faults,
and explicit cancellation retain their distinct control semantics. Neither an
error result nor a timed wait is treated as task success.

`uv` creates the environment when available. Otherwise the standard-library venv
module creates the initial environment and bootstraps a standalone `uv` into
`runtime/python/tools`. This first-use fallback requires Python venv/ensurepip
support and package-index access. Subsequent installs use the standalone binary,
a clean base interpreter, and an explicit target prefix, so shared-package Python
startup hooks do not run in the backend during installation.

This minimal implementation accepts index package names, extras and version
constraints, and installs wheels only. Source builds, local paths, URLs and raw
installer options are unsupported. Selected managed-Python commands and installed
CLI entry points use the shared venv, including calls inside their shells and subprocesses.
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
