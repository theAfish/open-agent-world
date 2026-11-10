"""Explicit POC setup through the existing Store and Pack installation lifecycle."""
from pathlib import Path
import sys
import tomllib

from packaging.version import Version

from backend.packs.archive import MAX_ARCHIVE_BYTES, inspect_archive
from backend.packs.marketplace import Identity, MarketplaceError
from backend.packs.planning import plan


def read_requirements(path: Path):
    with path.open("rb") as source:
        data = source.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise ValueError("POC Pack requirements exceed 64 KiB")
    document = tomllib.loads(data.decode("utf-8"))
    entries = document.get("packs", [])
    if (set(document) != {"packs"} or not isinstance(entries, list) or len(entries) > 100
            or any(not isinstance(row, dict) or set(row) != {"pack_id", "version"} for row in entries)):
        raise ValueError("POC packs.toml requires [[packs]] entries with pack_id and an exact version")
    requirements = [Identity.model_validate(row) for row in entries]
    if len({row.pack_id for row in requirements}) != len(requirements):
        raise ValueError("POC Pack requirements contain duplicate distributions")
    return requirements


def dependency_status(services, requirements):
    manager = services.pack_installations
    selected = manager.selected()
    sources = manager.source_specs()
    result = []
    for required in requirements:
        installed = selected.get(required.pack_id)
        source = sources.get(required.pack_id)
        version = installed.version if installed else (
            source.version if source and manager.source_enrolled(required.pack_id)
            and services.plugins.is_enabled(source.owner) else None)
        result.append({"pack_id": required.pack_id, "required_version": required.version,
            "installed_version": version,
            "status": "ready" if version == required.version else "missing" if version is None else "version_mismatch"})
    return result


async def synchronize(services, requirements, archives=()):
    manager = services.pack_installations
    required_by_id = {row.pack_id: row for row in requirements}
    supplied = {}
    # Check all explicit archives before installing anything. An unrelated or
    # wrong-version archive must not silently change the developer's profile.
    for path in archives:
        with path.open("rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError("Pack archive exceeds the installation limit")
        pack = inspect_archive(data)
        required = required_by_id.get(pack.manifest.id)
        if required is None or required.version != pack.manifest.version:
            raise ValueError(f"{path.name}: Pack identity/version does not match this POC's packs.toml")
        if required.pack_id in supplied:
            raise ValueError(f"Duplicate archive for {required.pack_id}")
        supplied[required.pack_id] = (pack, data)

    for required in requirements:
        label = f"{required.pack_id}@{required.version}"
        state = dependency_status(services, [required])[0]
        retained = next((row for row in manager.status()["versions"]
                         if (row["id"], row["version"]) == (required.pack_id, required.version)), None)
        override = supplied.get(required.pack_id)
        if override and retained and override[0].digest != retained["digest"]:
            raise ValueError(f"{label}: supplied archive differs from the installed immutable version")
        if state["status"] == "ready":
            print(f"POC Pack {label}: already installed; no download.", file=sys.stderr, flush=True)
            continue
        if state["installed_version"] and Version(state["installed_version"]) > Version(required.version):
            raise ValueError(f"{label}: installed version {state['installed_version']} is newer; "
                             "use a separate --profile or update packs.toml. Setup will not downgrade it.")
        if override:
            pack, data = override
            digest = pack.digest
            manifest = pack.manifest
        else:
            print(f"POC Pack {label}: checking Store...", file=sys.stderr, flush=True)
            try:
                metadata = await services.marketplace.get_version(required.pack_id, required.version)
            except MarketplaceError as exc:
                if exc.status_code == 404:
                    raise ValueError(f"{label} is not published in the configured Store. "
                        "Ask the maintainer to publish this exact version, or rerun setup --pack PATH "
                        "with its team .oawpack. No older version was substituted.") from None
                raise ValueError(f"{label}: {exc} Rerun setup when the Store is reachable, "
                                 "or supply the exact version with --pack PATH.") from None
            digest, manifest = metadata.sha256, metadata.manifest
        if retained and retained["digest"] != digest:
            raise ValueError(f"{label}: Store/archive differs from the installed immutable version")
        blockers = plan(manager, manifest, source="archive" if override else "store")["blockers"]
        if blockers:
            raise ValueError(f"{label}: " + "; ".join(blockers))
        if retained:
            manager.activate(required.pack_id, required.version)
        else:
            if not override:
                print(f"POC Pack {label}: downloading and verifying...", file=sys.stderr, flush=True)
                try:
                    data = await services.marketplace.download(metadata)
                except MarketplaceError as exc:
                    raise ValueError(f"{label}: {exc} Rerun setup to retry.") from None
            print(f"POC Pack {label}: installing...", file=sys.stderr, flush=True)
            manager.install(data, expected_id=required.pack_id, expected_version=required.version,
                            expected_sha256=digest)
        print(f"POC Pack {label}: ready.", file=sys.stderr, flush=True)
    return dependency_status(services, requirements)
