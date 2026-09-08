# Opt-in Sandbox networking

Networking defaults to **Disabled**. Stop the Sandbox, select **Enabled** in its
existing settings, save, and start it. Manual commands, Agent commands, Skill
scripts and connectivity diagnostics all use the same saved execution policy.
The selected runtime remains pinned. A missing networking prerequisite never
causes an automatic runtime switch or disables an otherwise usable offline runtime.

Saved **Enabled** is intent, not a connectivity result. Current Sandbox information
distinguishes missing prerequisites, setup failure and runtime readiness. For a
supported runtime, **Retry / Recheck** reaches the normal authorized Start path
even after cached prerequisite failure. Restore the prerequisite and retry without
changing policy or reloading the app. Settings **Refresh** explicitly rechecks
discovery and then refreshes Sandbox information; there is no discovery polling.

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
positive evidence. Keep the application, pytest and Sandbox workloads
unelevated. The Windows WFP lifecycle test uses an explicitly approved,
test-only elevated read-only auditor with a startup-frozen profile allowlist;
the test fails when the auditor is unavailable. The broker-loss test uses a
separate approved controller for a test-owned broker and an existing-process
native probe. Its IPC controls only its own child; replacing an existing broker
requires separate approval of its exact authenticated PID at helper startup.

### Windows setup

Use a trusted checkout and trusted backend Python, accessible to the same Windows
user before and after UAC elevation. Do not run the application, pytest or test
workloads as administrator. Run the following from the repository root in an
ordinary PowerShell, outside restricted tool tokens. The native probe requires
existing LLVM clang/lld and the Windows SDK libraries; verify the paths below
against your installation. Acceptance does not install these dependencies.

```powershell
$testPython = (Resolve-Path backend/.venv/Scripts/python.exe).Path
$testRepo = (Get-Location).Path
$testSession = [guid]::NewGuid().ToString('N')
$testOutput = Join-Path $testRepo ".outputs/windows-network-$testSession"
New-Item -ItemType Directory -Path $testOutput | Out-Null
$env:OAW_TEST_WFP_ROOT = Join-Path $testOutput 'managed'
$env:OAW_TEST_WFP_SESSION = $testSession
$env:OAW_TEST_BROKER_SESSION = $testSession
$env:OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS = '1'
$env:OAW_TEST_NETWORK_RUNTIME = 'windows'
$testProfiles = @(@'
import os; from pathlib import Path; from backend.sandbox.windows import WindowsSandboxBackend; b=WindowsSandboxBackend(Path(os.environ["OAW_TEST_WFP_ROOT"])); print("\n".join(b._identity(i) for i in ("filter-lifecycle","loss-graceful","admission-graceful","loss-abnormal","admission-abnormal")))
'@ | & $testPython -)
if ($LASTEXITCODE -ne 0 -or $testProfiles.Count -ne 5) { throw 'Profile calculation failed' }
$testProfiles | Set-Content -Encoding UTF8 (Join-Path $testOutput 'approved-profiles.txt')
```

`OAW_TEST_WFP_ROOT` is the absolute, fresh managed root shared by the WFP lifecycle
and broker-loss tests. Do not use an application data directory. The five names
above are the actual test Sandbox IDs; `_identity` derives their AppContainer
profiles from the resolved root and ID. Inspect and approve this list before
starting the auditor. Changing the root changes the profile keys and requires a
new allowlist. Sessions must be 32 lowercase hexadecimal characters. The auditor
freezes at most eight profiles and exposes only deterministic filter inspection;
it does not execute requests, enumerate unrelated objects or mutate filters.

