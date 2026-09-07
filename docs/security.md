# Sandbox security contract

The Sandbox card represents an operating-system security boundary. Windows, Linux and WSL2 runtimes refuse execution if their required isolation cannot be established. The backend never falls back to an unrestricted host subprocess.

## Runtime and workspace ownership

The card stores a requested runtime and an optional existing absolute host folder with read-only or read/write access. `SandboxManager` creates only a small binding record until first start. Runtime factories and capability probes belong to an instance-owned registry. The resolved runtime is pinned before execution and survives restarts; working-folder changes require a stopped runtime and participate in the normal node lifecycle transaction/rollback.

Application-owned metadata, temporary data and resource attachments remain separate from external folders. No destroy or unbind operation recursively removes an external folder. Commands in a writable external folder modify real data; card deletion and canvas undo do not roll back those edits. Drive roots, home roots, application data and path traversal are rejected; each platform additionally validates its native filesystem restrictions. Captured Legion templates exclude host folder bindings.

Discovery probes are cached for the application lifetime so status polling does not repeatedly wake WSL distributions. Explicit Refresh checks prerequisites again. No distribution, Docker daemon, package set or VM image is installed automatically, and WSL global settings are not changed. The WSL bridge uses a fixed trusted Python bootstrap with structured stdin messages and an allowlisted environment. Linux commands pass only to the Linux isolation backend. No WSL-wide shutdown is used.

## Linux and WSL2 controls

- Bubblewrap exposes a minimal root with read-only system tools, a selected `/workspace`, private temporary/home directories and only graph-authorized resource bind mounts.
- User, mount and PID namespaces isolate execution. Offline execution has a disconnected network namespace. Enabled execution uses slirp4netns in a separate network namespace with a public IPv4 egress firewall installed before workload admission. Bubblewrap creates a child user namespace that cannot administer that firewall. The host network namespace is never shared. Host homes, application credentials, `/mnt/c`, `/run/WSL`, desktop sockets and service sockets remain absent.
- Offline seccomp denies sockets/connections. Enabled seccomp permits IPv4 stream/datagram sockets; Unix/interop, raw, packet, netlink and IPv6 sockets remain blocked, along with privileged kernel operations and further namespace creation. The egress filter denies non-public ranges, virtual gateway/DNS aliases and enumerated host interface addresses. Read-only resolver and CA files support DNS and verified TLS without mounting the host `/etc`. See [networking prerequisites and acceptance](sandbox-networking.md).
- Every command joins a delegated cgroup-v2 subtree before untrusted work begins, with memory, swap and process limits checked by the trusted launcher. Full-tree termination and timeout cleanup use the runtime-owned cgroup.
- Failure to provide the required namespace, seccomp or cgroup controls makes the runtime unavailable. A Linux process sandbox shares its host Linux kernel; it is not a separate-kernel VM.

`SandboxInfo.workspace` and `resources_path` are paths inside the selected execution environment. `workspace_path` is the user's host binding. An attachment is found at `resources_path / attachment.relative_path`; the inspect capability returns the concrete path. Both terminal commands and agent argv use the selected runtime rather than the server OS.

## Windows controls

