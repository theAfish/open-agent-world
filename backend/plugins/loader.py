from __future__ import annotations

import sys
import tomllib
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PluginRegistry


ENTRY_POINT_GROUP = "open_agent_world.plugins"


def load_plugin_registry(plugin_directory: Path | None = None, *, plugin_directories: tuple[Path, ...] = ()) -> PluginRegistry:
    """Load trusted packages in the project's plugins folder, then installed plugins.

    Each entry point exposes a zero-argument plugin factory. Loading is
    fail-closed: a broken, incompatible, or duplicate plugin prevents startup.
    """

    registry = create_builtin_registry()
    directory = (
        plugin_directory
        if plugin_directory is not None
        else Path(__file__).resolve().parents[2] / "plugins"
    )
    local: list[tuple[EntryPoint, Path]] = []
    for directory in dict.fromkeys((directory, *plugin_directories)):
        if not directory.is_dir():
            continue
        for package in sorted(directory.iterdir()):
            manifest = package / "pyproject.toml"
            if not package.is_dir() or not manifest.is_file():
                continue
            try:
                with manifest.open("rb") as stream:
                    project = tomllib.load(stream).get("project", {})
                declarations = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
                if not isinstance(declarations, dict):
                    raise TypeError(f"{ENTRY_POINT_GROUP} must be a table")
                for name, value in sorted(declarations.items()):
                    if not isinstance(value, str):
                        raise TypeError(f"entry point {name!r} must be a string")
                    local.append((EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP), manifest))
                if declarations:
                    source = str((package / "src" if (package / "src").is_dir() else package).resolve())
                    if source not in sys.path:
                        sys.path.insert(0, source)
            except Exception as exc:
                raise RuntimeError(f"Cannot discover plugin in {manifest}: {exc}") from exc
    discovered = entry_points()
    selected = (
        discovered.select(group=ENTRY_POINT_GROUP)
        if hasattr(discovered, "select")
        else discovered.get(ENTRY_POINT_GROUP, ())
    )
    local_keys = {(entry.name, entry.value) for entry, _ in local}
    candidates = local + [
        (entry, "installed distribution")
        for entry in sorted(selected, key=lambda item: item.name)
        if (entry.name, entry.value) not in local_keys
    ]
    for entry_point, origin in candidates:
        try:
            factory = entry_point.load()
            if not callable(factory):
                raise TypeError("entry point must expose a plugin factory")
            plugin = factory()
            registry.install(plugin)
            if isinstance(origin, Path):
                with origin.open("rb") as stream:
                    manifest = tomllib.load(stream)
                requirements = manifest.get("tool", {}).get("open-agent-world", {}).get("runtime", {}).get("python")
                if requirements is not None:
                    from backend.sandbox.python_runtime import validate_requirements
                    registry.runtime_requirements[plugin.descriptor.id] = tuple(validate_requirements(requirements))
        except Exception as exc:
            raise RuntimeError(f"Cannot load plugin {entry_point.name!r} from {origin}: {exc}") from exc
    return registry
