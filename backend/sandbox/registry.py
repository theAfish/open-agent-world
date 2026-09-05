"""Instance-owned discovery and construction of sandbox execution runtimes."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable

from .base import SandboxBackend, SandboxEventSink
from .models import SandboxSecurityError


@dataclass(frozen=True, slots=True)
class SandboxRuntime:
    id: str
    label: str
    platform: str
    shell: tuple[str, ...]
    available: bool = False
    reason: str | None = None
    supports_workspace: bool = True


@dataclass(frozen=True, slots=True)
class SandboxRuntimeRegistration:
    runtime: SandboxRuntime
    factory: Callable[[], SandboxBackend]
    probe: Callable[[], Awaitable[tuple[bool, str | None]]]
    priority: int = 0


class SandboxRuntimeRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, SandboxRuntimeRegistration] = {}
        self._results: dict[str, SandboxRuntime] = {}
        self._lock = asyncio.Lock()
        self.discovery: Callable[[], Awaitable[None]] | None = None
        self._discovered = False

    def register(self, registration: SandboxRuntimeRegistration) -> None:
        key = registration.runtime.id
        if not key or key == "auto" or key in self._registrations:
            raise ValueError(f"invalid or duplicate sandbox runtime: {key!r}")
        self._registrations[key] = registration
        self._results = {}

    async def catalog(self, *, refresh: bool = False) -> list[SandboxRuntime]:
        async with self._lock:
            if not self._discovered or refresh:
                if self.discovery is not None:
                    await self.discovery()
                self._discovered = True
            # Status polling must not repeatedly wake idle WSL distributions.
            # A user can explicitly refresh after installing dependencies.
            if self._results and not refresh:
                return list(self._results.values())

            async def probe(registration: SandboxRuntimeRegistration) -> SandboxRuntime:
                try:
                    available, reason = await asyncio.wait_for(registration.probe(), 15)
                except (OSError, SandboxSecurityError, TimeoutError) as exc:
                    available, reason = False, str(exc) or "Runtime probe timed out"
                return replace(registration.runtime, available=available, reason=reason)

            results = await asyncio.gather(*(probe(item) for item in self._registrations.values()))
            self._results = {item.id: item for item in results}
            return results

    async def select(self, requested: str) -> SandboxRuntime:
        runtimes = await self.catalog()
        if requested != "auto":
            for runtime in runtimes:
                if runtime.id == requested:
                    return runtime
            return SandboxRuntime(requested, requested, "unknown", (), reason="This runtime is not installed on the backend host")
        available = [item for item in runtimes if item.available]
        if available:
            return max(available, key=lambda item: self._registrations[item.id].priority)
        return SandboxRuntime("auto", "Automatic", "unknown", (), reason="; ".join(
            f"{item.label}: {item.reason}" for item in runtimes
        ) or "No sandbox runtime supports this host")

    async def describe(self, preferred: str = "auto", *, refresh: bool = False) -> dict[str, object]:
        runtimes = await self.catalog(refresh=refresh)
        selected = await self.select(preferred)
        return {"runtimes": [asdict(item) for item in runtimes], "default_runtime": selected.id if selected.available else None}

    def construct(self, runtime_id: str) -> SandboxBackend:
        try:
            return self._registrations[runtime_id].factory()
        except KeyError as exc:
            raise SandboxSecurityError(f"sandbox runtime {runtime_id!r} is unavailable") from exc


def builtin_sandbox_registry(root: Path, event_sink: SandboxEventSink | None) -> SandboxRuntimeRegistry:
    registry = SandboxRuntimeRegistry()
    if sys.platform == "linux":
        from .linux import LinuxSandboxBackend
        registry.register(SandboxRuntimeRegistration(
            SandboxRuntime("linux", "Linux · Bubblewrap", "linux", ("/bin/sh", "-c")),
            lambda: LinuxSandboxBackend(root, event_sink=event_sink),
            LinuxSandboxBackend.probe, 100,
        ))
    elif os.name == "nt":
        from .windows import WindowsSandboxBackend
        from .win32 import WindowsNativeApi
        shell = (str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"), "/d", "/s", "/c")

        async def windows_probe() -> tuple[bool, str | None]:
            try:
                await asyncio.to_thread(WindowsNativeApi)
                return True, None
            except (OSError, SandboxSecurityError) as exc:
                return False, str(exc)

        registry.register(SandboxRuntimeRegistration(
            SandboxRuntime("windows", "Windows · AppContainer", "windows", shell),
            lambda: WindowsSandboxBackend(root, event_sink=event_sink), windows_probe, 50,
        ))

        async def discover_wsl() -> None:
            from .wsl import WslSandboxBackend
            try:
                result = await asyncio.to_thread(subprocess.run,
                    ["wsl.exe", "--list", "--verbose"], capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                output = result.stdout.decode("utf-16-le", errors="replace").lstrip("\ufeff")
                if result.returncode:
                    return
            except (OSError, subprocess.TimeoutExpired):
                return
            for line in output.splitlines():
                parts = line.strip().removeprefix("*").strip().rsplit(None, 2)
                if len(parts) != 3 or parts[-1] != "2":
                    continue
                distribution = parts[0]
                if distribution.startswith("docker-desktop"):
                    continue
                runtime_id = f"wsl:{distribution}"
                if runtime_id in registry._registrations:
                    continue
                async def probe(name: str = distribution) -> tuple[bool, str | None]:
                    return await WslSandboxBackend.probe(name)
                registry.register(SandboxRuntimeRegistration(
                    SandboxRuntime(runtime_id, f"WSL2 · {distribution}", "linux", ("/bin/sh", "-c")),
                    lambda name=distribution, key=runtime_id: WslSandboxBackend(
                        root, distribution=name, runtime_id=key, event_sink=event_sink,
                    ), probe, 100,
                ))
        registry.discovery = discover_wsl
    return registry
