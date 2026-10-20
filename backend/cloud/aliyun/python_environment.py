"""Prepare wheel-only Python environments inside each private cloud VM.

Only dependency recipes and verification receipts persist on ECS. Packages never
run in the OAW host, and their files never enter bounded workspace snapshots.
"""
import asyncio
import hashlib
import json
import shlex

from backend.packs.requirements import aggregate_requirements
from backend.sandbox.models import SandboxPreparationError, SandboxStateError, SandboxValidationError

PYTHON_ROOT = "/opt/oaw-python"
SETUP_SECONDS = 240

SETUP_SCRIPT = r'''
import importlib.metadata, json, os, pathlib, subprocess, sys
requirements = json.loads(sys.argv[1])
root = pathlib.Path('/opt/oaw-python')
environment = dict(os.environ, PIP_CONFIG_FILE=os.devnull, PYTHONNOUSERSITE='1')
log = pathlib.Path('/opt/oaw-python-setup.log')
log.write_text('')
log.chmod(0o600)
def run(argv):
    with log.open('a') as output:
        result = subprocess.run(argv, env=environment, stdout=output, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        print(json.dumps({'error': log.read_text(errors='replace')[-12000:]}))
        raise SystemExit(result.returncode)
if not (root/'bin/python').exists():
    # Reuse trusted libraries baked into the selected Alibaba template. New
    # packages are written only into this VM's separate managed environment.
    run([sys.executable, '-I', '-m', 'venv', '--system-site-packages', str(root)])
run([str(root/'bin/python'), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
     'install', '--no-input', '--no-cache-dir', '--only-binary=:all:',
     '--index-url', 'https://mirrors.aliyun.com/pypi/simple/', *requirements])
run([str(root/'bin/python'), '-I', '-m', 'pip', 'check'])
probe = "import importlib.metadata as m,json,platform; names={d.metadata['Name'].lower() for d in m.distributions()}; print(json.dumps({'python_version':platform.python_version(),'package_versions':{n:m.version(n) for n in names}}))"
result = subprocess.check_output([str(root/'bin/python'), '-I', '-c', probe], env=environment, text=True)
print(result.strip())
'''


