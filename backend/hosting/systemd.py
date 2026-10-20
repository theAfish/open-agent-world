"""Native Linux deployment: stable service identities and socket-activated workers.

Rendering is portable and has no system side effects. Installation is an explicit,
offline root operation; no package installation happens here or at service startup.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time

from .configuration import GatewaySettings, default_upstream, read_gateway_settings, write_private_json


def deployment_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", value):
        raise ValueError("Deployment name must contain 1-24 lowercase letters, digits or hyphens")
    return value


def linux_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or str(path) != value
            or ".." in path.parts or len(path.parts) < 3
            or path.parts[1] not in {"opt", "srv", "var"}):
        raise ValueError("Use a dedicated absolute Linux path under /opt, /srv or /var, without spaces or symlinks")
    return value


def service_user(name: str, profile_id: str = "gateway") -> str:
    digest = hashlib.sha256(f"{deployment_name(name)}:{profile_id}".encode()).hexdigest()[:24]
    return "oaw-" + digest


def _common(app: str, user: str, memory: str, tasks: int) -> str:
    return f"""User={user}
Group={user}
WorkingDirectory={app}
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
UMask=0077
Restart=on-failure
RestartSec=3
TimeoutStopSec=45
KillMode=control-group
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectProc=invisible
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=link-local 100.100.100.200/32
MemoryMax={memory}
TasksMax={tasks}
"""


def render_units(settings: GatewaySettings, *, directory: str, application: str,
                 name: str = "oaw-hosted", port: int = 8080) -> dict[str, str]:
    directory, application, name = linux_path(directory), linux_path(application), deployment_name(name)
    if (PurePosixPath(directory).is_relative_to(application) or PurePosixPath(application).is_relative_to(directory)):
        raise ValueError("Application and hosting data directories must be separate")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Gateway port must be between 1024 and 65535")
    gateway = service_user(name)
    runner = f"{application}/backend/.venv/bin/python {application}/scripts/hosted.py"
    view = f"/var/lib/{name}-control"
    units = {f"{name}-gateway.service": f"""[Unit]
Description=OAW authenticated gateway ({name})
After=network-online.target
Wants=network-online.target
PartOf={name}.target

[Service]
{_common(application, gateway, '512M', 128)}BindPaths={directory}/control:{view}
InaccessiblePaths={directory}
ExecStart={runner} gateway --config {view}/hosting.json --port {port}
"""}
    members = [f"{name}-gateway.service"]
    users = [f'u {gateway} - "OAW {name} gateway" /nonexistent /usr/sbin/nologin']
    for profile in settings.profiles:
        instance = f"{directory}/instances/{profile.profile_id}"
        worker = f"{name}-worker-{profile.profile_id}"
        user = service_user(name, profile.profile_id)
        view = f"/var/lib/{name}-profile"
        users.append(f'u {user} - "OAW {name} {profile.profile_id}" /nonexistent /usr/sbin/nologin')
        members.extend([worker + ".socket", worker + ".service"])
        units[worker + ".socket"] = f"""[Unit]
Description=Private OAW socket ({profile.profile_id})
PartOf={name}.target

[Socket]
ListenStream=/run/{name}/{profile.profile_id}.sock
SocketUser={gateway}
SocketGroup={gateway}
SocketMode=0600
DirectoryMode=0755
RemoveOnStop=yes
"""
        units[worker + ".service"] = f"""[Unit]
Description=OAW private workspace ({profile.profile_id})
Requires={worker}.socket
After={worker}.socket network-online.target
Wants=network-online.target
PartOf={name}.target

[Service]
{_common(application, user, '2G', 256)}Sockets={worker}.socket
EnvironmentFile={instance}/worker.env
BindReadOnlyPaths={instance}:{view}
BindPaths={instance}/data:{view}/data
InaccessiblePaths={directory} /run/{name}
ExecStart={runner} worker --config {view}/worker.json --fd 3
"""
    units[f"{name}.target"] = f"""[Unit]
Description=OAW hosted users ({name})
Wants={' '.join(members)}
After={' '.join(members)}

