"""Opt-in Linux/root acceptance test against a prepared, root-owned installation.

Creates an ephemeral systemd deployment, tests real workers, then removes only
its own services, accounts and fixture data. Never installs application packages.
Run: sudo backend/.venv/bin/python scripts/test-hosted-systemd.py
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

from backend.hosting.accounts import AccountStore
from backend.hosting.cli import initialize, provision
from backend.hosting.configuration import read_gateway_settings
from backend.hosting.systemd import generate, install, service_user
from backend.hosting.templates import export_template
from backend.tests.test_deployments import prepare_source


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def wait_ready(client, path):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if response.status_code == 200:
                return response
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    raise AssertionError(f"Native service did not become ready: {path}")


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run on a Linux systemd test host as root with a prepared application")
    name = "oaw-check-" + secrets.token_hex(4)
    directory = Path("/srv") / name
    if directory.exists():
        raise SystemExit("Test directory already exists")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    profiles, units, accounts = [], [], []
    password = secrets.token_urlsafe(24)
    try:
        config = initialize(directory, origin, True)
        store = AccountStore(config.parent / "accounts.sqlite3")
        records = prepare_source(directory / "engineering")
        template = directory / "template.json"
        asyncio.run(export_template(directory / "engineering", records["release"]["id"], template))
        for index, username in enumerate(("alice", "bob", "carol")):
            user = store.create_user(username, username.title(), password)
            # One legacy TCP route exercises migration without changing its identity.
            profiles.append(asyncio.run(provision(directory, user["id"], mode="runtime" if index == 2 else "builder",
                template=template if index == 2 else None, agent_runtime="core.mock",
                upstream="http://legacy-worker:8000" if index == 0 else None)))
        output = generate(directory, str(ROOT), name=name, port=port)
        units = [path.name for path in output.iterdir() if path.suffix in {".service", ".socket", ".target"}]
        accounts = [service_user(name), *(service_user(name, profile.profile_id) for profile in profiles)]
        install(directory, name=name)
        print("PASS: host kernel enforces systemd network filtering", flush=True)
        migrated = read_gateway_settings(config)
        assert [(p.profile_id, p.user_id, p.secret) for p in migrated.profiles] == [
            (p.profile_id, p.user_id, p.secret) for p in profiles]
        assert (directory / "control/hosting.before-systemd.json").is_file()
        run("systemctl", "start", name + ".target")
        with ExitStack() as stack:
            clients = [stack.enter_context(httpx.Client(base_url=origin, trust_env=False, timeout=5,
                                                        headers={"Origin": origin})) for _ in profiles]
            wait_ready(clients[0], "/health/live")
            assert clients[0].get("/api/world").status_code == 401
            for client, username, profile in zip(clients, ("alice", "bob", "carol"), profiles):
                response = client.post("/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200, response.text
                client.headers["X-OAW-Instance"] = profile.profile_id
                info = client.get("/auth/me").json()
                assert info["instance"]["mode"] == profile.mode
                wait_ready(client, "/api/runtime-app" if profile.mode == "runtime" else "/api/world")
            print("PASS: three real services, password login, native socket routing and runtime entry", flush=True)
            alice, bob, carol = clients
            response = alice.post("/api/nodes", json={"type": "text", "name": "Alice private note", "content": "Private"})
            assert response.status_code == 201, response.text
            node = response.json()["id"]
            assert bob.get(f"/api/nodes/{node}").status_code == 404
            assert carol.get("/api/world").status_code == 404
            assert bob.post("/api/nodes", headers={"X-OAW-Instance": profiles[0].profile_id}, json={"type": "text"}).status_code == 409
            print("PASS: cross-user data and stale-tab mutations rejected", flush=True)
            for profile in profiles:
                unit = f"{name}-worker-{profile.profile_id}.service"
                pid = run("systemctl", "show", unit, "--property=MainPID", "--value")
                uid = pwd.getpwnam(service_user(name, profile.profile_id)).pw_uid
                assert uid > 0 and Path(f"/proc/{pid}").stat().st_uid == uid
                view = f"/var/lib/{name}-profile"
                code = '''import errno, pathlib, socket, sys
root, view, original, socket_path = sys.argv[1:]
for target in [root + '/scripts/hosted.py', view + '/worker.json', original + '/control/accounts.sqlite3']:
    try:
        with open(target, 'ab'):
            pass
    except OSError as error:
        assert error.errno in (errno.EACCES, errno.EROFS), error
    else:
        raise AssertionError('Private or read-only file was writable: ' + target)
try:
    socket.socket(socket.AF_UNIX).connect(socket_path)
except PermissionError:
    pass
else:
    raise AssertionError('Worker could connect to a gateway-only socket')
pathlib.Path(view + '/data/os-isolation-check').write_text('private')
'''
                run("nsenter", "--target", pid, "--mount", "--", "runuser", "-u", service_user(name, profile.profile_id),
                    "--", str(ROOT / "backend/.venv/bin/python"), "-c", code, str(ROOT), view, str(directory),
                    f"/run/{name}/{profile.profile_id}.sock")
            print("PASS: distinct non-root UIDs, read-only code/config, private data and sockets", flush=True)
            token = alice.cookies["oaw_session"]
            url = origin.replace("http:", "ws:") + "/ws/events?instance=" + profiles[0].profile_id
            with connect(url, origin=origin, additional_headers={"Cookie": "oaw_session=" + token}, proxy=None) as ws:
                assert ws.recv(timeout=10)
                assert alice.post("/auth/logout").status_code == 200
                deadline = time.monotonic() + 5
                try:
                    while time.monotonic() < deadline:
                        ws.recv(timeout=5)
                except ConnectionClosed as error:
                    assert error.code == 1008
                else:
                    raise AssertionError("Revoked websocket stayed connected")
            print("PASS: real WebSocket over Unix socket and session revocation", flush=True)
            # Reapplying to live users must not chown their mutable data.
            generate(directory, str(ROOT), name=name, port=port)
            try:
                install(directory, name=name)
            except ValueError as error:
                assert "Stop" in str(error), error
            else:
                raise AssertionError("Installer modified a running deployment")
            run("systemctl", "restart", name + ".target")
            wait_ready(bob, "/api/world")
            alice.post("/auth/login", json={"username": "alice", "password": password}).raise_for_status()
            assert wait_ready(alice, f"/api/nodes/{node}").json()["id"] == node
            print("PASS: restart retains accounts, sessions and private data; active install refused", flush=True)
            run("systemctl", "stop", name + ".target")
            original_uids = [pwd.getpwnam(account).pw_uid for account in accounts]
            install(directory, name=name)
            assert [pwd.getpwnam(account).pw_uid for account in accounts] == original_uids
            run("systemctl", "start", name + ".target")
            assert wait_ready(alice, f"/api/nodes/{node}").json()["id"] == node
            print("PASS: offline reinstallation preserves service identities and existing data", flush=True)
    except Exception:
        if units:
            print(run("journalctl", "--no-pager", "-n", "60", *[item for unit in units if unit.endswith('.service')
                       for item in ("-u", unit)]), file=sys.stderr)
        raise
    finally:
        if units:
            subprocess.run(["systemctl", "stop", name + ".target", *units], capture_output=True)
        for unit in units:
            (Path("/etc/systemd/system") / unit).unlink(missing_ok=True)
        if units:
            run("systemctl", "daemon-reload")
            subprocess.run(["systemctl", "reset-failed", *units], capture_output=True)
        for account in accounts:
            try:
                entry = pwd.getpwnam(account)
            except KeyError:
                continue
            assert entry.pw_gecos.startswith(f"OAW {name} ") and entry.pw_uid != 0
            run("userdel", account)
        for leftover in (Path("/run") / name, Path("/var/lib") / (name + "-profile"), Path("/var/lib") / (name + "-control")):
            if leftover.is_dir():
                leftover.rmdir()
        assert directory.parent == Path('/srv') and directory.name == name and not directory.is_symlink()
        if directory.exists():
            # Fixtures import Packs in this test process; release their lifetime
            # leases before removing only this process's ephemeral fixture data.
            from backend.packs.lifecycle import _PROCESS_PINS
            _PROCESS_PINS.close()
            shutil.rmtree(directory)


if __name__ == "__main__":
    main()
