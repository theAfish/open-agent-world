from collections.abc import Callable
from types import SimpleNamespace

from backend.plugins import (
    PLUGIN_API_VERSION,
    PluginDefinition,
    PluginDescriptor,
    PluginRegistration,
    PluginRegistry,
)


def install_test_plugin(
    registry: PluginRegistry,
    plugin_id: str,
    configure: Callable[[PluginRegistration], None],
) -> None:
    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id=plugin_id,
            version="1.0.0",
            plugin_api_version=PLUGIN_API_VERSION,
        ),
        configure=configure,
    ))


def publication_services(config=None):
    """Shared-card metadata for publication tests with mocked business handlers."""
    from backend.plugins import create_builtin_registry
    return SimpleNamespace(plugins=create_builtin_registry(), world=SimpleNamespace(
        get_card=lambda node_id: SimpleNamespace(id=node_id, type='text', state_scope='shared', config=config or {}),
        connections_from=lambda node_id: []))
