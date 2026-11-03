"""Small vector marks for inventory faces and zoomed-out instruments."""
from open_agent_world.plugin_api import PluginAsset

PATHS = {
    "digital": '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M7 9v6m4-6v6m6-6v6"/><path d="M14 10v.01M14 14v.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2M12 3v1m9 8h-1M12 21v-1M3 12h1"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4M7 14h2m3 0h2m3 0h.01M7 17h2m3 0h2"/>',
    "countdown": '<circle cx="12" cy="13" r="8"/><path d="M9 2h6M12 2v3m7 1 2-2M12 9v4l-3 2"/>',
    "stopwatch": '<circle cx="12" cy="14" r="8"/><path d="M9 2h6M12 2v4m7 1 2-2M12 9v5m-4 0h.01m8 0h.01"/>',
    "pomodoro": '<circle cx="12" cy="13" r="8"/><path d="M12 10v4l3 2M12 5V2m0 3c1-3 4-3 5-3M9 5 6 3"/>',
    "counter": '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="M5 12h4m6 0h4m-2-2v4m-5-5v6"/>',
}


def assets():
    for kind, paths in PATHS.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
        yield PluginAsset(id=f"time-{kind}", media_type="image/svg+xml", content=svg.encode())