```powershell
$testClang = 'C:\Program Files\LLVM\bin\clang.exe'
$testLink = 'C:\Program Files\LLVM\bin\lld-link.exe'
$testSdk = 'C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64'
$testObject = Join-Path $testOutput 'probe.obj'
$env:OAW_TEST_NETWORK_PROBE_EXE = Join-Path $testOutput 'probe.exe'
& $testClang -target x86_64-pc-windows-msvc -O2 -ffreestanding -fno-stack-protector -Wall -Wextra -Werror -c backend/tests/windows_network_probe.c -o $testObject
if ($LASTEXITCODE -ne 0) { throw 'Probe compilation failed' }
& $testLink /entry:mainCRTStartup /subsystem:console /nodefaultlib $testObject "/libpath:$testSdk" kernel32.lib ws2_32.lib "/out:$env:OAW_TEST_NETWORK_PROBE_EXE"
if ($LASTEXITCODE -ne 0) { throw 'Probe linking failed' }
```

Supply a controlled remote RFC1918 IPv4 HTTP listener that the host can reach
directly. `OAW_TEST_PRIVATE_URL` must use a literal IPv4 address, not a hostname,
loopback or the Windows host's own interface. For example, on an independently
reachable private peer with Python, run the existing utility:
`python3 /trusted/checkout/backend/tests/private_network_peer.py --bind <peer-ipv4>`.
It prints its dynamically assigned URL and PID, serves only a fixed marker and
bounded `/evidence`, and exits after one hour. Set `OAW_TEST_PRIVATE_URL` to the
printed `/check` URL and optionally `OAW_TEST_PRIVATE_EVIDENCE_URL` to `/evidence`
on that same service. Keep its request log. Do not substitute an unreachable peer;
without one, the private denial and broker-loss acceptance items remain blocked.
Host controls in the tests explicitly disable proxies.

After approval of the read-only audit and broker-loss interruption, launch the
two separate helpers with UAC (hidden consoles, absolute scripts, isolated Python):

```powershell
$testAuditScript = (Resolve-Path backend/tests/windows_wfp_audit.py).Path
$testControlScript = (Resolve-Path backend/tests/windows_broker_test_control.py).Path
$testAuditArgs = @('-I', ('"{0}"' -f $testAuditScript), '--session', $testSession)
foreach ($testProfile in $testProfiles) { $testAuditArgs += @('--profile', $testProfile) }
$testAuditProcess = Start-Process -FilePath $testPython -ArgumentList $testAuditArgs -Verb RunAs -WindowStyle Hidden -PassThru
$testControlArgs = @('-I', ('"{0}"' -f $testControlScript), '--session', $testSession)
# Read-only identification; a returned PID is NOT permission to interrupt it.
@'
from backend.tests.windows_broker_test_control import broker_pid; print(broker_pid())
'@ | & $testPython -
# If a broker exists, stop here until its exact PID and interruption are approved.
# Only then append: $testControlArgs += @('--replace-pid', '<approved-current-pid>')
$testControlProcess = Start-Process -FilePath $testPython -ArgumentList $testControlArgs -Verb RunAs -WindowStyle Hidden -PassThru
@'
import os; from backend.tests.windows_acceptance_pipe import request; print(request(os.environ["OAW_TEST_BROKER_SESSION"], "BrokerTest", {"version":1,"operation":"status"}))
'@ | & $testPython -
```

UAC approval alone is not approval to interrupt another workload's broker. Without
`--replace-pid`, the controller refuses an existing broker; that startup check must
succeed before tests run. Never kill an arbitrary discovered process. Record both
helper PIDs and the returned broker PID. Keep the original ordinary shell for
pytest; both helpers authenticate same-user clients and reject AppContainers.
They are bounded to one hour/512 requests, so use a new approved session if needed.

Run individual incomplete scenarios before the combined suites, preserving every
result. The WFP test distinguishes absent keys from access denial/query errors and
audits actual flags, conditions, actions and layers during the workload. The loss
test parametrizes graceful and abnormal exit and continues connections in the
same AppContainer process while the auditor independently checks persistent rules.

