"""Fixed-purpose Linux deployment reconciler; never executes browser shell commands.

The root timer creates private service identities and files. Template/plugin
initialization runs as the new workspace's unprivileged UID, inside the same
systemd restrictions as ordinary workers. Existing workers are never stopped.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import secrets
import stat

from .configuration import HostedProfile, default_upstream, identifier, read_gateway_settings, write_private_json
from .deployments import DeploymentStore, MAX_TEMPLATE
from .systemd import _common, _plain_tree, _own, _run, _trusted, deployment_name, linux_path, render_units, service_user
from .worker import WorkerConfig


def timer_units(directory: str, application: str, name="oaw-hosted"):
    directory, application, name = linux_path(directory), linux_path(application), deployment_name(name)
    return {
        f"{name}-provisioner.service": f"""[Unit]
Description=OAW queued workspace deployment
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory={application}
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
UMask=0077
TimeoutStartSec=300
ExecStart={application}/backend/.venv/bin/python -m backend.hosting.provisioner tick --directory {directory} --application {application} --name {name}
""",
        f"{name}-provisioner.timer": f"""[Unit]
Description=Check OAW workspace deployment queue

[Timer]
OnBootSec=10
OnUnitInactiveSec=5
Unit={name}-provisioner.service

[Install]
WantedBy=timers.target
""",
    }


def read_regular(path, limit):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
            raise ValueError("Expected a bounded regular deployment file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Deployment file too large")
        return data


def install_profile(directory, application, profile, config, template, settings, name):
    """Only NEW UUID-named instances. No modifications to another worker's tree."""
    import pwd
    instance = directory / "instances" / profile.profile_id
    if instance.exists() or instance.is_symlink():
        raise ValueError("Deployment directory already exists; inspect the prior attempt")
    account = service_user(name, profile.profile_id)
    try:
        pwd.getpwnam(account)
    except KeyError:
        pass
    else:
        raise ValueError("Deployment service identity already exists")
    instance.mkdir(mode=0o700, parents=True)
    write_private_json(instance / "worker.json", {**asdict(config), "data_root": "data"})
    if template is not None:
        write_private_json(instance / "template.json", template)
    (instance / "data").mkdir(mode=0o700)
    (instance / "worker.env").write_text("# Shared settings are inherited from worker-common.env.\n", encoding="utf-8")
    (instance / "worker.env").chmod(0o600)
    sysusers = instance / "sysusers.conf"
    sysusers.write_text(f'u {account} - "OAW {name} {profile.profile_id}" /nonexistent /usr/sbin/nologin\n', encoding="utf-8")
    _run("systemd-sysusers", str(sysusers))
    user = pwd.getpwnam(account)
    os.chown(instance, 0, user.pw_gid)
    instance.chmod(0o750)
    for filename in ("worker.json", "template.json") if template is not None else ("worker.json",):
        os.chown(instance / filename, 0, user.pw_gid)
        (instance / filename).chmod(0o640)
    _own(_plain_tree(instance / "data"), user.pw_uid, user.pw_gid)
    rendered = render_units(replace(settings, profiles=(*settings.profiles, profile)),
                            directory=str(directory), application=str(application), name=name)
    worker = f"{name}-worker-{profile.profile_id}"
    bootstrap = worker + "-initialize.service"
    view = f"/var/lib/{name}-profile"
    common = _common(str(application), account, "2G", 256).replace("Restart=on-failure", "Restart=no")
    init = f"""# OAW hosting directory: {directory}
[Unit]
Description=Initialize new OAW workspace

[Service]
Type=oneshot
{common}TimeoutStartSec=180
EnvironmentFile={instance}/worker.env
EnvironmentFile=-{directory}/worker-common.env
BindReadOnlyPaths={instance}:{view}
BindPaths={instance}/data:{view}/data
InaccessiblePaths={directory} /run/{name}
ExecStart={application}/backend/.venv/bin/python -m backend.hosting.provisioner initialize --config {view}/worker.json{f' --template {view}/template.json' if template is not None else ''}
"""
    units = {worker + suffix: rendered[worker + suffix] for suffix in (".service", ".socket")}
    units[bootstrap] = init
    for filename, content in units.items():
        path = Path("/etc/systemd/system") / filename
        # Exclusive creation; never overwrite an existing service, even after a crash.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        path.chmod(0o644)
    _run("systemd-analyze", "verify", *(f"/etc/systemd/system/{key}" for key in units))
    _run("systemctl", "daemon-reload")
    _run("systemctl", "start", bootstrap)  # Wait for the unprivileged oneshot.
    _run("systemctl", "start", worker + ".socket")
    # Persistent startup of this new socket without rewriting the shared target.
    wants = Path("/etc/systemd/system") / (name + ".target.wants")
    wants.mkdir(exist_ok=True)
    (wants / (worker + ".socket")).symlink_to(Path("/etc/systemd/system") / (worker + ".socket"))


