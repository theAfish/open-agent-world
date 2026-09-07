# Opt-in Sandbox networking

Networking defaults to **Disabled**. Stop the Sandbox, select **Enabled** in its
existing settings, save, and start it. Manual commands, Agent commands, Skill
scripts and connectivity diagnostics all use the same saved execution policy.
The selected runtime remains pinned. A missing networking prerequisite never
causes an automatic runtime switch or disables an otherwise usable offline runtime.

## Backend policies

| Backend | Enabled implementation | Private and host access |
| --- | --- | --- |
| Windows | AppContainer `internetClient` plus fixed per-profile Windows Filtering Platform deny rules, with native DNS and certificate trust | Explicit denial of non-public IPv4, all host interfaces and IPv6, independent of Windows network-profile classification. No private/server capability or loopback exemption. |
| Native Linux | slirp4netns, a private network namespace, nftables public IPv4 egress filter, retained seccomp and cgroup-v2 controls | Non-public IPv4 ranges and all enumerated host IPv4 interface addresses denied; IPv6 denied; virtual gateway and DNS forwarding aliases denied. |
| WSL2 | The same Linux implementation, invoked through the existing frozen-source structured worker transport | Same Linux policy, plus retained WSL interop and host-filesystem exclusions. |

Enabled execution supports normal hostname resolution, certificate-verified
HTTPS, and outbound TCP independent of HTTP proxy environment variables. Linux
also permits public IPv4 UDP. Inbound forwarding and private-network access are
not configurable policies in this release. All bundled public-egress implementations
currently reject IPv6.

Windows keeps the same per-Sandbox identity, workspace and Skill ACLs, minimal
environment, Job Object limits and whole-tree cleanup. Enabled admission checks
the required Windows services and rejects an existing exemption for that
AppContainer profile. A narrow elevated broker installs only fixed deny rules
scoped to that AppContainer. It never disables the firewall, adds allow rules or
changes loopback exemptions. The application itself stays unelevated.
The [Windows capability contract](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/app-capability-declarations)
distinguishes client Internet access from private and server access; explicit WFP
destination blocks additionally prevent network-profile classification from
granting private destinations.

On Linux the trusted launcher first verifies the command's cgroup limits, creates
an outer user/network namespace, installs the egress filter, starts slirp4netns,
and waits for its ready descriptor before admitting bubblewrap. Bubblewrap creates
a child user namespace and inherits only that filtered network namespace. The
workload cannot administer the namespace that owns the filter. Seccomp retains
the denial of privileged kernel operations, further namespaces, Unix-domain socket
connections, raw/packet/netlink sockets and IPv6. No host service socket is mounted.

