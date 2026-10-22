"""Run pytest with local plugin sources available during test collection.

The application loader discovers these same trusted source packages at startup.
Some plugin tests import their package before an application fixture is started,
so collection must not depend on test order or an editable developer install.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tomllib


class FileShards:
    """Partition pytest's actual collection, keeping each file on one runner."""

    def pytest_addoption(self, parser):
        parser.addoption("--shard", metavar="INDEX/COUNT", help="Run one file shard (1-based)")

    def pytest_configure(self, config):
        import pytest

        self.shard = None
        value = config.getoption("shard")
        if value is None:
            return
        try:
            index, count = map(int, value.split("/"))
            if not 1 <= index <= count:
                raise ValueError
        except ValueError:
            raise pytest.UsageError("--shard must be INDEX/COUNT with 1 <= INDEX <= COUNT") from None
        self.shard = index, count

    def pytest_collection_modifyitems(self, config, items):
        if self.shard is None:
            return
        index, count = self.shard
        paths = sorted({item.path for item in items})
        selected = set(paths[index - 1::count])
        deselected = [item for item in items if item.path not in selected]
        items[:] = [item for item in items if item.path in selected]
        config.hook.pytest_deselected(items=deselected)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sources = [str(repository)]
    for manifest in sorted((repository / "plugins").glob("*/pyproject.toml")):
        with manifest.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        if project.get("entry-points", {}).get("open_agent_world.plugins"):
            package = manifest.parent
            sources.append(str(package / "src" if (package / "src").is_dir() else package))
    if os.environ.get("PYTHONPATH"):
        sources.extend(os.environ["PYTHONPATH"].split(os.pathsep))
    os.environ["PYTHONPATH"] = os.pathsep.join(sources)
    sys.path[:0] = sources
    os.chdir(repository)
    arguments = sys.argv[1:] or ["tests", "backend/tests"]
    import pytest

    return pytest.main(arguments, plugins=[FileShards()])


if __name__ == "__main__":
    raise SystemExit(main())
