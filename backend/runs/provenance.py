"""Source fingerprints captured at Run start in existing invocation archives."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess


def runtime_source_snapshot(plugin_components: dict[str, Path]):
    """Capture host and plugin source provenance without exposing host layout.

    Plugin components remain explicit; protected host identities cannot be
    replaced by a plugin-supplied path. This reads files and never imports them.
    """
    import open_agent_world
    backend_root = Path(__file__).resolve().parents[1]
    public_root = Path(open_agent_world.__file__).resolve().parent
    components = {"backend/agents": backend_root / "agents", "backend/runs": backend_root / "runs",
                  "plugin_api": public_root / "plugin_api", "task_graph": public_root / "task_graph.py"}
    if components.keys() & plugin_components.keys():
        raise ValueError("Plugin provenance cannot replace a host component identity")
    return source_snapshot({**components, **plugin_components}, repository=backend_root.parent)


def source_snapshot(components: dict[str, Path], *, repository: Path | None = None):
    files, unavailable = {}, []
    for name, root in components.items():
        if not root.exists():
            unavailable.append(name)
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if (not path.is_file() or any(p in {"__pycache__", ".venv", "node_modules", ".git"} for p in path.parts)
                    or path.suffix not in {".py", ".md", ".toml", ".txt", ".json"}):
                continue
            key = name + "/" + (path.name if root.is_file() else path.relative_to(root).as_posix())
            try:
                files[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                unavailable.append(key)
    revision = None
    if repository is not None:
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True,
                                      capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return {"captured_at": datetime.now(UTC).isoformat(), "git_revision": revision,
            "working_source_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "files": files, "unavailable_files": unavailable,
            "complete": bool(files) and not unavailable,
            "scope": "Source bytes observed at Run start, including uncommitted changes; not inferred from export-time HEAD. Restart the host after source changes to align loaded modules with these bytes."}