[Install]
WantedBy=multi-user.target
"""
    units["sysusers.conf"] = "\n".join(users) + "\n"
    for filename in units:
        if filename != "sysusers.conf":
            units[filename] = f"# OAW hosting directory: {directory}\n" + units[filename]
    return units


def _fingerprint(directory: Path, settings: GatewaySettings) -> str:
    digest = hashlib.sha256((directory / "control/hosting.json").read_bytes())
    for profile in settings.profiles:
        digest.update((directory / "instances" / profile.profile_id / "worker.json").read_bytes())
    return digest.hexdigest()


def generate(directory: Path, application: str, *, name="oaw-hosted", port=8080) -> Path:
    directory = directory.absolute()
    settings = read_gateway_settings(directory / "control/hosting.json")
    units = render_units(settings, directory=str(directory), application=application, name=name, port=port)
    output = directory / "systemd" / name
    # Metadata contains no credentials. Units are generated, not executable shell scripts.
    plan = {"version": 1, "directory": str(directory), "application": application, "name": name,
            "port": port, "fingerprint": _fingerprint(directory, settings)}
    write_private_json(output / "plan.json", plan)
    for filename, content in units.items():
        path = output / filename
        if path.is_symlink():
            raise ValueError("Refusing symlink in generated service bundle")
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
    return output


def _plain_tree(root: Path) -> list[Path]:
    """Inspect before any ownership change; never follow tenant-controlled links."""
    if root.resolve() != root.absolute():
        raise ValueError("Hosting storage must not use symlink ancestors")
    result = [root]
    for current, directories, files in os.walk(root, followlinks=False):
        result.extend(Path(current) / item for item in directories + files)
    for path in result:
        metadata = path.lstat()
        if (not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                or (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1)):
            raise ValueError(f"Hosting storage must not contain symlinks, hard links or special files: {path}")
    return result


def _trusted(path: Path) -> None:
    # Includes ancestors, preventing replacement through an administrator-writable
    # checkout parent, and resolved interpreter/library links outside the checkout.
    for target in (path, *path.parents, path.resolve(strict=True), *path.resolve().parents):
        metadata = target.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ValueError(f"Shared application and its ancestors must be root-owned and not group/world writable: {target}")


def _own(paths: list[Path], uid: int, gid: int) -> None:
    for path in paths:
        metadata = path.lstat()
        os.chown(path, uid, gid, follow_symlinks=False)
        path.chmod(0o700 if path.is_dir() else 0o600 | (metadata.st_mode & 0o100))


def _run(*args):
    try:
        return subprocess.run(args, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise ValueError(f"{args[0]} failed: {(error.stderr or error.stdout or str(error))[-4000:]}") from error


def check_network_filter(python: Path, name: str) -> None:
    """Fail closed if systemd's cgroup network filter is silently unsupported.

    Probe only an ephemeral loopback listener; never contact instance metadata.
    """
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = str(listener.getsockname()[1])
        # Prove that the same system service can reach the listener without the
        # rule. Packet filters may drop SYNs (timeout), not return EPERM.
        _run("systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--unit={name}-network-control",
             str(python), "-c", "import socket, sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=3).close()", port)
        listener.accept()[0].close()
        code = """import errno, socket, sys
with socket.socket() as client:
    client.settimeout(3)
    try:
        client.connect(('127.0.0.1', int(sys.argv[1])))
    except TimeoutError:
        sys.exit(0)
    except OSError as error:
        print(str(error), file=sys.stderr)
        sys.exit(0 if error.errno in (errno.EACCES, errno.EPERM) else 2)
    sys.exit('systemd IPAddressDeny is not enforced by this host kernel')