class CloudPython:
    def __init__(self, backend):
        self.backend = backend

    def requirements(self, value, requested=()):
        try:
            return aggregate_requirements({"deployment": self.backend.settings.python_requirements,
                "saved cloud dependencies": value.get("python_requirements", []), "requested": requested})
        except ValueError as exc:
            raise SandboxValidationError(str(exc)) from None

    async def _renew_lifetime(self, sandbox, value):
        """A renewal is idempotent; never replay installation or user commands."""
        import httpx
        from e2b import SandboxException
        try:
            await sandbox.set_timeout(self.backend.settings.lifetime_seconds)
        except (TimeoutError, httpx.RequestError, SandboxException):
            # Recheck identity before one bounded retry on this same VM.
            info = await sandbox.get_info()
            if (info.metadata.get("oaw.profile") != self.backend.settings.profile_id
                    or info.metadata.get("oaw.request") != value["request_key"]):
                raise SandboxStateError("Cloud Python VM identity changed during renewal") from None
            await sandbox.set_timeout(self.backend.settings.lifetime_seconds)

    async def status(self):
        backend = self.backend
        if not backend.path.exists():
            return None
        value = json.loads(backend.path.read_text(encoding="utf-8"))
        verified = value.get("python_verified") or {}
        digest = hashlib.sha256(json.dumps(self.requirements(value)).encode()).hexdigest()
        return {"supported": True, "ready": bool(value["remote_id"] and verified.get("remote_id") == value["remote_id"]
                    and verified.get("digest") == digest),
            "provider": "aliyun-vm", "requirements": self.requirements(value),
            "python_version": verified.get("python_version"), "package_versions": verified.get("package_versions", {}),
            "versions_source": "last verified Alibaba VM" if verified else None,
            "preparing": bool(value.get("python_setup")), "interpreter": PYTHON_ROOT + "/bin/python"}

    async def prepare(self, requested, *, bootstrap_key=None):
        del bootstrap_key
        backend = self.backend
        if backend.lock.locked():
            raise SandboxStateError("Cloud Sandbox is busy; wait for the current operation")
        async with backend.lock:
            if not backend.path.exists():
                raise SandboxStateError("Start the Sandbox before preparing cloud Python")
            value = json.loads(backend.path.read_text(encoding="utf-8"))
            value = backend._read(value["sandbox_id"])
            requirements = self.requirements(value, requested)
            if value["command"] is not None or value["state"] in {"creating", "uncertain"}:
                raise SandboxStateError("Reconcile the previous cloud operation before preparing Python")
            previous = value.get("python_requirements", [])
            value["python_requirements"] = requirements
            backend._write(value)
            try:
                sandbox = await backend._ensure_remote(value)
                await self.ensure(sandbox, value)
            except BaseException:
                # A failed ad hoc package must not poison every future command.
                # Unknown outcomes still keep the setup receipt and block use.
                value["python_requirements"] = previous
                backend._write(value)
                raise
        return await self.status()

    async def ensure(self, sandbox, value):
        """Called under the backend operation lock, before user command admission."""
        backend = self.backend
        requirements = self.requirements(value)
        if not requirements:
            return
        digest = hashlib.sha256(json.dumps(requirements).encode()).hexdigest()
        verified = value.get("python_verified") or {}
        if verified.get("remote_id") == sandbox.sandbox_id and verified.get("digest") == digest:
            return
        if value.get("python_setup"):
            raise SandboxStateError("Cloud Python setup outcome is uncertain; stop and reconcile before retrying")
        if not value.get("network_enabled"):
            raise SandboxPreparationError("Cloud Python preparation needs networking enabled on this Sandbox")
        if backend.settings.lifetime_seconds < SETUP_SECONDS + 60:
            raise SandboxPreparationError("Cloud Python needs a Sandbox lifetime of at least 300 seconds")
        # pip may partially change an existing environment before failing.
        # Never reuse its old verification after an attempted mutation.
        value.pop("python_verified", None)
        value["python_setup"] = {"remote_id": sandbox.sandbox_id, "digest": digest,
                                 "requirements": requirements, "state": "admitted"}
        backend._write(value)
        phase = "initial_lifetime"
        try:
            await self._renew_lifetime(sandbox, value)
            command = shlex.join(["python3", "-I", "-c", SETUP_SCRIPT, json.dumps(requirements)])
            phase = "installation"
            async with asyncio.timeout(SETUP_SECONDS + 10):
                handle = await sandbox.commands.run(command, user="root", cwd="/", background=True, timeout=SETUP_SECONDS)
                value["python_setup"].update(pid=handle.pid, state="running")
                backend._write(value)
                from e2b.sandbox.commands.command_handle import CommandExitException
                try:
                    result = await handle.wait()
                except CommandExitException as exc:
                    result = exc
            if result.exit_code:
                value["python_setup"] = None
                backend._write(value)
                raise SandboxPreparationError("Cloud Python installation failed: " + (result.stdout + result.stderr)[-12000:])
            report = json.loads(result.stdout)
            if not isinstance(report.get("package_versions"), dict) or not isinstance(report.get("python_version"), str):
                raise ValueError("Invalid cloud Python verification")
            value["python_verified"] = {"remote_id": sandbox.sandbox_id, "digest": digest, **report}
            value["python_setup"] = None
            backend._write(value)
            phase = "final_lifetime"
            await self._renew_lifetime(sandbox, value)
            value.pop("python_setup_failure", None)
            backend._write(value)
        except SandboxPreparationError:
            raise
        except BaseException as exc:
            value["state"] = "uncertain"
            # Preserve useful diagnostics without logging credentials or raw
            # provider responses. A verified install and failed renewal differ.
            value["python_setup_failure"] = {"phase": phase, "error_type": type(exc).__name__}
            backend._write(value)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SandboxStateError("Cloud Python setup outcome is uncertain; its receipt was retained. Stop and reconcile before retrying") from None
