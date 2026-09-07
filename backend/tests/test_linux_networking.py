from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from pathlib import Path

import pytest

from backend.sandbox.linux import LinuxSandboxBackend, _SERVICE_GUARD
from backend.sandbox.linux_network import (
    NETWORK_CHILD, NETWORK_SUPERVISOR, NON_PUBLIC_IPV4, public_egress_rules,
)
from backend.sandbox.materialization import RuntimeBundle, RuntimeMount
from backend.sandbox.models import ResourceAccess, SandboxLimits, SandboxNetworkError, SandboxValidationError
from backend.sandbox.wsl import WslSandboxBackend, wsl_command


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "10.0.2.2", "10.0.2.3",
    "100.100.100.200", "169.254.169.254", "172.20.0.1", "192.168.1.2",
    "198.18.1.1", "224.0.0.1", "255.255.255.255"])
def test_public_egress_blocks_private_metadata_gateway_and_dns_aliases(address: str) -> None:
    assert any(ipaddress.IPv4Address(address) in ipaddress.IPv4Network(network)
        for network in NON_PUBLIC_IPV4)


def test_public_egress_protects_even_public_host_addresses_and_rejects_injection() -> None:
    rules = public_egress_rules(["8.8.8.8"])
    assert "8.8.8.8/32" in rules
    assert rules.index("ip daddr @protected_ipv4 reject") < rules.index("meta l4proto { tcp, udp } accept")
    assert "meta nfproto ipv4" in rules and "policy drop" in rules
    with pytest.raises(ValueError):
        public_egress_rules(["8.8.8.8 }; flush ruleset"])


def test_network_sources_compile_and_workload_only_enters_filtered_namespace() -> None:
    for source in (NETWORK_CHILD, NETWORK_SUPERVISOR, _SERVICE_GUARD):
        compile(source, "<trusted-network-launcher>", "exec")
    assert "'--user', '--map-root-user', '--net'" in NETWORK_SUPERVISOR
    assert NETWORK_SUPERVISOR.index("ready(configured_r") < NETWORK_SUPERVISOR.index("subprocess.Popen([binaries['slirp4netns']")
    assert NETWORK_SUPERVISOR.index("ready(slirp_ready_r") < NETWORK_SUPERVISOR.index("os.write(admit_w")
    assert "command[0] != '/usr/bin/bwrap'" in NETWORK_CHILD


@pytest.mark.asyncio
async def test_missing_network_dependency_does_not_disable_offline_probe(monkeypatch) -> None:
    import backend.sandbox.linux as module
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module, "network_prerequisites", lambda: ({}, "Missing slirp4netns"))
    assert await LinuxSandboxBackend.probe_network() == (False, "Missing slirp4netns")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", 1, [], None])
async def test_backend_network_policy_requires_boolean(tmp_path, value) -> None:
    for backend in (LinuxSandboxBackend(tmp_path), WslSandboxBackend(tmp_path, distribution="test")):
        with pytest.raises(SandboxValidationError, match="boolean"):
            await backend.execute("absent", ["/bin/true"], execution_policy={"network_enabled": value})


