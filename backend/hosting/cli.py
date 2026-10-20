"""Operator CLI for a small hosted installation. No public account administration API."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import getpass
import importlib
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

from .accounts import AccountStore
from .configuration import GatewaySettings, HostedProfile, default_upstream, public_origin, read_gateway_settings, write_private_json


def _user(store, name):
    matches = [user for user in store.list_users() if user["id"] == name or user["username"] == name.casefold()]
    if len(matches) != 1:
        raise ValueError("Unknown user")
    return matches[0]


def _password():
    first = getpass.getpass("Password (at least 12 characters): ")
    if first != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords do not match")
    return first


def initialize(directory: Path, origin: str, allow_local_http=False):
    public_origin(origin, allow_local_http)
    config = directory / "control/hosting.json"
    if config.exists():
        raise ValueError("Installation already exists; initialization never overwrites accounts or routes")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private_json(config, {"version": 1, "public_origin": origin, "allow_local_http": allow_local_http, "profiles": []})
    AccountStore(config.parent / "accounts.sqlite3")
    return config


async def provision(directory: Path, user_id: str, *, application_id="default", name="My workspace",
                    mode="builder", upstream=None, template=None, agent_runtime="google.adk"):
    from backend.storage_location import file_lock
    from .worker import WorkerConfig, prepare_hosted_binding
    from .templates import initialize_runtime
    config_path = directory / "control/hosting.json"
    with file_lock(directory / "control/provision.lock"):
        value = json.loads(config_path.read_text(encoding="utf-8"))
        existing = read_gateway_settings(config_path)
        if any(profile.user_id == user_id for profile in existing.profiles):
            raise ValueError("This account already has an entry workspace; never replace a user's data implicitly")
        if not AccountStore(existing.accounts_path).get_user(user_id):
            raise ValueError("Unknown user")
        profile_id = str(uuid4())
        profile = HostedProfile(profile_id, user_id, application_id, name, mode,
                                upstream or default_upstream(profile_id), secrets.token_hex(32))
        # Validate routing uniqueness before creating any profile data.
        GatewaySettings(existing.public_origin, existing.accounts_path, (*existing.profiles, profile), existing.allow_local_http)
        config = WorkerConfig(profile_id, user_id, application_id, mode, existing.public_origin, profile.secret,
                              directory / "instances" / profile_id / "data", agent_runtime)
        if mode == "runtime" and template is None:
            raise ValueError("Runtime profiles require --template exported from a published workspace")
        if mode == "builder" and template is not None:
            raise ValueError("Use runtime mode to initialize a published template")
        # A failed provision is left unregistered for inspection; it can never
        # receive user traffic or replace an existing runtime.
        prepare_hosted_binding(config.data_root, profile_id=profile_id, user_id=user_id, application_id=application_id)
        if mode == "runtime":
            await initialize_runtime(config, Path(template))
        worker = {**asdict(config), "data_root": "data"}
        write_private_json(config.data_root.parent / "worker.json", worker)
        env = config.data_root.parent / "worker.env"
        descriptor = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("# Operator-owned per-instance model/runtime environment. Never put login passwords here.\n")
        value["profiles"].append(asdict(profile))
        write_private_json(config_path, value)
        return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--origin", required=True)
    init.add_argument("--allow-local-http", action="store_true")
    for name in ("user-add", "user-list", "user-disable", "user-enable", "password", "identity-link", "profile-add"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, required=True)
        if name != "user-list":
            command.add_argument("--user", required=True, help="Username or stable internal user UUID")
        if name == "user-add":
            command.add_argument("--display-name")
            command.add_argument("--oidc-only", action="store_true")
        elif name == "identity-link":
            command.add_argument("--issuer", required=True)
            command.add_argument("--subject", required=True)
        elif name == "profile-add":
            command.add_argument("--application", default="default")
            command.add_argument("--name", default="My workspace")
            command.add_argument("--mode", choices=["builder", "runtime"], default="builder")
            command.add_argument("--template", type=Path)
            command.add_argument("--upstream")
            command.add_argument("--agent-runtime", default="google.adk")
    commands.add_parser("prepare-runtime", help="Prepare shared Pack dependencies during installation only")
    for name in ("systemd", "install-systemd"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--name", default="oaw-hosted")
        if name == "systemd":
            command.add_argument("--application-directory", type=Path, default=Path(__file__).resolve().parents[2])
            command.add_argument("--port", type=int, default=8080)
    export = commands.add_parser("template-export")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--release", required=True)
    export.add_argument("--output", type=Path, required=True)
    for name in ("gateway", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8080 if name == "gateway" else 8000)
        command.add_argument("--frontend", type=Path, default=Path(__file__).resolve().parents[2] / "frontend/dist")
        if name == "worker":
            command.add_argument("--fd", type=int, help="Inherited systemd Unix socket (native hosted deployment)")
    args = parser.parse_args(argv)
    if hasattr(args, "port") and not 1 <= args.port <= 65535:
        parser.error("Port must be 1-65535")
    if args.command in {"prepare-runtime", "systemd", "install-systemd"}:
        from .systemd import generate, install, prepare_runtime
        if args.command == "prepare-runtime":
            print(prepare_runtime())
        elif args.command == "systemd":
            print(generate(args.directory, str(args.application_directory.absolute()), name=args.name, port=args.port))
        else:
            install(args.directory, name=args.name)
            print(f"Installed; start with: systemctl enable --now {args.name}.target")
        return
    if args.command == "init":
        print(initialize(args.directory.resolve(), args.origin, args.allow_local_http))
        return
    if args.command == "template-export":
        from .templates import export_template
        value = asyncio.run(export_template(args.source, args.release, args.output))
        print(f"Exported {value['name']} to {args.output}; includes selected current documents, no account credentials or conversation history.")
        return
    if args.command in {"gateway", "worker"}:
        import uvicorn
        descriptor = getattr(args, "fd", None)
        if descriptor is not None:
            from .systemd import activated_socket
            activated_socket(descriptor)
        if args.command == "gateway":
            from .gateway import create_gateway
            config = args.config.resolve()
            oidc_path = config.parent / "oidc.json"
            oidc = None
            if oidc_path.is_file():
                from .oidc import OidcSettings
                oidc = OidcSettings(**json.loads(oidc_path.read_text(encoding="utf-8")))
            app = create_gateway(read_gateway_settings(config), args.frontend, oidc_settings=oidc)
        else:
            from .worker import create_worker_app
            value = json.loads(args.config.read_text(encoding="utf-8"))
            value["data_root"] = args.config.resolve().parent / value["data_root"]
            factory_name = os.environ.get("OAW_HOSTED_SERVICES_FACTORY")
            factory = None
            if factory_name:
                module, name = factory_name.split(":", 1)
                factory = getattr(importlib.import_module(module), name)
            app = create_worker_app(value, frontend_directory=args.frontend, services_factory=factory)
        # Do not log URLs/cookies or trust arbitrary forwarding headers. A single
        # process owns each profile and the gateway checks the configured origin.
        uvicorn.run(app, host=args.host, port=args.port, fd=descriptor, proxy_headers=False, access_log=False)
        return
    directory = args.directory.resolve()
    settings = read_gateway_settings(directory / "control/hosting.json")
    store = AccountStore(settings.accounts_path)
    if args.command == "user-add":
        user = store.create_user(args.user, args.display_name, None if args.oidc_only else _password())
        print(f"Created {user['username']}: {user['id']}")
    elif args.command == "user-list":
        for user in store.list_users():
            print(f"{user['id']}  {user['username']}  {'enabled' if user['enabled'] else 'disabled'}")
    else:
        user = _user(store, args.user)
        if args.command == "password":
            store.set_password(user["id"], _password())
        elif args.command in {"user-enable", "user-disable"}:
            store.set_enabled(user["id"], args.command == "user-enable")
        elif args.command == "identity-link":
            store.link_identity(user["id"], args.issuer, args.subject)
        elif args.command == "profile-add":
            profile = asyncio.run(provision(directory, user["id"], application_id=args.application, name=args.name,
                mode=args.mode, upstream=args.upstream, template=args.template, agent_runtime=args.agent_runtime))
            print(f"Created {profile.mode} profile {profile.profile_id}; restart the gateway to load the new route.")
        else:
            raise ValueError("Unsupported command")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
