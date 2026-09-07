"""Trusted, command-scoped slirp4netns launcher.

The outer user namespace owns an nftables network namespace. Bubblewrap creates
a *child* user namespace and inherits only this filtered network, never the host
network. No workload has CAP_NET_ADMIN in the namespace owning the firewall.
The source below is frozen into the existing cgroup guard's structured payload;
it is never loaded from a user-editable workspace during execution.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path


# IANA non-public IPv4 space, including loopback, link-local, CGNAT, test nets,
# multicast and reserved space. Block entire special-use ranges conservatively.
NON_PUBLIC_IPV4 = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
)
PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")
NETWORK_ERROR_MARKER = "OAW_SANDBOX_NETWORK_SETUP:"


def public_egress_rules(host_addresses: list[str]) -> str:
    """Produce a closed IPv4 TCP/UDP filter; host addresses are numeric only."""
    addresses = {str(ipaddress.IPv4Address(value)) + "/32" for value in host_addresses}
    networks = ipaddress.collapse_addresses(
        ipaddress.IPv4Network(value) for value in (*NON_PUBLIC_IPV4, *addresses)
    )
    blocked = ", ".join(str(value) for value in networks)
    return "\n".join((
        "table inet oaw_egress {",
        " set protected_ipv4 { type ipv4_addr; flags interval; elements = { " + blocked + " }; }",
        " chain output { type filter hook output priority 0; policy drop;",
        "  meta nfproto ipv4 ip daddr @protected_ipv4 reject",
        "  meta nfproto ipv4 meta l4proto { tcp, udp } accept",
        " }",
        " chain input { type filter hook input priority 0; policy drop;",
        "  meta nfproto ipv4 ct state established,related accept",
        " }",
        " chain forward { type filter hook forward priority 0; policy drop; }",
        "}",
    ))


def network_prerequisites() -> tuple[dict[str, str], str | None]:
    binaries: dict[str, str] = {}
    for name in ("slirp4netns", "nft", "ip", "unshare"):
        for directory in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
            path = Path(directory) / name
            if path.is_file():
                binaries[name] = str(path)
                break
    missing = [name for name in ("slirp4netns", "nft", "ip", "unshare") if name not in binaries]
    if not ca_bundle():
        missing.append("ca-certificates")
    if missing:
        return binaries, ("Missing Linux networking components: " + ", ".join(missing)
            + ". On Debian/Ubuntu install: sudo apt-get install slirp4netns nftables iproute2 util-linux ca-certificates. Offline execution remains available.")
    return binaries, None


def ca_bundle() -> str | None:
    for name in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if Path(name).is_file():
            return str(Path(name).resolve())
    return None


NETWORK_CHILD = r'''
import base64, ctypes, json, os, subprocess, sys
try:
    data = json.loads(base64.b64decode(sys.argv[1]))
    if os.readlink('/proc/self/ns/net') == data['host_netns']:
        raise RuntimeError('private network namespace was not established')
    result = subprocess.run([data['binaries']['nft'], '-f', '-'],
        input=data['rules'], text=True, capture_output=True, timeout=5,
        env={'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8'})
    if result.returncode:
        raise RuntimeError('cannot enforce public egress firewall: ' + result.stderr[:1200])
    os.write(data['configured_fd'], b'1')
    os.close(data['configured_fd'])
    if os.read(data['admit_fd'], 1) != b'1':
        raise RuntimeError('network supervisor disconnected before readiness')
    os.close(data['admit_fd'])
    command = data['command']
    if command[0] != '/usr/bin/bwrap' or '--unshare-all' not in command:
        raise RuntimeError('network helper only admits bubblewrap workloads')
    # --share-net applies ONLY to the private filtered namespace created above.
    # The workload's new child userns cannot administer its parent's netns.
    offset = command.index('--unshare-all') + 1
    command[offset:offset] = ['--share-net']
    os.execve(command[0], command, {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
except BaseException as exc:
    print('OAW_SANDBOX_NETWORK_SETUP: ' + str(exc), file=sys.stderr, flush=True)
    sys.exit(125)
'''


NETWORK_SUPERVISOR = r'''
import ipaddress, select, signal, subprocess, time
children = []
fds = []
supervisor_pid = os.getpid()
def parent_death():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != supervisor_pid:
        os._exit(125)
def pipe():
    pair = os.pipe()
    fds.extend(pair)
    return pair
def close(fd):
    if fd in fds:
        os.close(fd)
        fds.remove(fd)
def ready(fd, label):
    if not select.select([fd], [], [], 5)[0] or os.read(fd, 1) != b'1':
        raise RuntimeError(label + ' did not become ready')
try:
    binaries = data['network_binaries']
    addresses = subprocess.run([binaries['ip'], '-j', '-4', 'address', 'show'],
        capture_output=True, text=True, timeout=3, check=True,
        env={'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8'})
    host_addresses = [str(ipaddress.IPv4Address(address['local']))
        for link in json.loads(addresses.stdout) for address in link.get('addr_info', [])
        if address.get('family') == 'inet']
    if not host_addresses:
        raise RuntimeError('host IPv4 addresses could not be enumerated')
    networks = ipaddress.collapse_addresses(ipaddress.IPv4Network(value)
        for value in data['non_public_ipv4'] + [value + '/32' for value in host_addresses])
    rules = data['network_rules'].replace('HOST_PROTECTED_NETWORKS', ', '.join(str(value) for value in networks))
    resolver = os.memfd_create('oaw-sandbox-resolver', 0)
    fds.append(resolver)
    os.write(resolver, data['resolver'].encode())
    os.lseek(resolver, 0, os.SEEK_SET)
    command[1:1] = ['--ro-bind-data', str(resolver), '/etc/resolv.conf']
    configured_r, configured_w = pipe()
    admit_r, admit_w = pipe()
    child_payload = base64.b64encode(json.dumps({
        'binaries': binaries, 'rules': rules, 'command': command,
        'host_netns': os.readlink('/proc/self/ns/net'),
        'configured_fd': configured_w, 'admit_fd': admit_r,
    }).encode()).decode()
    child = subprocess.Popen([binaries['unshare'], '--user', '--map-root-user', '--net',
        '--', sys.executable, '-I', '-c', data['network_child'], child_payload],
        stdin=subprocess.DEVNULL, pass_fds=(descriptor, resolver, configured_w, admit_r),
        env={'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8'}, preexec_fn=parent_death)
    children.append(child)
    close(configured_w)
    close(admit_r)
    ready(configured_r, 'public egress firewall')
    close(configured_r)
    slirp_ready_r, slirp_ready_w = pipe()
    slirp_exit_r, slirp_exit_w = pipe()
    helper = subprocess.Popen([binaries['slirp4netns'], '--configure', '--disable-host-loopback',
        '--enable-sandbox', '--enable-seccomp', '--mtu=1500',
        '--ready-fd=' + str(slirp_ready_w), '--exit-fd=' + str(slirp_exit_r),
        str(child.pid), 'tap0'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        pass_fds=(slirp_ready_w, slirp_exit_r),
        env={'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8'}, preexec_fn=parent_death)
    children.append(helper)
    close(slirp_ready_w)
    close(slirp_exit_r)
    ready(slirp_ready_r, 'slirp4netns')
    close(slirp_ready_r)
    if helper.poll() is not None or child.poll() is not None:
        raise RuntimeError('network helper exited before admitting workload')
    os.write(admit_w, b'1')
    close(admit_w)
    close(resolver)
    while child.poll() is None:
        if helper.poll() is not None:
            raise RuntimeError('slirp4netns exited while the workload was running')
        time.sleep(0.02)
    result = child.returncode
except BaseException as exc:
    print('OAW_SANDBOX_NETWORK_SETUP: ' + str(exc), file=sys.stderr, flush=True)
    result = 125
finally:
    for fd in list(fds):
        close(fd)
    for process in reversed(children):
        if process.poll() is None:
            process.kill()
        process.wait()
os._exit(result if result >= 0 else 128 - result)
'''


def network_payload() -> dict[str, object]:
    binaries, reason = network_prerequisites()
    if reason:
        from .models import SandboxSecurityError
        raise SandboxSecurityError(reason)
    rules = public_egress_rules([])
    original = ", ".join(str(value) for value in ipaddress.collapse_addresses(
        ipaddress.IPv4Network(value) for value in NON_PUBLIC_IPV4))
    return {
        "network_binaries": binaries,
        "network_child": NETWORK_CHILD,
        "network_supervisor": NETWORK_SUPERVISOR,
        "network_rules": rules.replace(original, "HOST_PROTECTED_NETWORKS"),
        "non_public_ipv4": list(NON_PUBLIC_IPV4),
        "resolver": "".join(f"nameserver {value}\n" for value in PUBLIC_RESOLVERS)
            + "options timeout:2 attempts:2\n",
    }
