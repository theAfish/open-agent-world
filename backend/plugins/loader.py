from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PluginRegistry


ENTRY_POINT_GROUP = "open_agent_world.plugins"


def load_plugin_registry() -> PluginRegistry:
    """Load trusted backend plugins registered through Python entry points.

    An entry point may expose a callable ``register(registry)`` directly or an
    object with a callable ``register`` attribute. Loading is fail-closed: a
    broken or duplicate plugin prevents startup instead of silently weakening
    graph or capability semantics.
    """

    registry = create_builtin_registry()
    discovered = entry_points()
    selected = (
        discovered.select(group=ENTRY_POINT_GROUP)
        if hasattr(discovered, "select")
        else discovered.get(ENTRY_POINT_GROUP, ())
    )
    for entry_point in sorted(selected, key=lambda item: item.name):
        plugin: Any = entry_point.load()
        register = plugin if callable(plugin) else getattr(plugin, "register", None)
        if not callable(register):
            raise TypeError(
                f"plugin entry point {entry_point.name!r} must expose register(registry)"
            )
        register(registry)
    return registry
