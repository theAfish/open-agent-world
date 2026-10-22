"""Read-only startup checks, run with the same persistent Python as the backend."""
from __future__ import annotations

import argparse
from contextlib import closing
from importlib import metadata
import json
from pathlib import Path
import sqlite3
import sys
import tomllib
from urllib.parse import urlsplit
from urllib.request import url2pathname

from setup import ROOT, backend_python, prerequisites, profile_name


def check_host(root: Path) -> None:
    from packaging.requirements import Requirement
    config = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    requirements = [*project["dependencies"], *config["dependency-groups"]["dev"]]
    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        version = metadata.version(requirement.name)
        if version not in requirement.specifier:
            raise RuntimeError(f"Host dependency {requirement.name} {version} does not satisfy {requirement.specifier}")


def check_plugin(path: Path) -> None:
    project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    distribution = metadata.distribution(project["name"])
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(direct.get("url", ""))
    # url2pathname handles Windows drive letters and percent-encoded spaces.
    location = Path(url2pathname(("//" + url.netloc if url.netloc else "") + url.path))
    if url.scheme != "file" or not direct.get("dir_info", {}).get("editable") or location.resolve() != path.resolve():
        raise RuntimeError(f"Plugin {project['name']} is not installed editable from {path}")
    if project.get("version") and distribution.version != project["version"]:
        raise RuntimeError(f"Plugin {project['name']} metadata is stale")
    installed = {(entry.group, entry.name): entry.value for entry in distribution.entry_points}
    for group, entries in project.get("entry-points", {}).items():
        for name, value in entries.items():
            if installed.get((group, name)) != value:
                raise RuntimeError(f"Plugin {project['name']} entry point {group}/{name} is stale")


def unenrolled_sources(data_root: Path) -> set[str]:
    # Do not instantiate PackInstallationManager here: its constructor recovers
    # operations and creates databases. Startup checking must be read-only.
    database = data_root.resolve() / "packs/installations.sqlite3"
    if not database.is_file():
        return set()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return {row[0] for row in connection.execute("SELECT id FROM source_states WHERE enrolled=0")}


def check_packs(paths: list[Path], data_root: Path) -> None:
    from backend.packs.adapters import _source_adapter_location, check_adapter_dependencies
    from backend.packs.manifest import AdapterDependencies
    from backend.packs.planning import environment_status
    from backend.packs.spec import read_source_pack
    excluded = unenrolled_sources(data_root)
    for path in paths:
        if not (path / "oaw.pack.toml").is_file():
            continue
        spec = read_source_pack(path)
        if spec.distribution_id in excluded:
            continue
        # Inspect the same installation record as activation. Full artifact
        # verification belongs to setup or the explicit Pack verify command.
        location = _source_adapter_location(spec, data_root)
        if location:
            adapter, receipt_root = location
            check_adapter_dependencies([adapter])
            for wheel in adapter.wheels:
                artifact = receipt_root / "packs/adapter-wheels" / wheel.sha256 / ".oaw-wheel.whl"
                if not artifact.is_file():
                    raise RuntimeError(f"Pack {spec.owner} dependency artifact is missing: {artifact}")
        else:
            check_adapter_dependencies([AdapterDependencies(requirements=spec.backend.python)], allow_existing=True)
        for name, declaration in spec.environments.items():
            if declaration.target != "host-worker" or declaration.prepare != "install":
                continue
            status = environment_status(spec.owner, name, declaration, spec.source_root, data_root)
            if status["state"] != "ready":
                raise RuntimeError(f"Pack {spec.owner}/{name} is {status['state']}: {status.get('error') or data_root}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=profile_name)
    parser.add_argument("--plugin-path", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    suffix = f" -Profile {args.profile} / --profile {args.profile}" if args.profile else ""
    try:
        if Path(sys.executable).absolute() != backend_python().absolute():
            raise RuntimeError(f"Use the persistent backend interpreter: {backend_python()}")
        prerequisites(install=False)
        sys.path.insert(0, str(ROOT))
        check_host(ROOT)
        if args.profile:
            from backend.development import profile_path, validate_profile
            data_root = profile_path(args.profile)
            validate_profile(data_root, require_marker=data_root.exists())
        else:
            from backend.config import _default_data_root
            data_root = _default_data_root()
        # Optional Pack metadata/prepared artifacts are checked individually by
        # the loader. A preflight must not prevent opening their recovery UI.
    except (ImportError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        raise SystemExit(f"Source environment is not ready: {exc}\nRun scripts/setup.ps1 or scripts/setup.sh{suffix}; include the same plugin paths.") from exc
    print("Reusing prepared backend and Pack environments (no installation).", flush=True)


if __name__ == "__main__":
    main()