```powershell
& $testPython -m pytest -vv backend/tests/test_windows_network.py -k owned_wfp --basetemp "$testOutput/pytest-wfp" -p no:cacheprovider -p no:faulthandler --junitxml="$testOutput/wfp.xml"
& $testPython -m pytest -vv backend/tests/test_windows_network.py -k private_peer --basetemp "$testOutput/pytest-private" -p no:cacheprovider --junitxml="$testOutput/private.xml"
& $testPython -m pytest -vv backend/tests/test_windows_broker_loss.py -k graceful --basetemp "$testOutput/pytest-graceful" -p no:cacheprovider -p no:faulthandler --junitxml="$testOutput/graceful.xml"
& $testPython -m pytest -vv backend/tests/test_windows_broker_loss.py -k abnormal --basetemp "$testOutput/pytest-abnormal" -p no:cacheprovider -p no:faulthandler --junitxml="$testOutput/abnormal.xml"
& $testPython -m pytest -vv -ra backend/tests/test_windows_network.py backend/tests/test_sandbox_network_contract.py backend/tests/test_windows_broker_loss.py --basetemp "$testOutput/pytest-final" -p no:cacheprovider -p no:faulthandler --junitxml="$testOutput/final.xml"
```

Inspect exit codes after each command; do not proceed on failure. The helper
clients append structured audit/loss/peer evidence beneath the managed root's
parent `evidence/` directory. Preserve console logs alongside XML, including
failed attempts. `no:faulthandler` only suppresses dumps for handled WFP missing-key
queries; it does not suppress test errors or change inspection assertions.

Tests destroy their own Sandboxes in cleanup. Before closing the auditor, verify
all approved keys are absent and preserve the result. Do not delete manifests or
profiles to hide failed cleanup: restore the broker and retry destruction through
the owning backend with the same root/ID, then audit again. Never remove unrelated
rules or stop BFE/firewall. On successful cleanup:

```powershell
@'
import os,json; from pathlib import Path; from backend.tests.windows_acceptance_pipe import request; root=Path(os.environ["OAW_TEST_WFP_ROOT"]).parent; profiles=(root/"approved-profiles.txt").read_text(encoding="utf-8-sig").splitlines(); results=[request(os.environ["OAW_TEST_WFP_SESSION"],"Audit",{"version":1,"profile":p}) for p in profiles]; (root/"cleanup-audit.json").write_text(json.dumps(results),encoding="utf-8"); assert all(r["status"]=="missing" and r["code"]==0x80320003 for s in results for r in s["filters"])
'@ | & $testPython -
if ($LASTEXITCODE -ne 0) { throw 'Cleanup audit failed; preserve evidence and helpers for recovery' }
@'
import os; from backend.tests.windows_acceptance_pipe import request; print(request(os.environ["OAW_TEST_BROKER_SESSION"],"BrokerTest",{"version":1,"operation":"finish"})); print(request(os.environ["OAW_TEST_WFP_SESSION"],"Audit",{"version":1,"close":True}))
'@ | & $testPython -
```

`finish` deliberately leaves the restored broker running while the controller
exits; record the returned PID and verify its authenticated readiness. Check that
both helper processes exit, stop only the private-peer process you started, and
retain evidence/build outputs. Unset the `OAW_TEST_*` and native opt-in variables
in the test shell or close that shell. Do not interrupt the restored broker as
part of frontend validation.

### Other runtimes

From the repository root with the backend Python:

```powershell
$env:OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS='1'
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

### Windows acceptance boundary

The Windows AppContainer path has been exercised with the elevated networking
broker, persistent Sandbox-scoped WFP deny rules, and ordinary unprivileged
application and workload processes. It covers offline restoration, verified
public HTTPS and non-HTTP TCP, manual/Agent/Skill policy routing, protected
host and private-network denial, broker access denial, graceful and abnormal
broker loss, and cleanup.

The private-peer test requires `OAW_TEST_PRIVATE_URL` to name a controlled,
reachable remote RFC1918 HTTP service and may set `OAW_TEST_PRIVATE_EVIDENCE_URL`
for server-side request evidence. The host controls disable proxy use; an
unreachable address, public endpoint or loopback listener is not acceptable.

Native Linux has not been exercised on a standalone Linux host. WSL acceptance
remains separately opt-in and depends on its installed distribution and public
test endpoints. Store per-run logs, XML, filter snapshots and cleanup evidence
under `.outputs/` or in CI artifacts rather than this documentation.
