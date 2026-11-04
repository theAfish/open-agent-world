"""Profile-scoped context supplied to opt-in Pack factories."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.packs.spec import PackSpec
    from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec, EnvironmentHandle
    from backend.packs.executables import ExecutableEnvironmentManager, ExecutableEnvironmentSpec, ExecutableHandle


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
