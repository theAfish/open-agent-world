"""Trusted public-egress preparation for Apple Container workloads.

The ordinary workload image stays offline.  Enabled workloads use a separate
image with nftables, install an IPv4-only filter before receiving a command,
then irreversibly drop the setup capabilities and the root identity.  An image
is prepared explicitly on the Mac; discovery never builds images or downloads
packages as a side effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .linux_network import PUBLIC_RESOLVERS, public_egress_rules
from .models import SandboxNetworkError


_RECIPE = """FROM docker.io/library/python:3.12-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends nftables ca-certificates curl \\
    && rm -rf /var/lib/apt/lists/*
"""
NETWORK_IMAGE = "oaw-public-egress:" + hashlib.sha256(_RECIPE.encode()).hexdigest()[:12]
_INET = re.compile(r"^\s*inet\s+([0-9]+(?:\.[0-9]+){3})\b", re.MULTILINE)


# Executed as a trusted, short-lived root bootstrap *inside* a dedicated VM.
# No user command is read until nft has installed the complete filter.  The
# command worker then runs as the mounted files' owner with an empty capability
# bounding set and no-new-privileges, so it cannot alter the VM's firewall.
NETWORK_BOOTSTRAP_SOURCE = r'''
import ctypes, errno, json, os, subprocess, sys

try:
    settings = json.loads(sys.argv[1])
    if not isinstance(settings['uid'], int) or settings['uid'] <= 0:
        raise RuntimeError('workload must have a non-root user ID')
    result = subprocess.run(['/usr/sbin/nft', '-f', '-'],
        input=settings['rules'], text=True, capture_output=True, timeout=10,
        env={'PATH': '/usr/sbin:/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    if result.returncode:
        raise RuntimeError('cannot install public-egress filter: ' + result.stderr[:1200])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError('cannot prevent privilege regain')
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) != 0:  # PR_CAPBSET_DROP
            if ctypes.get_errno() != errno.EINVAL:
                raise RuntimeError('cannot drop capability bounding set')
    os.setgroups([])
    os.setgid(settings['gid'])
    os.setuid(settings['uid'])
    status = open('/proc/self/status', encoding='ascii').read()
    for field in ('CapInh', 'CapEff', 'CapPrm', 'CapBnd', 'CapAmb'):
        value = next((line.split(':', 1)[1].strip() for line in status.splitlines()
            if line.startswith(field + ':')), None)
        if value is None or int(value, 16):
            raise RuntimeError('network bootstrap retained Linux capabilities')
except BaseException as exc:
    print('OAW_SANDBOX_NETWORK_SETUP: ' + str(exc)[:1200], file=sys.stderr, flush=True)
    sys.exit(125)
exec(compile(sys.argv[2], '<oaw-container-worker>', 'exec'))
'''

_PROBE_SOURCE = r'''
import socket
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(('127.0.0.1', 0))
listener.listen(1)
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.settimeout(1)
try:
    client.connect(listener.getsockname())
except OSError:
    pass
else:
    raise RuntimeError('public-egress filter permits loopback TCP')
finally:
    client.close()
    listener.close()
print('oaw-network-ready', flush=True)
'''


def host_ipv4_addresses() -> list[str]:
    """Enumerate every host interface so its public addresses are denied too."""
    if sys.platform != "darwin":
        raise SandboxNetworkError("macOS public-egress policy requires a Mac")
    try:
        result = subprocess.run(["/sbin/ifconfig", "-a"], capture_output=True,
            text=True, timeout=5, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SandboxNetworkError(f"Cannot enumerate macOS host interfaces: {exc}") from exc
    addresses = {str(ipaddress.IPv4Address(value)) for value in _INET.findall(result.stdout)}
    if not addresses:
        raise SandboxNetworkError("Cannot enumerate macOS host IPv4 addresses")
    return sorted(addresses)


def network_bootstrap_arguments(uid: int, gid: int) -> str:
    import json
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise SandboxNetworkError("Apple Container public egress requires a non-root backend user")
    return json.dumps({"uid": uid, "gid": gid,
        "rules": public_egress_rules(host_ipv4_addresses())}, separators=(",", ":"))


def image_available(container_path: str) -> bool:
    try:
        result = subprocess.run([container_path, "image", "inspect", NETWORK_IMAGE],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare_network_image(container_path: str | None = None) -> str:
    """Build the fixed trusted image on an explicitly requested Mac setup run."""
    if sys.platform != "darwin":
        raise SandboxNetworkError("Apple Container networking requires macOS")
    executable = container_path or shutil.which("container")
    if not executable:
        raise SandboxNetworkError("Install Apple container and start its system service")
    if image_available(executable):
        return NETWORK_IMAGE
    with tempfile.TemporaryDirectory(prefix="oaw-network-image-") as directory:
        context = Path(directory)
        recipe = context / "Dockerfile"
        recipe.write_text(_RECIPE, encoding="utf-8")
        try:
            result = subprocess.run([executable, "build", "--tag", NETWORK_IMAGE,
                "--file", str(recipe), str(context)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxNetworkError(f"Cannot prepare Apple Container networking image: {exc}") from exc
        if result.returncode or not image_available(executable):
            detail = (result.stdout + result.stderr)[-4000:].decode("utf-8", "replace")
            raise SandboxNetworkError("Cannot prepare Apple Container networking image: " + detail[-1600:])
    return NETWORK_IMAGE


async def probe_network_image(container_path: str | None = None) -> tuple[bool, str | None]:
    if sys.platform != "darwin":
        return False, "Apple Container public egress requires macOS"
    executable = container_path or shutil.which("container")
    if not executable:
        return False, "Install Apple container and start its system service"
    try:
        settings = await asyncio.to_thread(network_bootstrap_arguments, 65534, 65534)
    except SandboxNetworkError as exc:
        return False, str(exc)
    if not await asyncio.to_thread(image_available, executable):
        return False, ("Prepare the fixed public-egress image on this Mac with: "
            "python -m backend.sandbox.macos_container_network prepare; then refresh Sandbox runtimes")
    command = [executable, "run", "--rm", "--network", "default",
        "--memory", "256M", "--uid", "0", "--gid", "0",
        "--cap-drop", "ALL", "--read-only", "--tmpfs", "/tmp"]
    for capability in ("NET_ADMIN", "SETPCAP", "SETUID", "SETGID"):
        command.extend(["--cap-add", capability])
    for resolver in PUBLIC_RESOLVERS:
        command.extend(["--dns", resolver])
    command.extend([NETWORK_IMAGE, "python3", "-I", "-u", "-c",
        NETWORK_BOOTSTRAP_SOURCE, settings, _PROBE_SOURCE])
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(*command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    except (OSError, TimeoutError, asyncio.CancelledError) as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, TimeoutError):
            raise SandboxNetworkError(
                f"Apple Container public-egress setup probe timed out: {exc}") from exc
        return False, f"Apple Container public-egress probe failed: {exc}"
    if process.returncode or b"oaw-network-ready" not in stdout.splitlines():
        detail = stderr.decode("utf-8", "replace").strip()[:1200]
        raise SandboxNetworkError(
            f"Apple Container could not enforce public-egress policy: {detail or process.returncode}")
    return True, None


if __name__ == "__main__":
    if sys.argv[1:] != ["prepare"]:
        raise SystemExit("usage: python -m backend.sandbox.macos_container_network prepare")
    print(prepare_network_image())
