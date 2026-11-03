"""Disposable, offline Preview composition for the existing hosted services.

This policy makes the supported Preview surface explicit. It is not an isolation
boundary for application code; the separately reviewed systemd units provide it.
Never import or initialize an artifact as root.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from starlette.responses import JSONResponse


def require_nonroot():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ValueError("Preview application commands must run as an unprivileged Preview user")


def create_preview_services(settings):
    """Keep real storage/lifecycles and Core cards, with no paid or cloud runtime."""
    from backend.hosting.policy import unsafe_host_configuration
    from backend.plugins.loader import load_plugin_registry
    from backend.services import create_services

    if settings.tenant_scope is None or not settings.tenant_scope.world_id:
        raise ValueError("Preview requires an explicitly bound hosted profile")
    settings = replace(settings, agent_runtime="core.mock", sandbox_runtime=None,
                       application_mode="preview", marketplace_url=None,
                       plugin_directories=(), inherit_runtime_environment=False)
    # Optional Packs can contain their own providers/actions. The first Preview
    # iteration deliberately exposes only the existing Core card catalog.
    registry = load_plugin_registry(data_root=settings.data_root, core_only=True,
                                    load_installed=False, load_entry_points=False)
    services = create_services(settings, plugins=registry)
    services.run_manager.unavailable_runtime("google.adk", ValueError("Paid models are disabled in Preview"),
                                           phase="hosting_policy")
    if any(unsafe_host_configuration({"type": card.type, "config": card.config})
           for card in services.world.list_cards()):
        services.close()
        raise ValueError("Preview fixtures cannot contain host filesystem bindings or local agents")
    return services


class PreviewPolicyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            path = scope["path"].rstrip("/")
            prefixes = ("/api/settings/models", "/api/settings/llm", "/api/sandboxes", "/api/knowledge")
            if any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
                return await JSONResponse({"error": {"code": "preview_operation_disabled",
                    "message": "Paid models and cloud Sandbox actions are disabled in Preview."}},
                    status_code=403, headers={"Cache-Control": "no-store"})(scope, receive, send)
        await self.app(scope, receive, send)


def create_preview_app(config, *, frontend_directory=None):
    from .worker import WorkerConfig, create_worker_app
    config = config if isinstance(config, WorkerConfig) else WorkerConfig(**config)
    if config.shared_models_origin is not None or config.shared_environment_origin is not None:
        raise ValueError("Preview cannot use shared models or execution environment services")
    config = replace(config, agent_runtime="core.mock", sandbox_runtime=None)
    app = create_worker_app(config, frontend_directory=frontend_directory, services_factory=create_preview_services)
    app.add_middleware(PreviewPolicyMiddleware)
    return app


async def initialize_preview(directory: Path, origin: str, passwords: dict, *, service="oaw-preview",
                             allow_local_http=False):
    """Create two independent fixture accounts once; never reset existing data."""
    require_nonroot()
    from backend.config import Settings
    from backend.world.models import CardCreate, EdgeCreate, Point
    from .accounts import AccountStore
    from .cli import initialize, provision
    from .configuration import default_upstream, public_origin, read_gateway_settings, write_private_json
    from .deployments import DeploymentStore
    from .systemd import deployment_name
    from .worker import hosted_scope

    deployment_name(service)
    if type(allow_local_http) is not bool or not isinstance(origin, str):
        raise ValueError("Preview origin must be a string and allow_local_http must be a boolean")
    public_origin(origin, allow_local_http)
    if allow_local_http:
        match = re.fullmatch(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]*)", origin)
        if match is None or not 1024 <= int(match[1]) <= 65535:
            raise ValueError("Private Preview requires an exact HTTP loopback origin with an unprivileged port")
    if (not isinstance(passwords, dict) or set(passwords) != {"alice", "bob"}
            or any(not isinstance(value, str) or len(value) < 12 or len(value.encode()) > 1024
                   for value in passwords.values()) or passwords["alice"] == passwords["bob"]):
        raise ValueError("Provide distinct passwords of at least 12 characters for exactly alice and bob")
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Preview initialization requires a new, empty directory; existing fixtures are never replaced")
    config_path = initialize(directory, origin, allow_local_http)
    accounts = AccountStore(config_path.parent / "accounts.sqlite3")
    # Release idle checks run before the first gateway start. This store closes
    # each schema transaction internally and retains no open SQLite connection.
    DeploymentStore(config_path.parent / "deployments.sqlite3")
    fixtures = []
    for username in ("alice", "bob"):
        user = accounts.create_user(username, username.title() + " (Preview)", passwords[username])
        profile = await provision(directory, user["id"], application_id="preview",
                                  name=username.title() + " Preview workspace", agent_runtime="core.mock")
        root = directory / "instances" / profile.profile_id / "data"
        settings = replace(Settings.for_data_root(root), tenant_scope=hosted_scope(
            profile.user_id, profile.application_id, profile.profile_id))
        services = create_preview_services(settings)
        try:
            await services.startup()
            note = await services.create_card(CardCreate(type="text", name="Preview notes",
                position=Point(x=80, y=80), content="Disposable Preview fixture. Do not enter production data or credentials."))
            agent = await services.create_card(CardCreate(type="agent", name="Preview mock agent",
                position=Point(x=460, y=80), config={"runtime_provider_id": "core.mock"}))
            conversation = await services.create_card(CardCreate(type="conversation", name="Preview conversation",
                position=Point(x=840, y=80)))
            await services.create_edge(EdgeCreate(source=agent.id, target=conversation.id, relationship="participate"))
            fixtures.append({"username": username, "user_id": user["id"], "profile_id": profile.profile_id,
                             "cards": {"note": note.id, "agent": agent.id, "conversation": conversation.id}})
        finally:
            try:
                await services.shutdown()
            finally:
                services.close()
    settings = read_gateway_settings(config_path)
    profiles = [replace(profile, upstream=default_upstream(profile.profile_id, service)) for profile in settings.profiles]
    write_private_json(config_path, {"version": 1, "public_origin": origin, "allow_local_http": allow_local_http,
                                     "profiles": [asdict(profile) for profile in profiles]})
    receipt = {"version": 1, "environment": "preview", "paid_models": False, "cloud_sandbox": False,
               "fixtures": fixtures}
    write_private_json(directory / "fixture-receipt.json", receipt)
    return receipt


def preview_units(settings, *, directory: str, application: str, service: str, port: int,
                  verify_user: str, production_roots: list[str], execution_root: str):
    """Render only; reviewed unit installation belongs to the pinned operator."""
    from .configuration import default_upstream
    from .systemd import linux_path, render_units, service_user
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", verify_user) or verify_user == "root":
        raise ValueError("An unprivileged Preview verification group is required")
    if not production_roots:
        raise ValueError("Pin all production application, hosting, release and operator roots")
    roots = [linux_path(root) for root in production_roots]
    linux_path(execution_root)
    preview_paths = [PurePosixPath(value) for value in (directory, application, execution_root)]
    if any(left.is_relative_to(right) or right.is_relative_to(left)
           for left in preview_paths for right in map(PurePosixPath, roots)):
        raise ValueError("Production paths must be separate from Preview application and data")
    if any(left.is_relative_to(right) or right.is_relative_to(left)
           for index, left in enumerate(preview_paths) for right in preview_paths[index + 1:]):
        raise ValueError("Preview application, data and execution root must be separate")
    if settings.shared_models_origin or settings.shared_environment_origin:
        raise ValueError("Preview units cannot use shared production services")
    if any(profile.upstream != default_upstream(profile.profile_id, service) for profile in settings.profiles):
        raise ValueError("Preview worker sockets must belong to the Preview service")
    units = render_units(settings, directory=directory, application=application, name=service, port=port)
    ingress = f"{service}-gateway.socket"
    units[ingress] = f"""# OAW hosting directory: {directory}
