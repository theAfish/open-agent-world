"""Fail-closed Windows implementation of :class:`SandboxBackend`."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
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
            # Reassert the native ACL so external ACL removal cannot turn into
            # silent partial functionality after an application restart.
            await asyncio.to_thread(
                self._native.grant_path,
                record.workspace,
                record.profile.sid,
                read_only=False,
            )
            record.stop_requested = False
            record.state = SandboxState.READY
            self._write_manifest(record)
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
            record.state = SandboxState.RUNNING
            record.active_command = command
            record.cancel_event = threading.Event()
            record.stop_requested = False
            record.command_done.clear()
            self._write_manifest(record)

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
            environment = minimal_windows_environment(record.workspace, env)
            (record.workspace / ".tmp").mkdir(exist_ok=True)
            native_result: NativeCommandResult = await asyncio.to_thread(
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
            )
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
        async with record.lock:
            record.state = SandboxState.STOPPED
            self._write_manifest(record)
        await self._emit_state(record)

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
        target = self._safe_child(record.workspace, *PureWindowsPath(validated_relative).parts)

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
            record.workspace, *PureWindowsPath(attachment.relative_path).parts
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
        self._native.revoke_path(record.workspace, record.profile.sid)
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
        return _SandboxRecord(
            sandbox_id=sandbox_id,
            root=root,
            workspace=workspace,
            identity=identity,
            profile=profile,
            state=SandboxState.STOPPED,
            attachments=attachments,
        )

    def _write_manifest(self, record: _SandboxRecord) -> None:
        state = record.state
        # Persisting RUNNING is informative only; reload always becomes stopped.
        payload = {
            "version": 1,
            "sandbox_id": record.sandbox_id,
            "identity": record.identity,
            "state": state.value,
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
        )

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
