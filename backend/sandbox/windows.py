"""Fail-closed Windows implementation of :class:`SandboxBackend`."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from .base import SandboxBackend, SandboxEventSink
from .environment import minimal_windows_environment
from .models import (
    CommandResult,
    ResourceAccess,
    ResourceAttachment,
    SandboxEvent,
    SandboxEventType,
    SandboxInfo,
    SandboxLimits,
    SandboxNotFoundError,
    SandboxSecurityError,
    SandboxState,
    SandboxStateError,
    SandboxValidationError,
)
from .win32 import AppContainerProfile, NativeCommandResult, WindowsNativeApi


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MANIFEST_NAME = "sandbox.json"


@dataclass(slots=True)
class _SandboxRecord:
    sandbox_id: str
    root: Path
    workspace: Path
    identity: str
    profile: AppContainerProfile
    workspace_path: str | None = None
    workspace_access: ResourceAccess = ResourceAccess.READ_WRITE
    workspace_authorized: bool = False
    workspace_identity: tuple[int, int] | None = None
    workspace_handle: int | None = None
    state: SandboxState = SandboxState.STOPPED
    attachments: dict[str, ResourceAttachment] = field(default_factory=dict)
    active_command: tuple[str, ...] | None = None
    active_job: int | None = None
    cancel_event: threading.Event | None = None
    stop_requested: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    command_done: asyncio.Event = field(default_factory=asyncio.Event)
    thread_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.command_done.set()


class WindowsSandboxBackend(SandboxBackend):
    """Native Windows AppContainer + NTFS ACL + Job Object sandbox.

    ``native_api`` is private test injection.  Production construction always
    instantiates :class:`WindowsNativeApi`, which raises on non-Windows systems
    or unavailable security primitives.  There is intentionally no subprocess
    or path-only fallback.
    """

    def __init__(
        self,
        managed_root: Path,
        *,
        limits: SandboxLimits = SandboxLimits(),
        event_sink: SandboxEventSink | None = None,
        native_api: WindowsNativeApi | None = None,
    ) -> None:
        self._managed_root = Path(managed_root).resolve()
        self._sandboxes_root = self._managed_root / "sandboxes"
        self._sandboxes_root.mkdir(parents=True, exist_ok=True)
        self._limits = limits
        self._event_sink = event_sink
        self._native = native_api if native_api is not None else WindowsNativeApi()
        self._records: dict[str, _SandboxRecord] = {}
        self._records_lock = asyncio.Lock()

    async def create(self, sandbox_id: str) -> SandboxInfo:
        self._validate_id(sandbox_id, "sandbox_id")
        async with self._records_lock:
            if sandbox_id in self._records:
                raise SandboxStateError(f"sandbox already exists: {sandbox_id}")
            sandbox_root = self._safe_child(self._sandboxes_root, sandbox_id)
            if sandbox_root.exists():
                raise SandboxStateError(f"sandbox already exists: {sandbox_id}")

            record = await asyncio.to_thread(self._create_secure, sandbox_id, sandbox_root)
            self._records[sandbox_id] = record
        await self._emit_state(record)
        return self._info(record)

    def _create_secure(self, sandbox_id: str, sandbox_root: Path) -> _SandboxRecord:
        workspace = sandbox_root / "workspace"
        profile: AppContainerProfile | None = None
        identity = self._identity(sandbox_id)
        sandbox_root.mkdir(parents=False, exist_ok=False)
        try:
            workspace.mkdir()
            (workspace / ".tmp").mkdir()
            profile = self._native.ensure_appcontainer(identity)
            self._native.grant_path(workspace, profile.sid, read_only=False)
            record = _SandboxRecord(
                sandbox_id=sandbox_id,
                root=sandbox_root,
                workspace=workspace,
                identity=identity,
                profile=profile,
            )
            self._write_manifest(record)
            return record
        except BaseException:
            # Cleanup is deliberately best-effort but the original secure
            # creation error is never hidden and no executable record is kept.
            if profile is not None:
                try:
                    self._native.free_appcontainer_sid(profile)
                finally:
                    try:
                        self._native.delete_appcontainer(identity)
                    except SandboxSecurityError:
                        pass
            if sandbox_root.exists():
                shutil.rmtree(sandbox_root)
            raise

    async def start(self, sandbox_id: str) -> SandboxInfo:
        record = await self._record(sandbox_id)
        async with record.lock:
            if record.state == SandboxState.RUNNING:
                raise SandboxStateError("cannot start a sandbox while a command is running")
            if record.state == SandboxState.READY:
                return self._info(record)
            # Reassert the native ACL so external ACL removal cannot turn into
            # silent partial functionality after an application restart.
            await asyncio.to_thread(
                self._native.grant_path,
                record.root / "workspace",
                record.profile.sid,
                read_only=False,
            )
            if record.workspace_path is not None:
                interrupted = await self._security_mutation(self._grant_workspace, record)
                if interrupted:
                    await self._security_mutation(self._revoke_workspace, record)
                    self._write_manifest(record)
                    raise asyncio.CancelledError
            record.stop_requested = False
            record.state = SandboxState.READY
            try:
                self._write_manifest(record)
            except BaseException:
                record.state = SandboxState.STOPPED
                if record.workspace_path is not None:
                    await asyncio.to_thread(self._revoke_workspace, record)
                raise
        await self._emit_state(record)
        return self._info(record)

    async def configure(
        self,
        sandbox_id: str,
        *,
        workspace_path: str | None,
        workspace_access: ResourceAccess,
    ) -> SandboxInfo:
        record = await self._record(sandbox_id)
        try:
            access = ResourceAccess(workspace_access)
        except (TypeError, ValueError) as exc:
            raise SandboxValidationError("invalid workspace access") from exc
        async with record.lock:
            if record.state != SandboxState.STOPPED:
                raise SandboxStateError("stop the sandbox before changing its workspace")
            workspace = (
                record.root / "workspace"
                if workspace_path is None
                else await asyncio.to_thread(self._validate_workspace, workspace_path)
            )
            old = (record.workspace, record.workspace_path, record.workspace_access)
            if record.workspace_path is not None:
                await asyncio.to_thread(self._revoke_workspace, record)
            record.workspace = workspace
            record.workspace_path = str(workspace) if workspace_path is not None else None
            record.workspace_access = access
            try:
                self._write_manifest(record)
            except BaseException:
                record.workspace, record.workspace_path, record.workspace_access = old
                raise
        await self._emit_state(record)
        return self._info(record)

    async def execute(
        self,
        sandbox_id: str,
        argv: Any,
        *,
        timeout_seconds: float | None = None,
        env: Any = None,
    ) -> CommandResult:
        record = await self._record(sandbox_id)
        command = self._validate_argv(argv)
        timeout = (
            self._limits.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if timeout <= 0:
            raise SandboxValidationError("timeout_seconds must be positive")

        async with record.lock:
            if record.state != SandboxState.READY:
                raise SandboxStateError(
                    f"sandbox {sandbox_id} must be ready, not {record.state.value}"
                )
            if record.workspace_path is not None:
                self._assert_workspace_identity(record)
                await asyncio.to_thread(self._validate_workspace, record.workspace_path)
            storage = record.root / "workspace"
            environment = minimal_windows_environment(
                record.workspace, env, storage_directory=storage
            )
            record.state = SandboxState.RUNNING
            record.active_command = command
            record.cancel_event = threading.Event()
            record.stop_requested = False
            record.command_done.clear()
            try:
                self._write_manifest(record)
            except BaseException:
                record.state = SandboxState.READY
                record.active_command = None
                record.cancel_event = None
                record.command_done.set()
                raise

        await self._emit_state(record)
        await self._emit(
            SandboxEvent(
                sandbox_id=sandbox_id,
                type=SandboxEventType.COMMAND_STARTED,
                payload={"argv": list(command), "timeout_seconds": timeout},
            )
        )
        loop = asyncio.get_running_loop()

        def emit_stream(event_type: SandboxEventType, text: str) -> None:
            self._emit_from_thread(
                loop,
                SandboxEvent(
                    sandbox_id=sandbox_id,
                    type=event_type,
                    payload={"text": text},
                ),
            )

        def job_open(handle: int) -> None:
            with record.thread_lock:
                record.active_job = handle
                should_stop = record.stop_requested
            if should_stop:
                record.cancel_event.set()
                self._native.terminate_job(handle)

        def job_close() -> None:
            with record.thread_lock:
                record.active_job = None

        try:
            (storage / ".tmp").mkdir(exist_ok=True)
            native_task = asyncio.create_task(asyncio.to_thread(
                self._native.run_appcontainer,
                record.profile,
                command,
                cwd=record.workspace,
                environment=environment,
                limits=self._limits,
                timeout_seconds=timeout,
                cancel_event=record.cancel_event,
                on_stdout=lambda text: emit_stream(SandboxEventType.STDOUT, text),
                on_stderr=lambda text: emit_stream(SandboxEventType.STDERR, text),
                on_job_open=job_open,
                on_job_close=job_close,
            ))
            try:
                native_result: NativeCommandResult = await asyncio.shield(native_task)
            except asyncio.CancelledError:
                # Cancelling to_thread does not stop its native process. Keep
                # the Job and record alive until the complete tree is gone.
                record.stop_requested = True
                record.cancel_event.set()
                with record.thread_lock:
                    job = record.active_job
                if job is not None:
                    await asyncio.to_thread(self._native.terminate_job, job)
                try:
                    await asyncio.shield(native_task)
                finally:
                    raise
        except BaseException as exc:
            async with record.lock:
                record.state = (
                    SandboxState.STOPPED
                    if record.stop_requested
                    else SandboxState.ERROR
                )
                record.active_command = None
                record.cancel_event = None
                record.command_done.set()
                if record.workspace_path is not None:
                    await asyncio.to_thread(self._revoke_workspace, record)
                self._write_manifest(record)
            await self._emit(
                SandboxEvent(
                    sandbox_id=sandbox_id,
                    type=SandboxEventType.RUNTIME_ERROR,
                    payload={"error": str(exc)},
                )
            )
            await self._emit_state(record)
            raise

        result = CommandResult(
            sandbox_id=sandbox_id,
            argv=command,
            exit_code=native_result.exit_code,
            stdout=native_result.stdout,
            stderr=native_result.stderr,
            duration_seconds=native_result.duration_seconds,
            timed_out=native_result.timed_out,
            cancelled=native_result.cancelled,
        )
        async with record.lock:
            record.state = (
                SandboxState.STOPPED if record.stop_requested else SandboxState.READY
            )
            record.active_command = None
            record.cancel_event = None
            record.command_done.set()
            self._write_manifest(record)
        await self._emit(
            SandboxEvent(
                sandbox_id=sandbox_id,
                type=SandboxEventType.COMMAND_FINISHED,
                payload={
                    "argv": list(command),
                    "exit_code": result.exit_code,
                    "duration_seconds": result.duration_seconds,
                    "timed_out": result.timed_out,
                    "cancelled": result.cancelled,
                },
            )
        )
        await self._emit_state(record)
        return result

    async def terminate(self, sandbox_id: str) -> None:
        record = await self._record(sandbox_id)
        async with record.lock:
            record.stop_requested = True
            cancel = record.cancel_event
            with record.thread_lock:
                job = record.active_job
            if cancel is not None:
                cancel.set()

        if job is not None:
            await asyncio.to_thread(self._native.terminate_job, job)
        if not record.command_done.is_set():
            await record.command_done.wait()
        interrupted = False
        async with record.lock:
            if record.workspace_path is not None:
                try:
                    interrupted = await self._security_mutation(self._revoke_workspace, record)
                except BaseException:
                    record.state = SandboxState.ERROR
                    self._write_manifest(record)
                    raise
            record.state = SandboxState.STOPPED
            self._write_manifest(record)
        await self._emit_state(record)
        if interrupted:
            raise asyncio.CancelledError

    async def attach_resource(
        self,
        sandbox_id: str,
        resource_id: str,
        source: Path,
        relative_path: str,
        access: ResourceAccess,
    ) -> ResourceAttachment:
        self._validate_id(resource_id, "resource_id")
        if not isinstance(access, ResourceAccess):
            try:
                access = ResourceAccess(access)
            except ValueError as exc:
                raise SandboxValidationError(f"invalid resource access: {access}") from exc
        record = await self._record(sandbox_id)
        if record.state == SandboxState.RUNNING:
            await self.terminate(sandbox_id)

        validated_source = self._validate_source(Path(source), record)
        validated_relative = self._validate_relative_path(relative_path)
        target = self._safe_child(record.root / "workspace", *PureWindowsPath(validated_relative).parts)

        async with record.lock:
            if any(
                attachment.source == validated_source
                and existing_id != resource_id
                for existing_id, attachment in record.attachments.items()
            ):
                raise SandboxValidationError(
                    "a resource may only be attached once per sandbox"
                )
            if any(
                attachment.relative_path.casefold() == validated_relative.casefold()
                and existing_id != resource_id
                for existing_id, attachment in record.attachments.items()
            ):
                raise SandboxValidationError(
                    f"attachment path is already in use: {validated_relative}"
                )
            existing = record.attachments.get(resource_id)
            if existing is not None:
                await asyncio.to_thread(self._detach_sync, record, existing)

            attachment = ResourceAttachment(
                sandbox_id=sandbox_id,
                resource_id=resource_id,
                source=validated_source,
                relative_path=validated_relative,
                access=access,
            )
            await asyncio.to_thread(self._attach_sync, record, attachment, target)
            record.attachments[resource_id] = attachment
            self._write_manifest(record)
        await self._emit(
            SandboxEvent(
                sandbox_id=sandbox_id,
                type=SandboxEventType.RESOURCE_ATTACHED,
                payload={
                    "resource_id": resource_id,
                    "relative_path": validated_relative,
                    "access": access.value,
                },
            )
        )
        return attachment

    def _attach_sync(
        self, record: _SandboxRecord, attachment: ResourceAttachment, target: Path
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise SandboxValidationError(f"attachment target already exists: {target}")
        read_only = attachment.access == ResourceAccess.READ_ONLY
        try:
            # Managed storage shares a volume.  Refuse to copy on hard-link
            # failure because copies would break read/write synchronization.
            os.link(attachment.source, target)
            # Apply after linking: Windows can inherit target-parent ACEs onto
            # the shared file security descriptor as the link is created.
            if read_only:
                self._native.protect_path_acl(attachment.source)
                self._native.revoke_path(attachment.source, record.profile.sid)
            self._native.grant_path(
                attachment.source, record.profile.sid, read_only=read_only
            )
        except BaseException:
            target.unlink(missing_ok=True)
            try:
                self._native.revoke_path(attachment.source, record.profile.sid)
            except SandboxSecurityError:
                pass
            if read_only:
                try:
                    self._native.unprotect_path_acl(attachment.source)
                except SandboxSecurityError:
                    pass
            raise

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        record = await self._record(sandbox_id)
        if record.state == SandboxState.RUNNING:
            await self.terminate(sandbox_id)
        async with record.lock:
            attachment = record.attachments.get(resource_id)
            if attachment is None:
                raise SandboxNotFoundError(f"resource is not attached: {resource_id}")
            await asyncio.to_thread(self._detach_sync, record, attachment)
            del record.attachments[resource_id]
            self._write_manifest(record)
        await self._emit(
            SandboxEvent(
                sandbox_id=sandbox_id,
                type=SandboxEventType.RESOURCE_DETACHED,
                payload={"resource_id": resource_id},
            )
        )

    def _detach_sync(
        self, record: _SandboxRecord, attachment: ResourceAttachment
    ) -> None:
        target = self._safe_child(
            record.root / "workspace", *PureWindowsPath(attachment.relative_path).parts
        )
        # The process tree is already gone before ACL revocation, closing the
        # open-handle loophole in permission revocation.
        self._native.revoke_path(attachment.source, record.profile.sid)
        read_only = attachment.access == ResourceAccess.READ_ONLY
        if read_only:
            try:
                self._native.unprotect_path_acl(attachment.source)
            except SandboxSecurityError:
                self._native.grant_path(
                    attachment.source, record.profile.sid, read_only=True
                )
                raise
        try:
            target.unlink(missing_ok=True)
        except OSError:
            # Re-grant on unlink failure so manifest and security state do not
            # silently disagree.  Surface the filesystem error to the caller.
            if read_only:
                self._native.protect_path_acl(attachment.source)
                self._native.revoke_path(attachment.source, record.profile.sid)
            self._native.grant_path(
                attachment.source,
                record.profile.sid,
                read_only=read_only,
            )
            raise

    async def destroy(self, sandbox_id: str) -> None:
        record = await self._record(sandbox_id)
        await self.terminate(sandbox_id)
        async with record.lock:
            await asyncio.to_thread(self._destroy_sync, record)
        async with self._records_lock:
            self._records.pop(sandbox_id, None)

    def _destroy_sync(self, record: _SandboxRecord) -> None:
        for attachment in tuple(record.attachments.values()):
            self._detach_sync(record, attachment)
        record.attachments.clear()
        self._native.revoke_path(record.root / "workspace", record.profile.sid)
        self._native.free_appcontainer_sid(record.profile)
        self._native.delete_appcontainer(record.identity)
        self._assert_within(record.root, self._sandboxes_root)
        shutil.rmtree(record.root)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        return self._info(await self._record(sandbox_id))

    async def _record(self, sandbox_id: str) -> _SandboxRecord:
        self._validate_id(sandbox_id, "sandbox_id")
        async with self._records_lock:
            record = self._records.get(sandbox_id)
            if record is not None:
                return record
            root = self._safe_child(self._sandboxes_root, sandbox_id)
            manifest = root / _MANIFEST_NAME
            if not manifest.is_file():
                raise SandboxNotFoundError(f"sandbox not found: {sandbox_id}")
            record = await asyncio.to_thread(self._load_manifest, manifest)
            self._records[sandbox_id] = record
            return record

    def _load_manifest(self, manifest: Path) -> _SandboxRecord:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SandboxSecurityError(f"invalid sandbox manifest: {manifest}") from exc
        sandbox_id = data.get("sandbox_id")
        self._validate_id(sandbox_id, "sandbox_id")
        root = manifest.parent.resolve()
        expected = self._safe_child(self._sandboxes_root, sandbox_id)
        if root != expected:
            raise SandboxSecurityError("sandbox manifest identity/path mismatch")
        workspace = self._safe_child(root, "workspace")
        if not workspace.is_dir():
            raise SandboxSecurityError("sandbox workspace is missing")
        identity = self._identity(sandbox_id)
        if data.get("identity") != identity:
            raise SandboxSecurityError("sandbox native identity mismatch")
        profile = self._native.ensure_appcontainer(identity)
        attachments: dict[str, ResourceAttachment] = {}
        try:
            workspace_path = data.get("workspace_path")
            workspace_access = ResourceAccess(data.get("workspace_access", "read_write"))
            workspace_authorized = data.get("workspace_authorized", False)
            identity_data = data.get("workspace_identity")
            if type(workspace_authorized) is not bool or (
                identity_data is not None and (
                    not isinstance(identity_data, list) or len(identity_data) != 2
                    or any(type(value) is not int for value in identity_data)
                )
            ) or (workspace_authorized and (workspace_path is None or identity_data is None)):
                raise SandboxValidationError("invalid persisted workspace authorization")
            # Keep the binding available for cleanup even if the user moved or
            # removed the folder while the application was not running.
            if workspace_path is not None:
                workspace = self._validate_workspace_name(workspace_path)
            for raw in data.get("attachments", []):
                resource_id = raw["resource_id"]
                self._validate_id(resource_id, "resource_id")
                source = self._safe_child(self._managed_root, *Path(raw["source"]).parts)
                relative = self._validate_relative_path(raw["relative_path"])
                access = ResourceAccess(raw["access"])
                if not source.is_file():
                    raise SandboxSecurityError(f"attached source is missing: {source}")
                attachments[resource_id] = ResourceAttachment(
                    sandbox_id=sandbox_id,
                    resource_id=resource_id,
                    source=source,
                    relative_path=relative,
                    access=access,
                )
        except (KeyError, TypeError, ValueError, SandboxValidationError) as exc:
            self._native.free_appcontainer_sid(profile)
            raise SandboxSecurityError("sandbox manifest attachment is invalid") from exc
        # A backend process restart closes its Job handles (KILL_ON_JOB_CLOSE),
        # therefore no command can still be live when this record is reloaded.
        record = _SandboxRecord(
            sandbox_id=sandbox_id,
            root=root,
            workspace=workspace,
            identity=identity,
            profile=profile,
            state=SandboxState.STOPPED,
            attachments=attachments,
            workspace_path=workspace_path,
            workspace_access=workspace_access,
            workspace_authorized=workspace_authorized,
            workspace_identity=(
                tuple(identity_data) if identity_data is not None else None
            ),
        )
        if record.workspace_path is not None:
            try:
                self._revoke_workspace(record)
                self._write_manifest(record)
            except BaseException:
                self._native.free_appcontainer_sid(profile)
                raise
        return record

    def _write_manifest(self, record: _SandboxRecord) -> None:
        state = record.state
        # Persisting RUNNING is informative only; reload always becomes stopped.
        payload = {
            "version": 2,
            "sandbox_id": record.sandbox_id,
            "identity": record.identity,
            "state": state.value,
            "workspace_path": record.workspace_path,
            "workspace_access": record.workspace_access.value,
            "workspace_authorized": record.workspace_authorized,
            "workspace_identity": record.workspace_identity,
            "attachments": [
                {
                    "resource_id": item.resource_id,
                    "source": str(item.source.relative_to(self._managed_root)),
                    "relative_path": item.relative_path,
                    "access": item.access.value,
                }
                for item in sorted(
                    record.attachments.values(), key=lambda item: item.resource_id
                )
            ],
        }
        temporary = record.root / f"{_MANIFEST_NAME}.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(record.root / _MANIFEST_NAME)

    def _info(self, record: _SandboxRecord) -> SandboxInfo:
        return SandboxInfo(
            sandbox_id=record.sandbox_id,
            state=record.state,
            workspace=record.workspace,
            attachments=tuple(
                sorted(record.attachments.values(), key=lambda item: item.resource_id)
            ),
            security_boundary="windows-appcontainer+ntfs-acl+job-object",
            network_enabled=False,
            active_command=record.active_command,
            runtime_id="windows",
            platform="windows",
            shell=(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"), "/d", "/s", "/c"),
            workspace_path=record.workspace_path,
            workspace_access=record.workspace_access,
            resources_path=record.root / "workspace",
            runtime_locked=True,
        )

    def _validate_workspace_name(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise SandboxValidationError("workspace_path must be an absolute folder path")
        path = Path(raw)
        if not path.is_absolute() or raw.startswith(("\\\\", "//")):
            raise SandboxValidationError("workspace must be an absolute local folder")
        if any(part in {".", ".."} or part.rstrip(" .") != part for part in path.parts[1:]):
            raise SandboxValidationError("workspace path contains ambiguous components")
        # Compare lexical paths before resolving: resolving first would hide a
        # junction in the path and accidentally authorize its target.
        path = Path(os.path.abspath(path))
        protected = {Path(path.anchor), Path.home().resolve(), self._managed_root}
        for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if value := os.environ.get(key):
                protected.add(Path(value).resolve())
        if any(path == item or self._is_within(item, path) for item in protected):
            raise SandboxValidationError("choose a project folder, not a drive, home or system root")
        if self._is_within(path, self._managed_root):
            raise SandboxValidationError("workspace may not overlap managed sandbox storage")
        for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if value := os.environ.get(key):
                if self._is_within(path, Path(value).resolve()):
                    raise SandboxValidationError("workspace may not be a system folder")
        return path

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        metadata = path.lstat()
        return stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & 0x400
        )

    def _validate_workspace(self, raw: str) -> Path:
        path = self._validate_workspace_name(raw)
        try:
            for component in (*reversed(path.parents), path):
                if self._is_reparse(component):
                    raise SandboxValidationError("workspace paths may not contain links or reparse points")
            if not path.is_dir():
                raise SandboxValidationError("workspace must be an existing folder")
            self._native.validate_workspace_volume(path)
            for directory, directories, files in os.walk(path, followlinks=False, onerror=self._walk_error):
                for name in (*directories, *files):
                    child = Path(directory) / name
                    metadata = child.lstat()
                    if self._is_reparse(child):
                        raise SandboxValidationError(f"workspace contains a link or reparse point: {child}")
                    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                        raise SandboxValidationError(f"workspace contains a hard-linked file: {child}")
                    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                        raise SandboxValidationError(f"workspace contains a special file: {child}")
        except OSError as exc:
            raise SandboxValidationError(f"workspace cannot be accessed: {path}: {exc}") from exc
        return path

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error

    def _grant_workspace(self, record: _SandboxRecord) -> None:
        self._validate_workspace(record.workspace_path)
        if record.workspace_authorized:
            self._revoke_workspace(record)
        record.workspace_handle = self._native.open_workspace(record.workspace)
        try:
            metadata = record.workspace.stat()
            record.workspace_identity = (metadata.st_dev, metadata.st_ino)
            record.workspace_authorized = True
            # Write the cleanup obligation before granting any authority. A
            # process crash may close handles but must not forget NTFS ACEs.
            self._write_manifest(record)
        except BaseException:
            record.workspace_authorized = False
            self._native.close_workspace(record.workspace_handle)
            record.workspace_handle = None
            raise

        try:
            self._native.grant_workspace(
                record.workspace, record.profile.sid,
                read_only=record.workspace_access == ResourceAccess.READ_ONLY,
                root_handle=record.workspace_handle,
            )
        except BaseException:
            self._revoke_workspace(record)
            self._write_manifest(record)
            raise

    @staticmethod
    async def _security_mutation(action: Any, record: _SandboxRecord) -> bool:
        """Finish ACL mutations before a cancelled caller releases its lock."""
        task = asyncio.create_task(asyncio.to_thread(action, record))
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        task.result()
        return interrupted

    def _assert_workspace_identity(self, record: _SandboxRecord) -> None:
        if not record.workspace_authorized:
            return
        try:
            for component in (*reversed(record.workspace.parents), record.workspace):
                if self._is_reparse(component):
                    raise SandboxSecurityError("authorized workspace path changed to a reparse point")
            metadata = record.workspace.stat()
        except OSError as exc:
            raise SandboxSecurityError(
                "authorized workspace is missing; restore the original folder before stopping or rebinding"
            ) from exc
        if record.workspace_identity != (metadata.st_dev, metadata.st_ino):
            raise SandboxSecurityError(
                "authorized workspace was replaced; restore the original folder before stopping or rebinding"
            )

    def _revoke_workspace(self, record: _SandboxRecord) -> None:
        if not record.workspace_authorized:
            return
        self._assert_workspace_identity(record)
        # Revocation visits links themselves without following them. User files
        # are never unlinked, copied or restored by workspace lifecycle actions.
        self._native.revoke_workspace(
            record.workspace, record.profile.sid, root_handle=record.workspace_handle,
        )
        record.workspace_authorized = False
        if record.workspace_handle is not None:
            self._native.close_workspace(record.workspace_handle)
            record.workspace_handle = None

    def _identity(self, sandbox_id: str) -> str:
        digest = hashlib.sha256(
            f"{self._managed_root}|{sandbox_id}".encode("utf-8")
        ).hexdigest()[:40]
        return f"OpenAgentWorld.{digest}"

    def _validate_source(self, source: Path, record: _SandboxRecord) -> Path:
        if source.is_symlink():
            raise SandboxValidationError("resource symlinks/reparse points are not allowed")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise SandboxValidationError(f"managed resource does not exist: {source}") from exc
        self._assert_within(resolved, self._managed_root)
        if self._is_within(resolved, self._sandboxes_root):
            raise SandboxValidationError("sandbox files cannot be mounted as managed resources")
        if not resolved.is_file():
            raise SandboxValidationError("only regular managed files can be attached")
        if self._is_within(resolved, record.root):
            raise SandboxValidationError("a sandbox cannot attach its own workspace")
        return resolved

    def _validate_relative_path(self, raw: str) -> str:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise SandboxValidationError("relative_path must be a non-empty string")
        path = PureWindowsPath(raw)
        if path.is_absolute() or path.drive or path.root:
            raise SandboxValidationError("attachment path must be relative")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise SandboxValidationError("attachment path may not traverse directories")
        if any(part.rstrip(" .") != part for part in path.parts):
            raise SandboxValidationError("attachment path has ambiguous Windows suffixes")
        return str(path)

    def _validate_argv(self, argv: Any) -> tuple[str, ...]:
        if isinstance(argv, (str, bytes)):
            raise SandboxValidationError("argv must be a sequence, never a shell string")
        try:
            command = tuple(argv)
        except TypeError as exc:
            raise SandboxValidationError("argv must be a sequence") from exc
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise SandboxValidationError("argv must contain non-empty strings")
        if any("\x00" in item for item in command):
            raise SandboxValidationError("argv must be NUL-free")
        return command

    @staticmethod
    def _validate_id(value: Any, label: str) -> None:
        if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
            raise SandboxValidationError(f"invalid {label}: {value!r}")

    def _safe_child(self, parent: Path, *parts: str) -> Path:
        child = parent.joinpath(*parts).resolve(strict=False)
        self._assert_within(child, parent)
        return child

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _assert_within(self, path: Path, parent: Path) -> None:
        if not self._is_within(path, parent.resolve()):
            raise SandboxValidationError(f"path escapes managed storage: {path}")

    async def _emit_state(self, record: _SandboxRecord) -> None:
        await self._emit(
            SandboxEvent(
                sandbox_id=record.sandbox_id,
                type=SandboxEventType.STATE_CHANGED,
                payload={"state": record.state.value},
            )
        )

    async def _emit(self, event: SandboxEvent) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(event)
        if inspect.isawaitable(result):
            await result

    def _emit_from_thread(
        self, loop: asyncio.AbstractEventLoop, event: SandboxEvent
    ) -> None:
        def schedule() -> None:
            loop.create_task(self._emit(event))

        loop.call_soon_threadsafe(schedule)