The slirp virtual gateway and DNS aliases are blocked by the egress filter, in
addition to `--disable-host-loopback`. This addresses the
[documented DNS-forwarding alias caveat](https://github.com/rootless-containers/slirp4netns/blob/master/slirp4netns.1.md).
A command-local read-only resolver file uses public DNS servers `1.1.1.1` and
`8.8.8.8`. Only the distribution's CA bundle is bound read-only; host `/etc`, DNS
credentials and management credentials are not exposed. TLS verification stays on.

## Prerequisites

Windows requires its Base Filtering Engine (`BFE`), Windows Defender Firewall
service (`MpsSvc`) and DNS Client (`Dnscache`), plus the OAW network-policy broker.
Discovery reports missing components without disabling offline commands. Enable
required services through the host's normal administrative process.

Start the narrow broker explicitly from a trusted installation in an elevated
terminal, from the repository root:

```powershell
backend/.venv/Scripts/python -m backend.sandbox.windows_network_broker
```

Only the broker needs elevation. Its local named pipe accepts fixed
`probe`/`ensure`/`release` operations for canonical OAW AppContainer names, with
bounded JSON messages and no command, file, destination or arbitrary rule input.
Windows peer-token checks reject AppContainer callers; the unelevated client
verifies an elevated broker belonging to the same Windows account. The pipe has
an explicit owner/admin/System DACL and denies low-integrity writes. The broker
loads its trusted policy code before it begins accepting requests. Restart it
after updating the implementation; never launch it from untrusted workspace code.

Per-profile hard deny filters are installed atomically before enabled process
creation. They persist through broker exit and BFE restart, so helper failure
cannot remove restrictions from a running workload. Their ownership is recorded
in the Sandbox manifest. Destroy removes them only after Job termination; if the
broker is unavailable, deletion retains the manifest/profile and deny rules so
cleanup can be retried after restarting the broker. Stop does not remove the
Sandbox's security policy. There is no automatically installed Windows service.

Linux/WSL offline prerequisites remain bubblewrap, libseccomp, Python and a
systemd user session with delegated cgroup-v2 memory/pids limits. Networking adds
slirp4netns, nftables, iproute2, util-linux and trusted CA certificates. On Debian
or Ubuntu, install explicitly in the runtime's distribution:

```sh
sudo apt-get install --no-install-recommends slirp4netns nftables iproute2 util-linux ca-certificates
```

The kernel must permit nested user/network namespaces and nftables in the private
namespace. Refresh the Sandbox runtime list after installation. Discovery probes
offline availability separately from networking prerequisites and network setup.
It does not contact an external service to declare prerequisites available.

## Host management protection

The API accepts numeric loopback socket peers by default and independently
rejects unauthenticated interface/gateway/public/NAT callers. HTTP and WebSocket
ingress share that rule. Bundled ASGI launchers use `--no-proxy-headers`; Vite checks
the original socket peer before proxying, including WebSocket upgrades.

An explicitly configured remote integration can use the host-private
`OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN` with a Bearer header. Custom reverse proxies
must authenticate callers before forwarding and preserve that authority; they
must never turn arbitrary remote clients into unauthenticated local callers.
Forwarding headers do not authenticate. Do not add this token to a Sandbox
environment, environment profile, mount or command. See [trust zones](security.md#trust-zones).

## Lifecycle and diagnostics

The Linux network launcher, slirp process, namespace descriptors and workload all
belong to the existing command cgroup. Parent-death signals, an exit descriptor,
bounded readiness waits and the existing cgroup termination path cover setup
failure, cancellation, timeout, stop and interrupted transport. There is no
long-lived networking daemon or shared mutable forwarding configuration.

Runtime discovery exposes `supported_network_modes`, `network_available`,
`network_status` and an actionable `network_reason`. It distinguishes an
unimplemented backend, missing components and failed isolated setup. An enabled
command with a setup failure raises `network_setup_failed`; it is never retried
outside isolation. A destination failure is an ordinary command result.

**Test connectivity** invokes curl inside this same Sandbox, with its saved policy,
configuration, resource limits and command history. It ignores curl startup files
and proxy settings, verifies certificates and reports DNS failure, certificate
verification failure, connection failure, HTTP authentication refusal and HTTP
errors separately. A successful host-helper request is never used as connectivity
proof. The Linux resolver requires reachable public DNS; environments blocking
both configured resolvers need a separately designed DNS policy.

## Real-runtime acceptance

Networking acceptance is explicitly opted in; skipped native tests are not
positive evidence. Run from the repository root with the backend Python:

```powershell
$env:OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS='1'
$env:OAW_TEST_NETWORK_RUNTIME='windows'
backend/.venv/Scripts/python -m pytest backend/tests/test_windows_network.py backend/tests/test_sandbox_network_contract.py

$env:OAW_TEST_WSL_DISTRO='Ubuntu'
$env:OAW_TEST_NETWORK_RUNTIME='wsl:Ubuntu'
backend/.venv/Scripts/python -m pytest backend/tests/test_linux_networking.py backend/tests/test_sandbox_network_contract.py
```

On a native Linux host, set `OAW_TEST_LINUX_NETWORK=1` and
`OAW_TEST_NETWORK_RUNTIME=linux`, using `backend/.venv/bin/python` for the same two
Linux/contract files. Windows-specific tests are separate.

The tests use `example.com` for verified HTTPS and `ssh.github.com:443` for an SSH
identification exchange over non-HTTP TCP without logging in. These are external
service dependencies, not locally owned endpoints. `OAW_TEST_HTTPS_URL` can select
a controlled public HTTPS endpoint for the Windows and API workflow tests.
Host-denial tests use controlled listeners with successful host-side controls.

The API workflow exercises offline denial, Stop/Save/Start enablement, manual and
Agent commands, an Agent-invoked Skill, diagnostics, cancellation, a rejected live
policy edit and returning both manual and Skill execution to offline. Backend
native tests cover protocol traffic, host/IPv6 denial and retained isolation.

## Validation snapshot: 2026-09-08

| Backend | Real tests executed | Remaining verification |
| --- | --- | --- |
| Ubuntu WSL2 | Public DNS, verified HTTPS, non-HTTP SSH TCP exchange, manual/Agent/Skill/diagnostic policy routing, offline return, controlled host listener denial, alias/IPv6/Unix/raw denial, read-only Skill and credential isolation, memory limits, setup failure and cancellation with observed helper/namespace/cgroup cleanup | Public tests depend on external DNS, example.com and ssh.github.com. |
| Native Linux | Implementation shared with and exercised through WSL | No standalone Linux host was available; native Linux remains unverified. |
| Windows AppContainer | Retained offline filesystem/Skill/environment/Job/cancellation checks; real SID equivalence; an earlier capability-only prototype also completed DNS, verified HTTPS and SSH TCP | Final AppContainer + persistent WFP + elevated broker path is **not yet natively verified**. Administrator consent for the broker is pending. Earlier prototype connectivity is not final policy validation. |

Executed checks (overlapping suites are not added together):

- Whole default backend suite: **483 passed, 19 skipped**. Skips include explicitly
  opted-in native acceptance; this total is not an OS-isolation claim.
- Combined WSL networking/contract/Linux tests: **47 passed, 1 skipped**. The skipped
  old workspace scenario was then covered by the three existing real WSL Skill,
  environment and workspace scenarios: **3 passed, 1 skipped** (the separate WSL
  network flag was unset for that second selection).
- Retained Windows native/Skill/environment/workspace suites: **124 passed,
  1 skipped** (the WSL-specific scenario).
- Final focused Windows policy/broker suite: **48 passed, 7 skipped**. It includes
  an additional native lifecycle test, added after the whole-suite run, which
  reads all 17 owned filter keys during an enabled workload, verifies persistent
  hard-block flags and checks their removal after Sandbox destruction. This test
  awaits the same administrator consent as the other final native networking cases.
- Frontend: **173 passed** across 28 files; production TypeScript/Vite build passed.
- Browser: **4 passed** across Sandbox card, execution configuration and equipment
  scenarios. Sandbox card/runtime/file responses in that browser scenario are
  mocked; the separate API networking acceptance above uses real isolation.
- `git diff --check` passed. Existing dependency-deprecation and Vite bundle-size
  warnings remain.

The existing Ubuntu WSL distribution received `slirp4netns` 1.0.1 and its
`libslirp0` dependency through an approved package install. No distribution,
global firewall/sysctl policy, loopback exemption or Windows service was installed.
The first public resolver was unavailable on this network; the second resolved
successfully. Temporary controlled listeners and test profiles were cleaned up.

After approving Windows administrator consent, rerun the native Windows command
above and the API workflow with `OAW_TEST_NETWORK_RUNTIME=windows`. The private-peer
negative test additionally accepts `OAW_TEST_PRIVATE_URL`: provide a controlled
reachable private HTTP listener and first verify its response from the host. The
test requires host success, Sandbox refusal and no host state change. This approval
is a Windows OS requirement for the scoped WFP writes, not an application API
permission or a reason to report Windows validation as complete.
