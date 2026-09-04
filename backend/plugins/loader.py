from __future__ import annotations

from importlib.metadata import entry_points
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PluginRegistry


ENTRY_POINT_GROUP = "open_agent_world.plugins"


def load_plugin_registry() -> PluginRegistry:
    """Load trusted backend plugins registered through Python entry points.

    Each entry point exposes a zero-argument plugin factory. Loading is
    fail-closed: a broken, incompatible, or duplicate plugin prevents startup.
    """

    registry = create_builtin_registry()
    discovered = entry_points()
    selected = (
        discovered.select(group=ENTRY_POINT_GROUP)
        if hasattr(discovered, "select")
        else discovered.get(ENTRY_POINT_GROUP, ())
    )
    for entry_point in sorted(selected, key=lambda item: item.name):
        factory = entry_point.load()
        if not callable(factory):
            raise TypeError(
                f"plugin entry point {entry_point.name!r} must expose a plugin factory"
            )
        plugin = factory()
        registry.install(plugin)
    return registry
