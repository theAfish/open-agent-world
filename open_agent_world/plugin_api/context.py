"""Profile-scoped context supplied to opt-in Pack factories."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.packs.spec import PackSpec
    from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec, EnvironmentHandle
    from backend.packs.executables import ExecutableEnvironmentManager, ExecutableEnvironmentSpec, ExecutableHandle
    from backend.packs.workers import PackWorkerSpec, PackWorker, PackWorkerHost


@dataclass(frozen=True)
class PluginEnvironment:
    """A declared host-worker environment bound to its existing host provider.

    Construction, status and read never install. Call prepare only from an
    explicit setup action; keep a lease through worker exit and cleanup.
    The provider/spec remain available for provider-specific operations.
    """

    manager: PackEnvironmentManager | ExecutableEnvironmentManager
    spec: PythonEnvironmentSpec | ExecutableEnvironmentSpec

    def status(self) -> dict:
        return self.manager.status(self.spec)

    def read(self) -> EnvironmentHandle | ExecutableHandle:
        from backend.packs.executables import ExecutableEnvironmentManager
        if isinstance(self.manager, ExecutableEnvironmentManager):
            try:
                return self.manager.read(self.spec)
            except RuntimeError:
                if not self.spec.accept_external:
                    raise
                return self.manager.read_external(self.spec)
        return self.manager.read_python(self.spec)

    def prepare(self) -> EnvironmentHandle | ExecutableHandle:
        from backend.packs.executables import ExecutableEnvironmentManager
        if isinstance(self.manager, ExecutableEnvironmentManager):
            return self.manager.prepare(self.spec)
        return self.manager.prepare_python(self.spec)

    def lease(self, handle: EnvironmentHandle | ExecutableHandle):
        return self.manager.lease(handle)


@dataclass(frozen=True)
class PluginContext:
    data_root: Path
    source_root: Path | None = None
    _pack_spec: PackSpec | None = field(default=None, repr=False)
    _worker_host: PackWorkerHost | None = field(default=None, repr=False, compare=False)

    async def start_worker(self, spec: PackWorkerSpec, *, operation_id: str, workspace: Path,
                           log_name: str = 'console.log', interactive: bool = False) -> PackWorker:
        """Start this Pack's fixed worker through the configured host executor.

        Plugin API 1.52. Local paths currently require a shared-filesystem
        executor. No environment preparation or native fallback is performed
        when the host has not explicitly enabled that execution contract.
        Keep the returned handle until close() confirms process cleanup.
        """
        from backend.errors import RuntimeUnavailableError
        from backend.packs.workers import PackWorkerSpec
        if (not isinstance(spec, PackWorkerSpec) or self._pack_spec is None
                or spec.pack_id != self._pack_spec.owner):
            raise ValueError('Worker must belong to this host-loaded Pack')
        declaration = self._pack_spec.environments.get(spec.environment_id)
        if declaration is None or declaration.target != 'host-worker':
            raise ValueError('Worker must reference a declared host-worker environment')
        if self._worker_host is None:
            raise RuntimeUnavailableError('This Pack has no host worker service; local fallback is disabled')
        bound = replace(spec, pack_version=self._pack_spec.version, source_root=self.source_root)
        return await self._worker_host.start(bound, operation_id=operation_id, workspace=workspace,
                                             log_name=log_name, interactive=interactive)

    def environment(self, name: str) -> PluginEnvironment:
        """Resolve this Pack's authoritative declaration by name (Plugin API 1.42).

        Source factories receive the validated source spec; installed factories
        receive the selected archive manifest, with resources rooted in that
        immutable version. Legacy entrypoints without a declaration cannot use
        this lookup. Sandbox environments remain owned by Sandbox profiles.
        """
        from backend.packs.planning import environment_provider
        if self._pack_spec is None or self.source_root is None:
            raise ValueError('Named environments require a host-loaded Pack declaration')
        declaration = self._pack_spec.environments.get(name)
        if declaration is None:
            raise ValueError(f'Pack {self._pack_spec.owner} has no environment {name!r}')
        if declaration.target != 'host-worker' or declaration.management != 'managed':
            raise ValueError('Only managed host-worker environments are available through PluginContext')
        if declaration.platforms and sys.platform not in declaration.platforms:
            raise ValueError(f'Environment {name!r} does not support {sys.platform}')
        manager, spec = environment_provider(self._pack_spec.owner, name, declaration, self.source_root, self.data_root)
        return PluginEnvironment(manager, spec)