def register_profile(directory, profile, name):
    import pwd
    from backend.storage_location import file_lock
    control = directory / "control"
    with file_lock(control / "provision.lock"):
        value = json.loads(read_regular(control / "hosting.json", 4 * 1024 * 1024))
        if any(p["profile_id"] == profile.profile_id for p in value["profiles"]):
            raise ValueError("Profile was already registered")
        value["profiles"].append(asdict(profile))
        write_private_json(control / "hosting.json", value)
        gateway = pwd.getpwnam(service_user(name))
        os.chown(control / "hosting.json", gateway.pw_uid, gateway.pw_gid)


def check_installation(directory, application, name):
    directory = Path(linux_path(str(directory)))
    application = Path(linux_path(str(application)))
    name = deployment_name(name)
    if os.geteuid() != 0:
        raise ValueError("Use the installed root deployment operator")
    _trusted(directory)
    _trusted(application)
    _trusted(application / "backend/.venv/bin/python")
    return directory, application, name


def enable(directory, application, name):
    directory, application, name = check_installation(directory, application, name)
    from .systemd import check_network_filter
    check_network_filter(application / "backend/.venv/bin/python", name)
    for filename, content in timer_units(str(directory), str(application), name).items():
        path = Path("/etc/systemd/system") / filename
        if path.exists():
            if path.is_symlink() or path.read_text() != content:
                raise ValueError("Existing provisioner unit differs; review before replacing")
        else:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            path.chmod(0o644)
    _run("systemctl", "daemon-reload")
    _run("systemctl", "enable", "--now", name + "-provisioner.timer")


def engineering(directory, application, name, username):
    from uuid import uuid4
    from .accounts import AccountStore
    from backend.storage_location import file_lock
    directory, application, name = check_installation(directory, application, name)
    with file_lock(directory / "deployment-reconcile.lock"):
        settings = read_gateway_settings(directory / "control/hosting.json")
        users = [u for u in AccountStore(settings.accounts_path).list_users() if u["username"] == username]
        if len(users) != 1 or users[0]["role"] != "admin" or not users[0]["enabled"]:
            raise ValueError("Choose an enabled administrator account")
        user_id = users[0]["id"]
        if any(p.user_id == user_id for p in settings.profiles):
            raise ValueError("Administrator already has a workspace")
        profile_id = str(uuid4())
        profile = HostedProfile(profile_id, user_id, "engineering", "Engineering workspace", "builder",
                                default_upstream(profile_id, name), secrets.token_hex(32))
        config = WorkerConfig(profile_id, user_id, profile.application_id, profile.mode, settings.public_origin,
                              profile.secret, directory / "instances" / profile_id / "data",
                              shared_models_origin=settings.shared_models_origin,
                              shared_environment_origin=settings.shared_environment_origin)
        install_profile(directory, application, profile, config, None, settings, name)
        register_profile(directory, profile, name)
        return profile.public()


