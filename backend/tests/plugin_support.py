from collections.abc import Callable

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