async def _network_resources(distro: str | None) -> dict:
    """Read actual helper processes, network namespace handles and cgroups."""
    script = r'''
import json, os, pathlib, subprocess
helpers = {}
for item in pathlib.Path('/proc').iterdir():
    if not item.name.isdigit(): continue
    try:
        args = (item/'cmdline').read_bytes().split(b'\0')
        if args[0].endswith(b'/slirp4netns') and any(arg.startswith(b'--ready-fd=') for arg in args):
            helpers[item.name] = os.readlink('/proc/' + args[-3].decode() + '/ns/net')
    except (OSError, ValueError, IndexError): pass
process = subprocess.run(['/usr/bin/systemctl','--user','list-units','--all','--plain','--no-legend','oaw-sandbox-*.scope'],
    capture_output=True,text=True,check=True,env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':'/run/user/'+str(os.getuid())})
print(json.dumps({'helpers':helpers,'scopes':sorted(line.split()[0] for line in process.stdout.splitlines() if line.strip())}))
'''
    prefix = wsl_command(distro)[:4] if distro else []
    process = await asyncio.create_subprocess_exec(*prefix, "/usr/bin/python3", "-I", "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **({"creationflags": 0x08000000} if os.name == "nt" else {}))
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr
    return json.loads(stdout)


async def _assert_resources_reclaimed(distro: str | None, before: dict) -> None:
    for _ in range(30):
        after = await _network_resources(distro)
        if (set(after["helpers"]) <= set(before["helpers"])
            and set(after["scopes"]) <= set(before["scopes"])):
            return
        await asyncio.sleep(.1)
    assert after == before


_CONNECTIVITY = r'''
import json, socket, ssl, struct, urllib.request
host = 'example.com'
answers = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
assert answers
with urllib.request.urlopen('https://' + host, context=ssl.create_default_context(), timeout=12) as response:
    assert response.status == 200
# Exchange SSH protocol identification over raw TCP. GitHub's SSH endpoint is
# an external service dependency; this is not an HTTP request or proxy client.
with socket.create_connection(('ssh.github.com', 443), timeout=8) as client:
    client.sendall(b'SSH-2.0-OAW-Network-Acceptance\r\n')
    assert client.recv(256).startswith(b'SSH-2.0-')
print(json.dumps({'dns': True, 'verified_https': True, 'non_http_tcp': True}))
'''

_DENIED = r'''
import errno, json, os, socket
for address in ('127.0.0.1', '0.0.0.0', '10.0.2.2', '10.0.2.3', '192.168.1.1', '169.254.169.254', '172.16.0.1', '100.100.100.200'):
    for port in (53, 8000, 8017, 2375):
        try:
            with socket.create_connection((address,port),timeout=.25): pass
        except OSError: pass
        else: raise AssertionError((address,port))
for family,kind in ((socket.AF_UNIX,socket.SOCK_STREAM),(socket.AF_INET6,socket.SOCK_STREAM),
                    (socket.AF_NETLINK,socket.SOCK_RAW),(socket.AF_INET,socket.SOCK_RAW)):
    try: socket.socket(family,kind)
    except OSError as exc: assert exc.errno == errno.EPERM
    else: raise AssertionError((family,kind))
assert not any(key in os.environ for key in ('WSL_INTEROP','WSLENV','AWS_SECRET_ACCESS_KEY','OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN'))
assert not os.path.exists('/mnt/c') and not os.path.exists('/run/WSL')
assert not os.path.exists('/root/.ssh') and not os.path.exists('/etc/shadow')
print('boundaries-enforced')
'''


@pytest.mark.asyncio
@pytest.mark.skipif(not (os.environ.get("OAW_TEST_WSL_DISTRO") or os.environ.get("OAW_TEST_LINUX_NETWORK")),
    reason="opt in with OAW_TEST_WSL_DISTRO or OAW_TEST_LINUX_NETWORK; requires public DNS, example.com HTTPS and ssh.github.com:443")
async def test_real_linux_or_wsl_public_network_tls_tcp_isolation_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN", "host-only-network-test-management-credential")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-only-network-test-provider-credential")
    distro = os.environ.get("OAW_TEST_WSL_DISTRO")
    kind = WslSandboxBackend if distro else LinuxSandboxBackend
    available, reason = await kind.probe_network(distro) if distro else await kind.probe_network()
    assert available, reason
    backend = kind(tmp_path / "managed", **({"distribution": distro} if distro else {}),
        limits=SandboxLimits(memory_bytes=128 * 1024 * 1024, active_process_limit=20))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    enabled = {"network_enabled": True}
    before = await _network_resources(distro)
    await backend.create("network")
    try:
        await backend.configure("network", workspace_path=str(workspace), workspace_access=ResourceAccess.READ_WRITE)
        await backend.start("network")
        offline = await backend.execute("network", ["/usr/bin/python3", "-c", "import socket; socket.create_connection(('1.1.1.1',53),timeout=1)"])
        assert offline.exit_code != 0 and "Operation not permitted" in offline.stderr
        result = await backend.execute("network", ["/usr/bin/python3", "-I", "-c", _CONNECTIVITY], execution_policy=enabled, timeout_seconds=30)
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == {"dns": True, "verified_https": True, "non_http_tcp": True}
        result = await backend.execute("network", ["/usr/bin/python3", "-I", "-c", _DENIED], execution_policy=enabled, timeout_seconds=15)
        assert result.exit_code == 0, result.stderr
        assert "boundaries-enforced" in result.stdout
        # A listener bound to all host interfaces establishes a real positive
        # baseline before testing both host loopback and non-loopback access.
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), 0,
            socket.AF_INET, socket.SOCK_STREAM) if not item[4][0].startswith("127.")})
        assert addresses, "A real non-loopback host IPv4 address is required"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("0.0.0.0", 0))
            listener.listen()
            listener.settimeout(1)
            port = listener.getsockname()[1]
            for address in ("127.0.0.1", *addresses):
                with socket.create_connection((address, port), timeout=2):
                    connection, _ = listener.accept()
                    connection.close()
            check = ("import socket\n"
                f"for address in {['127.0.0.1', *addresses]!r}:\n"
                " try:\n"
                f"  s=socket.create_connection((address,{port}),timeout=.4)\n"
                " except OSError: pass\n"
                " else: s.close(); raise AssertionError('host listener reached: '+address)\n"
                "print('live-host-services-blocked')\n")
            denial = await backend.execute("network", ["/usr/bin/python3", "-I", "-c", check], execution_policy=enabled)
            assert denial.exit_code == 0, denial.stderr
            listener.settimeout(.1)
            with pytest.raises(TimeoutError):
                listener.accept()
        script = _CONNECTIVITY + "\nfrom pathlib import Path\ntry: Path(__file__).write_text('changed')\nexcept OSError: print('skill-readonly')\nelse: raise AssertionError('mutable Skill')\n"
        mount = RuntimeMount(RuntimeBundle("network-skill", (("script.py", script.encode()),)), 1)
        skill = await backend.execute("network", ["/usr/bin/python3", "script.py"], runtime_mount=mount,
            execution_policy=enabled, timeout_seconds=30)
        assert skill.exit_code == 0 and "skill-readonly" in skill.stdout, skill.stderr
        # Same execution-policy transition used after manager Stop/Save/Start.
        await backend.terminate("network")
        await backend.start("network")
        offline = await backend.execute("network", ["/usr/bin/python3", "-c", "import socket; socket.socket()"], execution_policy={"network_enabled": False})
        assert offline.exit_code != 0 and "Operation not permitted" in offline.stderr
        oom = await backend.execute("network", ["/usr/bin/python3", "-c", "a=bytearray(256*1024*1024)"], execution_policy=enabled, timeout_seconds=8)
        assert oom.exit_code != 0
        timeout = await backend.execute("network", ["/bin/sh", "-c", "(sleep 2; echo escaped > timed-out.txt) & wait"], execution_policy=enabled, timeout_seconds=.2)
        assert timeout.timed_out
        task = asyncio.create_task(backend.execute("network", ["/bin/sh", "-c", "(sleep 3; echo escaped > cancelled.txt) & wait"], execution_policy=enabled, timeout_seconds=15))
        await asyncio.sleep(.6)
        active_resources = await _network_resources(distro)
        assert set(active_resources["helpers"]) - set(before["helpers"]), active_resources
        await backend.terminate("network")
        assert (await task).cancelled
        await asyncio.sleep(3)
        assert not (workspace / "timed-out.txt").exists()
        assert not (workspace / "cancelled.txt").exists()
        await _assert_resources_reclaimed(distro, before)
        # Fail the actual userspace helper before readiness. The workload must
        # never be admitted and its real scope/namespace resources must vanish.
        import backend.sandbox.linux as linux_module
        import backend.sandbox.wsl as wsl_module
        original_payload = linux_module.network_payload
        def failed_payload():
            payload = original_payload()
            payload["network_binaries"]["slirp4netns"] = "/usr/bin/false"
            return payload
        with monkeypatch.context() as patch:
            if distro:
                suffix = ("\n_original_payload = network_payload\n"
                    "def network_payload():\n p = _original_payload()\n"
                    " p['network_binaries']['slirp4netns'] = '/usr/bin/false'\n return p\n")
                patch.setattr(wsl_module, "_WORKER_MODULES", tuple((name, source + suffix if name == "linux_network" else source)
                    for name, source in wsl_module._WORKER_MODULES))
            else:
                patch.setattr(linux_module, "network_payload", failed_payload)
            await backend.start("network")
            with pytest.raises(SandboxNetworkError, match="ready"):
                await backend.execute("network", ["/bin/sh", "-c", "echo escaped > setup-failure.txt"], execution_policy=enabled)
        assert not (workspace / "setup-failure.txt").exists()
        await _assert_resources_reclaimed(distro, before)
        # Interrupt trusted setup while it still owns a namespace and before
        # slirp readiness. Neither a command nor a capability probe may orphan
        # that namespace/cgroup through a cancelled WSL transport.
        def delayed_payload():
            payload = original_payload()
            payload["network_child"] = "import time; time.sleep(10)\n" + payload["network_child"]
            return payload
        with monkeypatch.context() as patch:
            if distro:
                suffix = ("\n_original_payload = network_payload\n"
                    "def network_payload():\n p = _original_payload()\n"
                    " p['network_child'] = 'import time; time.sleep(10)\\n' + p['network_child']\n return p\n")
                patch.setattr(wsl_module, "_WORKER_MODULES", tuple((name, source + suffix if name == "linux_network" else source)
                    for name, source in wsl_module._WORKER_MODULES))
            else:
                patch.setattr(linux_module, "network_payload", delayed_payload)
            await backend.start("network")
            setup = asyncio.create_task(backend.execute("network", ["/bin/sh", "-c", "echo escaped > cancelled-setup.txt"], execution_policy=enabled))
            await asyncio.sleep(.3)
            await backend.terminate("network")
            assert (await setup).cancelled
            assert not (workspace / "cancelled-setup.txt").exists()
            await _assert_resources_reclaimed(distro, before)
            probe = asyncio.create_task(kind.probe_network(distro) if distro else kind.probe_network())
            await asyncio.sleep(.3)
            probe.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probe
        await _assert_resources_reclaimed(distro, before)
    finally:
        await backend.destroy("network")