def tick(directory: Path, application: Path, name="oaw-hosted"):
    from backend.storage_location import file_lock
    from .accounts import AccountStore
    from .templates import validate_template
    directory, application, name = check_installation(directory, application, name)
    with file_lock(directory / "deployment-reconcile.lock"):
        # The operator fixes directory/application/unit prefix in the timer. The
        # queue carries only UUIDs, a validated mode, and a portable definition.
        control = directory / "control"
        for path in (control, control / "hosting.json", control / "accounts.sqlite3", control / "deployments.sqlite3"):
            if path.is_symlink():
                raise ValueError("Control paths must not be symlinks")
        if not (control / "deployments.sqlite3").exists():
            return
        store = DeploymentStore(control / "deployments.sqlite3")
        with store.transaction(immediate=True) as db:
            db.execute("UPDATE hosting_jobs SET status='interrupted',message=? WHERE status='preparing'",
                       ("Deployment interrupted; operator inspection required. Existing workspaces are unchanged.",))
            row = db.execute("SELECT * FROM hosting_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return
            job = dict(row)
            db.execute("UPDATE hosting_jobs SET status='preparing' WHERE id=?", (job["id"],))
        try:
            for field in ("id", "profile_id", "requested_by", "user_id"):
                identifier(job[field])
            if job["profile_id"] != job["id"] or job["mode"] not in {"builder", "runtime"}:
                raise ValueError("Invalid deployment identity or mode")
            if len(job["template_json"].encode()) > MAX_TEMPLATE:
                raise ValueError("Template too large")
            template, _ = validate_template(json.loads(job["template_json"]))
            accounts = AccountStore(control / "accounts.sqlite3")
            actor, target = accounts.get_user(job["requested_by"]), accounts.get_user(job["user_id"])
            if not actor or not actor["enabled"] or actor["role"] != "admin" or not target or not target["enabled"] or target["role"] == "admin":
                raise ValueError("Deployment account authorization changed")
            settings = read_gateway_settings(control / "hosting.json")
            if len(settings.profiles) >= 100:
                raise ValueError("POC limit of 100 retained workspaces reached")
            profile = HostedProfile(job["id"], job["user_id"], "release-" + template["source_release_id"],
                                    template["name"], job["mode"], default_upstream(job["id"], name), secrets.token_hex(32))
            config = WorkerConfig(profile.profile_id, profile.user_id, profile.application_id, profile.mode,
                                  settings.public_origin, profile.secret, directory / "instances" / profile.profile_id / "data",
                                  shared_models_origin=settings.shared_models_origin,
                                  shared_environment_origin=settings.shared_environment_origin)
            install_profile(directory, application, profile, config, template, settings, name)
            register_profile(directory, profile, name)
            with store.transaction(immediate=True) as db:
                db.execute("UPDATE hosting_jobs SET status='ready',message=? WHERE id=?",
                           ("Ready. Select this workspace as the user's entry when appropriate.", job["id"]))
        except Exception:
            # Detailed logs stay with the server operator; do not expose private
            # paths, process output or credentials through the admin API.
            import logging
            logging.exception("Workspace deployment failed: %s", job["id"])
            with store.transaction(immediate=True) as db:
                db.execute("UPDATE hosting_jobs SET status='failed',message=? WHERE id=?",
                           ("Deployment failed; operator inspection required. Existing workspaces are unchanged.", job["id"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("tick", "enable", "engineering"):
        job = commands.add_parser(operation)
        job.add_argument("--directory", type=Path, required=True)
        job.add_argument("--application", type=Path, required=True)
        job.add_argument("--name", default="oaw-hosted")
        if operation == "engineering":
            job.add_argument("--user", required=True)
    init = commands.add_parser("initialize")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--template", type=Path)
    args = parser.parse_args()
    if args.command == "tick":
        tick(args.directory, args.application, args.name)
    elif args.command == "enable":
        enable(args.directory, args.application, args.name)
    elif args.command == "engineering":
        print(json.dumps(engineering(args.directory, args.application, args.name, args.user)))
    else:
        from .templates import initialize_runtime
        value = json.loads(args.config.read_text(encoding="utf-8"))
        value["data_root"] = args.config.parent / value["data_root"]
        config = WorkerConfig(**value)
        if args.template is None:
            from .worker import prepare_hosted_binding
            if config.mode != "builder":
                raise ValueError("Runtime initialization requires a template")
            prepare_hosted_binding(config.data_root, profile_id=config.profile_id, user_id=config.user_id,
                                   application_id=config.application_id)
        else:
            import importlib
            factory_name = os.environ.get("OAW_HOSTED_SERVICES_FACTORY")
            factory = None
            if factory_name:
                module, attribute = factory_name.split(":", 1)
                factory = getattr(importlib.import_module(module), attribute)
            asyncio.run(initialize_runtime(config, args.template, services_factory=factory))


if __name__ == "__main__":
    main()