"""
        _run("systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--unit={name}-network-check",
             "--property=IPAddressDeny=127.0.0.1/32", str(python), "-c", code, port)


def install(directory: Path, *, name="oaw-hosted") -> None:
    """Install a previously reviewed bundle, with services stopped. Never starts it."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("install-systemd requires root on a Linux systemd host")
    import pwd
    import grp
    name = deployment_name(name)
    directory = directory.absolute()
    linux_path(str(directory))
    _trusted(directory)
    version = _run("systemctl", "--version").stdout.splitlines()[0].split()[1]
    if int(version) < 249 or not Path("/run/systemd/system").is_dir():
        raise ValueError("Native hosting requires a running systemd 249 or later")
    output = directory / "systemd" / name
    _plain_tree(output)
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    settings = read_gateway_settings(directory / "control/hosting.json")
    if (plan.get("version") != 1 or plan.get("directory") != str(directory) or plan.get("name") != name
            or plan.get("fingerprint") != _fingerprint(directory, settings)):
        raise ValueError("Deployment bundle is stale; regenerate with the systemd command")
    application = Path(linux_path(plan["application"]))
    units = render_units(settings, directory=str(directory), application=str(application), name=name, port=plan["port"])
    for filename, content in units.items():
        if (output / filename).read_text(encoding="utf-8") != content:
            raise ValueError("Generated units changed; regenerate the bundle and use systemctl edit for overrides")
    # Include previously installed workers, even if an operator removed their route.
    installed = Path("/etc/systemd/system")
    service_names = set(units) - {"sysusers.conf"}
    service_names.update(path.name for path in installed.glob(name + "-worker-*.*"))
    stop_deadline = time.monotonic() + 60
    for unit in sorted(service_names):
        existing = installed / unit
        if existing.exists() or existing.is_symlink():
            if existing.is_symlink() or not existing.read_text(encoding="utf-8").startswith(f"# OAW hosting directory: {directory}\n"):
                raise ValueError(f"Deployment name is already used by another installation or unrelated unit: {unit}")
        state = _run("systemctl", "show", unit, "--property=ActiveState", "--value").stdout.strip()
        # Stopping a target can complete before its PartOf services finish their
        # graceful shutdown. Wait only for an already requested stop; never stop
        # active users implicitly or change ownership while they are exiting.
        while state == "deactivating" and time.monotonic() < stop_deadline:
            time.sleep(.1)
            state = _run("systemctl", "show", unit, "--property=ActiveState", "--value").stdout.strip()
        if state not in {"inactive", "failed", ""}:
            raise ValueError(f"Stop {name}.target before installation or permission changes ({unit}: {state})")
    paths = _plain_tree(directory)
    from itertools import chain
    for path in chain((application,), application.rglob("*")):
        _trusted(path)
    for required in ("backend/.venv/bin/python", "scripts/hosted.py", "frontend/dist/index.html",
                     "backend/.venv/oaw-runtime-bundle"):
        if not (application / required).exists():
            raise ValueError(f"Complete setup, frontend build and prepare-runtime before installation: {required}")
    # Validate that worker configs actually describe the same immutable identities.
    for profile in settings.profiles:
        instance = directory / "instances" / profile.profile_id
        worker = json.loads((instance / "worker.json").read_text(encoding="utf-8"))
        if any(worker.get(key) != getattr(profile, key) for key in
               ("profile_id", "user_id", "application_id", "mode", "secret")) or worker.get("data_root") != "data":
            raise ValueError("Worker configuration differs from its gateway identity or private data directory")
        if worker.get("public_origin") != settings.public_origin or not (instance / "worker.env").is_file():
            raise ValueError("Incomplete or inconsistent worker configuration")
    for line in units["sysusers.conf"].splitlines():
        account, marker = line.split()[1], line.split('"')[1]
        try:
            entry = pwd.getpwnam(account)
        except KeyError:
            continue
        group = grp.getgrnam(account)
        if (entry.pw_uid == 0 or entry.pw_gid == 0 or entry.pw_gid != group.gr_gid
                or entry.pw_gecos != marker or entry.pw_shell != "/usr/sbin/nologin"):
            raise ValueError(f"Refusing to reuse an unrelated system account: {account}")
    check_network_filter(application / "backend/.venv/bin/python", name)
    _run("systemd-sysusers", str(output / "sysusers.conf"))
    _run("systemd-analyze", "verify", *(str(output / key) for key in units if key != "sysusers.conf"))
    # Preserve old routes for rollback; identities, secrets and user data stay put.
    value = json.loads((directory / "control/hosting.json").read_text(encoding="utf-8"))
    migrated = [{**asdict(profile), "upstream": default_upstream(profile.profile_id, name)} for profile in settings.profiles]
    if migrated != value["profiles"]:
        backup = directory / "control/hosting.before-systemd.json"
        if not backup.exists():
            write_private_json(backup, value)
        value["profiles"] = migrated
        write_private_json(directory / "control/hosting.json", value)
    # Lock the outer tree first. Each service sees only its bind-mounted private view.
    _own(paths, 0, 0)
    gateway = pwd.getpwnam(service_user(name))
    _own(_plain_tree(directory / "control"), gateway.pw_uid, gateway.pw_gid)
    for profile in settings.profiles:
        instance = directory / "instances" / profile.profile_id
        user = pwd.getpwnam(service_user(name, profile.profile_id))
        os.chown(instance, 0, user.pw_gid)
        instance.chmod(0o750)
        os.chown(instance / "worker.json", 0, user.pw_gid)
        (instance / "worker.json").chmod(0o640)
        _own(_plain_tree(instance / "data"), user.pw_uid, user.pw_gid)
    for filename in units:
        if filename != "sysusers.conf":
            target = installed / filename
            if target.is_symlink():
                raise ValueError(f"Refusing to replace a symlinked unit: {target}")
            shutil.copyfile(output / filename, target)
            target.chmod(0o644)
    _run("systemctl", "daemon-reload")


def activated_socket(descriptor: int) -> int:
    """Accept only the one listening AF_UNIX socket passed by systemd to this PID."""
    import socket
    if (descriptor != 3 or os.environ.get("LISTEN_PID") != str(os.getpid())
            or os.environ.get("LISTEN_FDS") != "1"):
        raise ValueError("--fd requires exactly one systemd-activated socket for this process")
    with socket.socket(fileno=os.dup(descriptor)) as listener:
        if (listener.family != socket.AF_UNIX or listener.type != socket.SOCK_STREAM
                or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
            raise ValueError("Worker activation requires a listening filesystem Unix stream socket")
    os.set_inheritable(descriptor, False)
    return descriptor


def prepare_runtime() -> Path:
    """Explicit setup step for a shared read-only source Pack runtime."""
    from backend.packs.adapters import prepare_source_adapter
    from backend.packs.spec import read_source_pack
    from backend.plugins.loader import load_plugin_registry
    root = Path(sys.prefix) / "oaw-runtime-bundle"
    root.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).resolve().parents[2]
    for manifest in sorted((repository / "plugins").glob("*/pyproject.toml")):
        spec = read_source_pack(manifest.parent)
        print(f"Preparing shared Pack runtime: {spec.owner}", flush=True)
        prepare_source_adapter(spec, root)
    load_plugin_registry(data_root=root, plugin_directories=(), load_installed=False, load_unenrolled_sources=True)
    return root
