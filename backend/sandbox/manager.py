"""Lazy per-card runtime bindings; external workspaces are never managed data."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import SandboxBackend
from .materialization import RuntimeMount
from .models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxInfo,
    SandboxError, SandboxNotFoundError, SandboxSecurityError, SandboxState,
    SandboxStateError, SandboxValidationError, SandboxNetworkError,
)
from .registry import SandboxRuntimeRegistry


@dataclass(slots=True)
class _Binding:
    sandbox_id: str
    runtime: str = "auto"
    resolved_runtime: str | None = None
    workspace_path: str | None = None
    workspace_access: ResourceAccess = ResourceAccess.READ_WRITE
    provisioned: bool = False
    policy: dict = field(default_factory=dict)
    attachments: dict[str, ResourceAttachment] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    network_error: str | None = None


class SandboxManager(SandboxBackend):
    supports_invocation_environment = True

    def __init__(self, root: Path, registry: SandboxRuntimeRegistry, *, preferred: str = "auto") -> None:
        self.root = root.resolve()
        self.registry = registry
        self.preferred = preferred
        self._bindings_root = self.root / "sandbox-bindings"
        self._bindings: dict[str, _Binding] = {}
        self._backends: dict[str, SandboxBackend] = {}

    def _manifest(self, sandbox_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", sandbox_id):
            raise SandboxValidationError("invalid sandbox ID")
        return self._bindings_root / f"{sandbox_id}.json"

    def _write(self, binding: _Binding) -> None:
        path = self._manifest(binding.sandbox_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "runtime": binding.runtime, "resolved_runtime": binding.resolved_runtime,
                "workspace_path": binding.workspace_path, "workspace_access": binding.workspace_access.value,
                "provisioned": binding.provisioned, "policy": binding.policy}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _binding(self, sandbox_id: str) -> _Binding:
        path = self._manifest(sandbox_id)
        if sandbox_id not in self._bindings:
            if not path.is_file():
                raise SandboxNotFoundError(f"sandbox {sandbox_id!r} not found")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.pop("version") != 1:
                    raise ValueError("unknown binding version")
                data["workspace_access"] = ResourceAccess(data["workspace_access"])
                self._bindings[sandbox_id] = _Binding(sandbox_id, **data)
            except (ValueError, KeyError, TypeError) as exc:
                raise SandboxSecurityError(f"invalid sandbox binding for {sandbox_id!r}") from exc
        return self._bindings[sandbox_id]

    def _backend(self, runtime_id: str) -> SandboxBackend:
        if runtime_id not in self._backends:
            self._backends[runtime_id] = self.registry.construct(runtime_id)
        return self._backends[runtime_id]

    async def create(self, sandbox_id: str) -> SandboxInfo:
        if self._manifest(sandbox_id).exists() or sandbox_id in self._bindings:
            raise SandboxStateError(f"sandbox {sandbox_id!r} already exists")
        binding = _Binding(sandbox_id)
        # Existing AppContainer workspaces retain their identity and bytes.
        if (self.root / "sandboxes" / sandbox_id / "sandbox.json").is_file():
            binding.runtime = "windows"
            binding.resolved_runtime = "windows"
            binding.provisioned = True
        self._write(binding)
        self._bindings[sandbox_id] = binding
        return SandboxInfo(sandbox_id, SandboxState.STOPPED, self.root / "sandboxes" / sandbox_id / "workspace",
                           security_boundary="unprovisioned", runtime_id=binding.resolved_runtime or "auto",
                           platform="unknown", shell=(), runtime_locked=binding.provisioned)

    def validate_workspace(self, workspace_path: str | None) -> str | None:
        if workspace_path is None:
            return None
        path = Path(workspace_path)
        if not path.is_absolute() or "\x00" in workspace_path:
            raise SandboxValidationError("workspace_path must be an absolute directory on the backend host")
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise SandboxValidationError("workspace_path must be an existing directory")
            if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
                raise SandboxValidationError("choose a project folder, not a drive root or your home folder")
            if resolved.is_relative_to(self.root) or self.root.is_relative_to(resolved):
                raise SandboxValidationError("workspace cannot expose application data or contain the application data folder")
            for segment in (path, *path.parents):
                if segment.is_symlink() or (hasattr(segment, "is_junction") and segment.is_junction()):
                    raise SandboxValidationError("workspace path must not traverse symbolic links or junctions")
        except OSError as exc:
            raise SandboxValidationError(f"workspace directory is not accessible: {exc}") from exc
        return str(resolved)

    async def configure_options(self, sandbox_id: str, config: Mapping[str, Any]) -> None:
        binding = self._binding(sandbox_id)
        runtime = str(config.get("runtime", "auto"))
        access = ResourceAccess(config.get("workspace_access", "read_write"))
        policy = {key: config[key] for key in ("network_enabled", "memory_bytes", "active_process_limit", "command_timeout") if key in config}
        selected = await self.registry.select(binding.resolved_runtime or (self.preferred if runtime == "auto" else runtime))
        if policy.get("network_enabled") and "enabled" not in selected.supported_network_modes:
            raise SandboxValidationError(selected.network_reason)
        if policy.get("network_enabled") and not selected.network_available:
            raise SandboxValidationError(selected.network_reason or "Networking prerequisites are missing; refresh runtime discovery after setup")
        policy_changed = policy != binding.policy
        unchanged = not policy_changed and (binding.runtime, binding.workspace_path, binding.workspace_access) == (runtime, config.get("workspace_path"), access)
        if unchanged and not binding.provisioned:
            return
        async with binding.lock:
            # Auto on an existing Sandbox means keeping its pinned runtime,
            # including legacy Windows bindings restored with default config.
            # resolved_runtime remains unchanged, so this never selects a new host.
            if binding.provisioned and runtime != binding.runtime and runtime != "auto":
                raise SandboxStateError("Runtime is fixed after first start. Create a new Sandbox to use another runtime.")
            old_policy = binding.policy
            old = (binding.runtime, binding.workspace_path, binding.workspace_access)
            backend: SandboxBackend | None = None
            if binding.provisioned:
                await self.registry.catalog()
                backend = self._backend(binding.resolved_runtime or "")
                info = await backend.get(sandbox_id)
                if unchanged and (info.workspace_path, info.workspace_access) == (binding.workspace_path, access):
                    return
                if info.state != SandboxState.STOPPED:
                    raise SandboxStateError("Stop the Sandbox before changing its runtime, network or workspace settings")
            workspace = await asyncio.to_thread(self.validate_workspace, config.get("workspace_path"))
            if backend is not None:
                await backend.configure(sandbox_id, workspace_path=workspace, workspace_access=access)
            binding.policy = policy
            binding.network_error = None
            binding.runtime, binding.workspace_path, binding.workspace_access = runtime, workspace, access
            try:
                self._write(binding)
            except BaseException:
                binding.policy = old_policy
                binding.runtime, binding.workspace_path, binding.workspace_access = old
                if backend is not None:
                    await backend.configure(sandbox_id, workspace_path=old[1], workspace_access=old[2])
                raise

    async def configure(self, sandbox_id: str, *, workspace_path: str | None, workspace_access: ResourceAccess) -> SandboxInfo:
        binding = self._binding(sandbox_id)
        await self.configure_options(sandbox_id, {**binding.policy, "runtime": binding.runtime, "workspace_path": workspace_path, "workspace_access": workspace_access})
        return await self.get(sandbox_id)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        binding = self._binding(sandbox_id)
        runtime = await self.registry.select(binding.resolved_runtime or (self.preferred if binding.runtime == "auto" else binding.runtime))
        if binding.provisioned:
            try:
                # A failed fresh probe does not prove an existing command has
                # stopped. Preserve the concrete backend's actual state.
                info = await self._backend(runtime.id).get(sandbox_id)
                return replace(info, **self._policy_info(binding, runtime), runtime_id=runtime.id, runtime_locked=True,
                               available=runtime.available, unavailable_reason=runtime.reason)
            except (SandboxError, OSError) as exc:
                runtime = replace(runtime, available=False, reason=str(exc))
        return SandboxInfo(
            sandbox_id, SandboxState.ERROR if binding.provisioned else SandboxState.STOPPED,
            Path(binding.workspace_path) if binding.workspace_path else self.root / "sandboxes" / sandbox_id / "workspace",
            attachments=tuple(binding.attachments.values()), security_boundary="unprovisioned" if not binding.provisioned else "unavailable",
            runtime_id=runtime.id, platform=runtime.platform, shell=runtime.shell,
            workspace_path=binding.workspace_path, workspace_access=binding.workspace_access,
            available=runtime.available, unavailable_reason=runtime.reason, runtime_locked=binding.provisioned,
            **self._policy_info(binding, runtime),
        )

    async def start(self, sandbox_id: str) -> SandboxInfo:
        binding = self._binding(sandbox_id)
        async with binding.lock:
            runtime = await self.registry.select(binding.resolved_runtime or (self.preferred if binding.runtime == "auto" else binding.runtime))
            if not runtime.available:
                raise SandboxSecurityError(runtime.reason or "sandbox runtime unavailable")
            if binding.policy.get("network_enabled"):
                # Recheck even a cached failure so restored prerequisites can
                # recover without switching runtimes or editing saved policy.
                if "enabled" in runtime.supported_network_modes:
                    runtime = await self.registry.refresh_network(runtime.id)
                if "enabled" not in runtime.supported_network_modes or not runtime.network_available:
                    if runtime.network_status == "setup_failed":
                        raise SandboxNetworkError(runtime.network_reason)
                    raise SandboxSecurityError(runtime.network_reason or "Networking is unavailable for the pinned runtime")
                binding.network_error = None
            backend = self._backend(runtime.id)
            created = False
            if not binding.provisioned:
                try:
                    await backend.get(sandbox_id)
                except SandboxNotFoundError:
                    await backend.create(sandbox_id)
                    created = True
                try:
                    await backend.configure(sandbox_id, workspace_path=binding.workspace_path, workspace_access=binding.workspace_access)
                    # Pin before execution. A failed start can be retried with the same data.
                    binding.resolved_runtime = runtime.id
                    binding.provisioned = True
                    self._write(binding)
                except BaseException:
                    binding.resolved_runtime = None
                    binding.provisioned = False
                    if created:
                        await backend.destroy(sandbox_id)
                    raise
            for attachment in binding.attachments.values():
                await backend.attach_resource(sandbox_id, attachment.resource_id, attachment.source,
                                              attachment.relative_path, attachment.access)
            info = await backend.start(sandbox_id)
            binding.network_error = None
            return replace(info, **self._policy_info(binding, runtime), runtime_id=runtime.id, runtime_locked=True)

    @staticmethod
    def _policy_info(binding, runtime):
        status = runtime.network_status
        if runtime.network_available:
            status = "enabled" if binding.policy.get("network_enabled") else "disabled"
        if binding.network_error and runtime.network_available:
            status = "setup_failed"
        return {"network_enabled": binding.policy.get("network_enabled", False),
            "supported_network_modes": runtime.supported_network_modes,
            "network_available": runtime.network_available,
            "network_status": status,
            "network_reason": (binding.network_error if runtime.network_available else None) or runtime.network_reason}

    async def execute(self, sandbox_id: str, argv: Sequence[str], *, timeout_seconds: float | None = None,
                      env: Mapping[str, str] | None = None,
                      invocation_env: Mapping[str, str] | None = None,
                      runtime_mount: RuntimeMount | None = None) -> CommandResult:
        binding = self._binding(sandbox_id)
        if not binding.provisioned:
            raise SandboxStateError("Start the Sandbox before executing commands")
        options = {"runtime_mount": runtime_mount} if runtime_mount is not None else {}
        backend = self._backend(binding.resolved_runtime or "")
        if binding.policy and backend.supports_execution_policy:
            options["execution_policy"] = dict(binding.policy)
        elif any(binding.policy.get(k, v) != v for k, v in {"network_enabled": False, "memory_bytes": 536870912, "active_process_limit": 16, "command_timeout": 60}.items()):
            raise SandboxValidationError("This runtime does not support configurable execution policy")
        if invocation_env is not None:
            if not backend.supports_invocation_environment:
                raise SandboxValidationError("This Sandbox runtime does not support invocation configuration")
            options["invocation_env"] = invocation_env
        try:
            result = await backend.execute(
                sandbox_id, argv, timeout_seconds=timeout_seconds, env=env, **options)
        except SandboxNetworkError as exc:
            binding.network_error = str(exc)
            raise
        binding.network_error = None
        return result

    async def bundle_status(self, sandbox_id, bundle):
        binding = self._binding(sandbox_id)
        if not binding.provisioned:
            return {"cached": False, "current": False}
        return await self._backend(binding.resolved_runtime or "").bundle_status(sandbox_id, bundle)

    async def prepare_python(self, runtime_id, requirements=(), bootstrap_key=None):
        backend = self._backend(runtime_id)
        if hasattr(backend, "prepare_python"):
            return await backend.prepare_python(requirements, bootstrap_key=bootstrap_key)
        runtime = getattr(backend, "python_runtime", None)
        if runtime is None:
            raise SandboxValidationError("This execution platform has no managed Python runtime")
        return await runtime.prepare(requirements, bootstrap_key=bootstrap_key)

    async def install_python_packages(self, sandbox_id, requirements):
        binding = self._binding(sandbox_id)
        if not binding.provisioned:
            raise SandboxStateError("Start the Sandbox before installing packages")
        return await self.prepare_python(binding.resolved_runtime, requirements)

    async def reset_cache(self, sandbox_id):
        binding = self._binding(sandbox_id)
        if binding.provisioned:
            await self._backend(binding.resolved_runtime or "").reset_cache(sandbox_id)

    async def cancel(self, sandbox_id):
        binding = self._binding(sandbox_id)
        if binding.provisioned:
            await self._backend(binding.resolved_runtime or "").cancel(sandbox_id)

    async def file_operation(self, sandbox_id, operation, **options):
        binding = self._binding(sandbox_id)
        if not binding.provisioned:
            if binding.workspace_path:
                from .files import file_operation, run_file_operation
                return await run_file_operation(file_operation, Path(binding.workspace_path), binding.workspace_access,
                    tuple(binding.attachments.values()), operation, **options)
            raise SandboxStateError("Start once to prepare the managed workspace")
        return await self._backend(binding.resolved_runtime or "").file_operation(sandbox_id, operation, **options)

    async def terminate(self, sandbox_id: str) -> None:
        binding = self._binding(sandbox_id)
        if binding.provisioned:
            await self.registry.catalog()
            await self._backend(binding.resolved_runtime or "").terminate(sandbox_id)

    async def attach_resource(self, sandbox_id: str, resource_id: str, source: Path,
                              relative_path: str, access: ResourceAccess) -> ResourceAttachment:
        binding = self._binding(sandbox_id)
        from pathlib import PurePosixPath
        path = PurePosixPath(relative_path.replace("\\", "/"))
        if (path.is_absolute() or ".." in path.parts or not path.parts
            or ":" in relative_path or "\x00" in relative_path):
            raise SandboxValidationError("attachment path must be a safe relative path")
        relative_path = str(path)
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise SandboxValidationError("managed attachment does not exist") from exc
        if (source.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(self.root)
            or any(resolved.is_relative_to(self.root / name) for name in ("sandbox-bindings", "sandboxes", "sandbox-runtimes", "runtime"))):
            raise SandboxValidationError("attachment must be a regular managed resource")
        source = resolved
        attachment = ResourceAttachment(sandbox_id, resource_id, source, relative_path, access)
        async with binding.lock:
            for existing in binding.attachments.values():
                if existing.resource_id != resource_id and (
                    existing.relative_path.casefold() == relative_path.casefold() or existing.source == resolved
                ):
                    raise SandboxValidationError("attachment source and target must be unique")
            if binding.provisioned:
                await self.registry.catalog()
                attachment = await self._backend(binding.resolved_runtime or "").attach_resource(
                    sandbox_id, resource_id, source, relative_path, access)
            binding.attachments[resource_id] = attachment
        return attachment

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        binding = self._binding(sandbox_id)
        async with binding.lock:
            if binding.provisioned:
                await self.registry.catalog()
                await self._backend(binding.resolved_runtime or "").detach_resource(sandbox_id, resource_id)
            binding.attachments.pop(resource_id, None)

    async def destroy(self, sandbox_id: str) -> None:
        binding = self._binding(sandbox_id)
        async with binding.lock:
            if binding.provisioned:
                await self.registry.catalog()
                try:
                    await self._backend(binding.resolved_runtime or "").destroy(sandbox_id)
                except SandboxNotFoundError:
                    pass
            # Never derive any deletion path from workspace_path.
            self._manifest(sandbox_id).unlink(missing_ok=True)
            self._bindings.pop(sandbox_id, None)
