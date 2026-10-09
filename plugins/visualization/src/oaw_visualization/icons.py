"""Pack-owned chart marks, served through the public asset API."""
from open_agent_world.plugin_api import PluginAsset

PATHS = {
    "graph": '<circle cx="5" cy="5" r="3"/><circle cx="19" cy="8" r="3"/><circle cx="9" cy="19" r="3"/><path d="m8 6 8 1M6 8l2 8m3 1 6-7"/>',
    "line": '<path d="M3 3v18h18M6 15l4-6 5 3 6-8"/>',
    "bar": '<path d="M3 3v18h18M7 17v-5m5 5V6m5 11V9"/>',
    "scatter": '<path d="M3 3v18h18"/><circle cx="7" cy="14" r="1"/><circle cx="12" cy="10" r="1"/><circle cx="17" cy="12" r="1"/><circle cx="19" cy="5" r="1"/>',
    "histogram": '<path d="M3 21h18M5 21v-7h4v7m0 0V5h4v16m0 0V9h4v12m0 0v-5h4v5"/>',
}


def assets():
    for kind, paths in PATHS.items():
        yield PluginAsset(id=f"chart-{kind}", media_type="image/svg+xml", content=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
            f'{paths}</svg>').encode())
