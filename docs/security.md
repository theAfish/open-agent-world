# Sandbox security contract

The Sandbox card represents an operating-system security boundary, not a working-directory convention. Its implementation is Windows-only for this POC and fails closed anywhere that boundary cannot be established.

## Required controls

- A unique AppContainer or LPAC identity is created for each Sandbox.
- The managed workspace receives only the ACL entries required by that identity.
- Read-only and read/write attachments receive distinct grants. A read-only hard link has inheritance protected before the package's inherited workspace write grant is removed and replaced with read access.
- No Internet, private-network, or server capability is granted.
- The child receives a small allowlisted environment; credentials, tokens, SSH variables, cloud variables, and the application environment are not inherited.
- The process is assigned to a Job Object before it can execute untrusted work.
- Kill-on-close, command timeout, process-count, and memory limits apply to the entire Job Object.
- Stop and destroy terminate the complete process tree.
- Commands run headlessly and cannot request an interactive desktop.

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