[Unit]
Description=OAW private Preview ingress ({service})
PartOf={service}.target

[Socket]
ListenStream=/run/{service}/gateway.sock
SocketUser={service_user(service)}
SocketGroup=www-data
SocketMode=0660
DirectoryMode=0755
RemoveOnStop=yes
"""
    gateway_name = f"{service}-gateway.service"
    units[gateway_name] = units[gateway_name].replace("After=network-online.target\n",
        f"Requires={ingress}\nAfter={ingress} network-online.target\n")
    units[gateway_name] = units[gateway_name].replace("[Service]\n", f"[Service]\nSockets={ingress}\n")
    units[gateway_name] = units[gateway_name].replace(f" --port {port}\n", " --fd 3\n")
    units[f"{service}.target"] = units[f"{service}.target"].replace(
        "=" + gateway_name, "=" + ingress + " " + gateway_name)
    runner = f"{application}/backend/.venv/bin/python {application}/scripts/hosted.py worker"
    replacement = f"{application}/backend/.venv/bin/python -m backend.hosting.preview worker"
    for filename, content in list(units.items()):
        if filename.endswith(".socket") and "-worker-" in filename:
            content = re.sub(r"^SocketGroup=.*$", "SocketGroup=" + verify_user, content, flags=re.MULTILINE)
            content = content.replace("SocketMode=0600", "SocketMode=0660")
        if filename.endswith(".service"):
            content = re.sub(r"^MemoryMax=.*$", "MemoryMax=" + ("384M" if "-worker-" in filename else "128M"),
                             content, flags=re.MULTILINE)
            content = re.sub(r"^TasksMax=.*$", "TasksMax=128", content, flags=re.MULTILINE)
            content = content.replace("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n", "RestrictAddressFamilies=AF_UNIX\n")
            content = content.replace(runner, replacement)
            content = re.sub(r"^InaccessiblePaths=.*\n", "", content, flags=re.MULTILINE)
            content = re.sub(r"^IPAddressDeny=.*$", "IPAddressDeny=any", content, flags=re.MULTILINE)
            content = content.replace("[Service]\n", f"[Service]\nRootDirectory={execution_root}\nMountAPIVFS=yes\n"
                "PrivateIPC=yes\nProcSubset=pid\nPrivateNetwork=yes\n")
            if "-worker-" in filename:
                content = re.sub(r"^EnvironmentFile=.*\n", "", content, flags=re.MULTILINE)
                content = re.sub(r"^BindReadOnlyPaths=(.*)$", lambda match: "BindReadOnlyPaths="
                    + application + ":" + application + " " + match[1], content, flags=re.MULTILINE)
            else:
                content = content.replace("[Service]\n", "[Service]\nBindReadOnlyPaths="
                    + application + ":" + application + f" /run/{service}:/run/{service}\n")
        units[filename] = content
    return units


def main(argv=None):
    require_nonroot()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("initialize")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--origin", required=True)
    init.add_argument("--allow-local-http", action="store_true")
    init.add_argument("--service", default="oaw-preview")
    init.add_argument("--password-file", type=Path, required=True)
    units = commands.add_parser("units")
    units.add_argument("--directory", type=Path, required=True)
    units.add_argument("--application-directory", required=True)
    units.add_argument("--service", default="oaw-preview")
    units.add_argument("--port", type=int, default=8081)
    units.add_argument("--verify-user", required=True)
    units.add_argument("--execution-root", required=True)
    units.add_argument("--production-root", action="append", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--config", type=Path, required=True)
    worker.add_argument("--fd", type=int)
    worker.add_argument("--host", default="127.0.0.1")
    worker.add_argument("--port", type=int, default=8000)
    worker.add_argument("--frontend", type=Path, default=Path(__file__).resolve().parents[2] / "frontend/dist")
    args = parser.parse_args(argv)
    if args.command == "initialize":
        metadata = args.password_file.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or os.name == "posix" and metadata.st_mode & 0o077):
            raise ValueError("Preview password file must be an owner-only regular file")
        passwords = json.loads(args.password_file.read_text(encoding="utf-8"))
        print(json.dumps(asyncio.run(initialize_preview(args.directory, args.origin, passwords, service=args.service,
                                                       allow_local_http=args.allow_local_http))))
    elif args.command == "units":
        from .configuration import read_gateway_settings
        values = preview_units(read_gateway_settings(args.directory / "control/hosting.json"),
            directory=str(args.directory), application=args.application_directory, service=args.service,
            port=args.port, verify_user=args.verify_user, production_roots=args.production_root,
            execution_root=args.execution_root)
        output = args.directory / "systemd" / args.service
        output.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for filename, content in values.items():
            path = output / filename
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)
            hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(json.dumps({"directory": str(output), "files": hashes}, sort_keys=True))
    else:
        import uvicorn
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be 1-65535")
        if args.fd is not None:
            from .systemd import activated_socket
            activated_socket(args.fd)
        value = json.loads(args.config.read_text(encoding="utf-8"))
        value["data_root"] = args.config.resolve().parent / value["data_root"]
        app = create_preview_app(value, frontend_directory=args.frontend if args.frontend.is_dir() else None)
        uvicorn.run(app, host=args.host, port=args.port, fd=args.fd, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
