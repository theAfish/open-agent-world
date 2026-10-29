# Hosted profile boundary (Issue #38, first POC slice)

> Historical record, not current product or deployment instructions. Verify against the current source before use.


Audit at `b218fa5`: the Sandbox Card already calls host APIs, and
`SandboxManager` already persists a pinned backend per Card. No second manager
is needed. `SandboxBackend.file_operation` is the portable file boundary;
`managed_workspace`, backups/relocation, resource mounts and runtime bundles
still have host-path / command-scoped-mount assumptions. Pack wheels execute in
the trusted host process. SQLite, event hubs, credentials, run managers and
plugin state belong to a single profile. A tenant header cannot isolate them.

The first hosted slice therefore runs **one independently stored OAW profile per
owner**, not several users in one database. `ProfileAccess` accepts only an
identity verified by an injected `IdentityProvider`, checks issuer + subject,
pins the owner and scope on disk, and propagates that scope through HTTP and
WebSockets. It never falls back to local socket trust. Names/emails are not IDs.
Use separate processes/instances and volumes for different users. The caller
must keep the single-writer profile lock and durable storage guarantees.

`create_app` accepts a service factory and `create_services` accepts a Sandbox
registry factory. Cloud composition can install only an approved remote runtime;
desktop defaults and Pack business logic do not need provider conditionals.
The adapter must reject unsupported mounts/policies before provider side effects.
Remote paths must never be represented as accessible host filesystem paths.

The optional `hosting` dependency group adds standard OIDC browser login (code
flow with PKCE, provider signature/issuer/audience/nonce/expiry validation).
Passwords stay with the IdP. Only verified identity claims and bounded expiry
enter an HttpOnly/SameSite cookie. Configure a stable session signing secret and
exact public origin; no forwarded header supplies authentication or redirects.
Logout removes the browser session, not the IdP session or already running jobs.
Signed sessions expire within one hour; immediate central revocation and
periodic authentication of an already-open WebSocket are not implemented.

Provider-specific work lives on a separate integration branch: provider
configuration, managed Sandbox SDK, container recipe and an explicit runbook.
Cloud hosting must restrict host administration / arbitrary Pack installation.
Only operator-approved Packs may run in the trusted host. The identity protocol
does not itself implement an identity service or secure arbitrary Pack code.

Remaining work: remote runtime-bundle mounts, resource writeback, full MatCreator
native-worker portability, dynamic user routing/provisioning, shared database
tenancy, multi-replica leases, and durable command reattachment. Cloud session
affinity is not a replacement for persistence. Reconnect must never resubmit an
uncertain external job. Ordinary local shutdown semantics remain unchanged.
