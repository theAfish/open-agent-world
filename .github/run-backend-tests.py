"""Run pytest with local plugin sources available during test collection.

The application loader discovers these same trusted source packages at startup.
Some plugin tests import their package before an application fixture is started,
so collection must not depend on test order or an editable developer install.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sources = [str(repository)]
    for manifest in sorted((repository / "plugins").glob("*/pyproject.toml")):
        with manifest.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        if project.get("entry-points", {}).get("open_agent_world.plugins"):
            package = manifest.parent
            sources.append(str(package / "src" if (package / "src").is_dir() else package))
    environment = os.environ.copy()
    if environment.get("PYTHONPATH"):
        sources.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(sources)
    arguments = sys.argv[1:] or ["tests", "backend/tests"]
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments],
        cwd=repository,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