- A unique AppContainer or LPAC identity is created for each Sandbox.
- The managed workspace receives only the ACL entries required by that identity.
- Read-only and read/write attachments receive distinct grants. A read-only hard link has inheritance protected before the package's inherited workspace write grant is removed and replaced with read access.
- Offline commands receive no network capabilities. Enabled commands receive only `internetClient`, with no private-network or server capability. A narrow elevated broker atomically installs fixed WFP hard deny filters for the AppContainer: non-public IPv4, all host interfaces and IPv6. These remain enforced across helper exit or BFE restart and are released after Job termination when the Sandbox is destroyed. Windows DNS and certificate stores provide resolution and verified TLS. Required services, broker readiness and absence of a profile loopback exemption are checked before enabled execution; OAW never disables the firewall or creates an exemption. The API and workload remain unelevated. See [broker setup and lifecycle](sandbox-networking.md#prerequisites).
- The child receives a small allowlisted environment; credentials, tokens, SSH variables, cloud variables, and the application environment are not inherited.
- Explicitly selected [Environment Profiles and Compute Targets](execution-configuration.md) can add validated command-only variables. Authorization is rechecked before resolving secrets; loader and host startup overrides remain prohibited. Receiving code can read injected secrets. Output redaction does not replace isolation or authorization.
- The process is assigned to a Job Object before it can execute untrusted work.
- Kill-on-close, command timeout, process-count, and memory limits apply to the entire Job Object.
- Stop and destroy terminate the complete process tree.
- Commands run headlessly and cannot request an interactive desktop.
- An external NTFS folder receives only the selected AppContainer identity's grants while active. Stop/reload/destroy revoke those grants without resetting host ownership or other ACLs. Reparse points and hard links in an external tree are rejected; cleanup does not follow links into unrelated folders. Temporary files and resource attachments stay in managed storage.

## Fail-closed rules

Sandbox execution is refused when any of the following occurs:

- the platform is not supported;
- AppContainer/LPAC profile creation fails;
- ACL application or attachment materialization fails;
- the restricted process cannot be created suspended and assigned to the Job Object;
- Job Object limits cannot be applied;
- a path does not resolve below the managed root;
- an Agent lacks a current `execute` edge;
- an attachment edge is missing or grants insufficient access.

The implementation must never compensate by launching an ordinary host process.

## Trust zones

The management API independently accepts only numeric loopback socket peers by default; interface, gateway, public-address and NAT hairpin callers receive HTTP 403 or a WebSocket policy refusal. All bundled launchers disable ASGI proxy-header rewriting. The Vite development/preview ingress checks the actual socket peer before proxying HTTP or WebSocket requests, and never attaches a management credential for remote callers. This composes with the runtime's denial of guest access to host loopback.

Remote integrations can explicitly set `OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN` to a random credential of at least 32 characters and send `Authorization: Bearer <credential>`. Keep it host-private and outside Sandbox environment profiles, mounts and commands. Custom reverse proxies must authenticate remote clients before forwarding; they must not convert arbitrary remote callers into unauthenticated loopback callers. Forwarding headers alone never establish authority. Launch custom Uvicorn deployments with `--no-proxy-headers`.

Agent tools are grouped by operation. Relationships grant capability kinds;
operation definitions supply schemas, and the tool projection lists currently
authorized resource aliases. An alias, node ID, or operation ID is only a locator.
Every call resolves the selected resources against the live graph and rechecks
their required capability scopes before dispatch. Composite operations check each
resource independently, including calls using legacy internal capability IDs.
Tool listings and cached callables do not preserve revoked authority.

Skill runtime bundles compose current Skill-document access with current Sandbox
execution access. The host reads the existing card document, projects only declared
bundle files, and submits a command-scoped `RuntimeMount` to `SandboxBackend.execute`.
There is no separate Skill process launcher or package loader. Plugins supply the
ordinary Skill document contract and receive no runtime filesystem or service-container
access through this integration.

Materializations live below the Sandbox-owned `.oaw` directory, outside the workspace.
Portable paths and complete file trees are validated before writing; traversal,
case aliases, device paths, reparse points, symlinks and hard links are rejected.
Each execution exposes only its authorized bundle. Linux/WSL bind it read-only for
that command; Windows applies protected read/execute ACLs with explicit write denial,
then revokes them after the complete native process tree terminates, including failures
and cancellation. Parent traversal grants do not permit listing other cached bundles.
ACL failures fail closed. Ordinary commands clear stale Windows runtime grants before
launch, so cached paths never confer durable authority.

Revocation blocks subsequent submissions; it does not retroactively erase data already
read into a command or explicitly copied into its workspace. The current bundle bytes
are reused, replaced on edits, and removed with the Sandbox. Materializations are not
artifacts or portable state and are not copied into duplicated/summoned runtime instances.

The FastAPI process and Agent runtime may hold model credentials. Saved model credentials use authenticated encryption at rest, are never returned to the browser after submission, and are never stored in browser storage. The database contains only ciphertext. Its encryption key is stored separately with owner-only permissions and is additionally bound to the backend's Windows account through DPAPI on Windows. Sandbox processes are untrusted and never receive those values. Resource access crosses the boundary only through explicit graph relationships and the controlled workspace.

Operational logs may contain commands and program output, but they must not contain hidden model reasoning or copied host environment values.
