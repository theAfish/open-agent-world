# Sandbox security contract

The Sandbox card represents an operating-system security boundary. Windows, Linux and WSL2 runtimes refuse execution if their required isolation cannot be established. The backend never falls back to an unrestricted host subprocess.

## Runtime and workspace ownership

The card stores a requested runtime and an optional existing absolute host folder with read-only or read/write access. `SandboxManager` creates only a small binding record until first start. Runtime factories and capability probes belong to an instance-owned registry. The resolved runtime is pinned before execution and survives restarts; working-folder changes require a stopped runtime and participate in the normal node lifecycle transaction/rollback.

Application-owned metadata, temporary data and resource attachments remain separate from external folders. No destroy or unbind operation recursively removes an external folder. Commands in a writable external folder modify real data; card deletion and canvas undo do not roll back those edits. Drive roots, home roots, application data and path traversal are rejected; each platform additionally validates its native filesystem restrictions. Captured Legion templates exclude host folder bindings.

Discovery probes are cached for the application lifetime so status polling does not repeatedly wake WSL distributions. Explicit Refresh checks prerequisites again. No distribution, Docker daemon, package set or VM image is installed automatically, and WSL global settings are not changed. The WSL bridge uses a fixed trusted Python bootstrap with structured stdin messages and an allowlisted environment. Linux commands pass only to the Linux isolation backend. No WSL-wide shutdown is used.

## Linux and WSL2 controls

- Bubblewrap exposes a minimal root with read-only system tools, a selected `/workspace`, private temporary/home directories and only graph-authorized resource bind mounts.
- User, mount, PID and network namespaces isolate execution. Host homes, application credentials, `/mnt/c`, `/run/WSL`, desktop sockets and service sockets are absent unless a specific ordinary project folder is explicitly selected.
- Seccomp denies network socket creation/connections and privileged kernel operations, including Windows-interop socket access from WSL.
- Every command joins a delegated cgroup-v2 subtree before untrusted work begins, with memory, swap and process limits checked by the trusted launcher. Full-tree termination and timeout cleanup use the runtime-owned cgroup.
- Failure to provide the required namespace, seccomp or cgroup controls makes the runtime unavailable. A Linux process sandbox shares its host Linux kernel; it is not a separate-kernel VM.

`SandboxInfo.workspace` and `resources_path` are paths inside the selected execution environment. `workspace_path` is the user's host binding. An attachment is found at `resources_path / attachment.relative_path`; the inspect capability returns the concrete path. Both terminal commands and agent argv use the selected runtime rather than the server OS.

## Windows controls

- A unique AppContainer or LPAC identity is created for each Sandbox.
- The managed workspace receives only the ACL entries required by that identity.
- Read-only and read/write attachments receive distinct grants. A read-only hard link has inheritance protected before the package's inherited workspace write grant is removed and replaced with read access.
- No Internet, private-network, or server capability is granted.
- The child receives a small allowlisted environment; credentials, tokens, SSH variables, cloud variables, and the application environment are not inherited.
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

The FastAPI process and Agent runtime may hold model credentials. Sandbox processes are untrusted and never receive those values. Resource access crosses the boundary only through explicit graph relationships and the controlled workspace.

Operational logs may contain commands and program output, but they must not contain hidden model reasoning or copied host environment values.
