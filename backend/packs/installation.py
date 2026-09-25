"""Immutable local versions and transactional selection for the next startup."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from backend.packs.archive import InspectedPack, inspect_archive, inspect_wheel, json_object
from backend.packs.manifest import PackManifest, safe_path
from backend.packs.requirements import aggregate_requirements


class PackInstallationManager:
    def __init__(self, data_root: Path, registry=None):
        self.root = data_root.resolve() / "packs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.path = self.root / "installations.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT NOT NULL, version TEXT NOT NULL, manifest TEXT NOT NULL,
                    digest TEXT NOT NULL, checksums TEXT NOT NULL, PRIMARY KEY(id,version));
                CREATE TABLE IF NOT EXISTS selections (id TEXT PRIMARY KEY, version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identities (
                    id TEXT NOT NULL, version TEXT NOT NULL, checksums TEXT NOT NULL, PRIMARY KEY(id,version));
                INSERT OR IGNORE INTO identities SELECT id,version,checksums FROM versions;
            """)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    def version_path(self, pack_id: str, version: str) -> Path:
        safe_path(pack_id)
        safe_path(version)
        if "/" in pack_id or "/" in version:
            raise ValueError("Invalid Pack identity")
        path = self.root / "installed" / pack_id / version
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Pack directory escapes installation root")
        return path

    def selected(self, db=None) -> dict[str, PackManifest]:
        if db is None:
            with self.connect() as connection:
                return self.selected(connection)
        return {row['id']: PackManifest.model_validate_json(row['manifest']) for row in db.execute(
            "SELECT v.* FROM versions v JOIN selections s ON s.id=v.id AND s.version=v.version")}

    def _check_selection(self, selected: dict[str, PackManifest], *, check_requirements=True) -> None:
        registry = self.registry
        bundled = {}
        owners = {}
        declarations = {}
        if registry is not None:
            installed = registry.installed_packs
            for plugin in registry.plugins():
                if plugin.id not in installed:
                    owners[plugin.id] = plugin.id
                if registry.is_enabled(plugin.id) and plugin.id not in installed:
                    declarations[plugin.id] = registry.runtime_requirements.get(plugin.id, plugin.python_requirements)
            for pack in registry.catalog(include_disabled=True).packs:
                if pack.plugin_id not in installed:
                    bundled[pack.id] = next(p.version for p in registry.plugins() if p.id == pack.plugin_id)
                    owners[pack.id] = pack.plugin_id
        for manifest in selected.values():
            if manifest.id in owners:
                raise ValueError(f"Pack ID conflicts with bundled/development ownership: {manifest.id}")
            manifest.check_compatibility()
            enabled = registry is None or not registry.has_plugin(manifest.id) or registry.is_enabled(manifest.id)
            if enabled:
                declarations[manifest.id] = manifest.runtime.sandbox.python
            for dependency in manifest.dependencies.packs:
                candidate = selected.get(dependency.id)
                version = candidate.version if candidate else bundled.get(dependency.id)
                if version is None or Version(version) not in SpecifierSet(dependency.version):
                    raise ValueError(f"Pack {manifest.id} requires missing/incompatible Pack {dependency.id}{dependency.version}")
                owner = dependency.id if candidate else owners[dependency.id]
                if enabled and registry and registry.has_plugin(owner) and not registry.is_enabled(owner):
                    raise ValueError(f"Pack {manifest.id} requires disabled Pack {dependency.id}")
        pending = {key: {d.id for d in m.dependencies.packs if d.id in selected} for key, m in selected.items()}
        while pending:
            ready = {key for key, deps in pending.items() if not deps & pending.keys()}
            if not ready:
                raise ValueError("Cyclic Pack dependencies: " + ", ".join(sorted(pending)))
            pending = {key: deps for key, deps in pending.items() if key not in ready}
        if check_requirements:
            aggregate_requirements(declarations)

    def inspect(self, data: bytes) -> InspectedPack:
        pack = inspect_archive(data)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM versions WHERE id=? AND version=?", (pack.manifest.id, pack.manifest.version)).fetchone():
                raise ValueError("Pack version is already installed and immutable")
            self._check_identity(db, pack)
            self._check_selection({**self.selected(db), pack.manifest.id: pack.manifest})
        return pack

    def install(self, data: bytes, *, expected_id: str | None = None,
                expected_version: str | None = None, expected_sha256: str | None = None) -> dict:
        pack = inspect_archive(data)
        manifest = pack.manifest
        if expected_sha256 is not None and pack.digest != expected_sha256:
            raise ValueError("Pack SHA-256 differs from the requested artifact")
        if ((expected_id is not None and manifest.id != expected_id)
                or (expected_version is not None and manifest.version != expected_version)):
            raise ValueError("Pack identity differs from the requested Pack/version")
        stage = self.root / "staging" / uuid4().hex
        stage.mkdir(parents=True)
        published = None
        try:
            for name, content in pack.files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            with self.connect(write=True) as db:
                target = self.version_path(manifest.id, manifest.version)
                if target.exists() or db.execute("SELECT 1 FROM versions WHERE id=? AND version=?", (manifest.id, manifest.version)).fetchone():
                    raise ValueError("Pack version is already installed and immutable")
                self._check_identity(db, pack)
                self._check_selection({**self.selected(db), manifest.id: manifest})
                target.parent.mkdir(parents=True, exist_ok=True)
                stage.rename(target)
                published = target
                db.execute("INSERT INTO versions VALUES (?,?,?,?,?)", (manifest.id, manifest.version, manifest.model_dump_json(), pack.digest, pack.files['checksums.json'].decode('utf-8')))
                db.execute("INSERT OR IGNORE INTO identities VALUES (?,?,?)", (manifest.id, manifest.version, pack.files['checksums.json'].decode('utf-8')))
                db.execute("INSERT INTO selections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version", (manifest.id, manifest.version))
        except BaseException:
            # SQLite rolls back a failed write/commit. Return only the directory
            # published by this operation to staging so a retry stays possible.
            if published is not None:
                published.rename(stage)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return self.status()

    @staticmethod
    def _check_identity(db, pack: InspectedPack) -> None:
        previous = db.execute("SELECT checksums FROM identities WHERE id=? AND version=?", (pack.manifest.id, pack.manifest.version)).fetchone()
        if previous and json.loads(previous['checksums']) != json_object(pack.files['checksums.json']):
            raise ValueError("Pack version identity is immutable, including after removal; publish a new version")

    def activate(self, pack_id: str, version: str) -> dict:
        with self.connect(write=True) as db:
            row = db.execute("SELECT manifest FROM versions WHERE id=? AND version=?", (pack_id, version)).fetchone()
            if row is None:
                raise ValueError("Pack version is not installed")
            manifest = PackManifest.model_validate_json(row['manifest'])
            self.verify_installed(manifest)
            self._check_selection({**self.selected(db), pack_id: manifest})
            db.execute("INSERT INTO selections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version", (pack_id, version))
        return self.status()

    def uninstall(self, pack_id: str) -> dict:
        # Files and collection metadata remain recoverable; runtime stays loaded
        # until restart. API enforces world/provider/lifecycle usage first.
        with self.connect(write=True) as db:
            selected = self.selected(db)
            if pack_id not in selected:
                raise ValueError("Pack is not selected")
            del selected[pack_id]
            self._check_selection(selected)
            db.execute("DELETE FROM selections WHERE id=?", (pack_id,))
        return self.status()

    def remove_version(self, pack_id: str, version: str) -> dict:
        trash = None
        try:
            with self.connect(write=True) as db:
                if db.execute("SELECT 1 FROM selections WHERE id=? AND version=?", (pack_id, version)).fetchone():
                    raise ValueError("Cannot remove the selected version")
                loaded = self.registry.installed_packs.get(pack_id) if self.registry else None
                if loaded and loaded.version == version:
                    raise ValueError("Cannot remove a loaded version; restart OAW first")
                row = db.execute("SELECT 1 FROM versions WHERE id=? AND version=?", (pack_id, version)).fetchone()
                if row is None:
                    raise ValueError("Pack version is not installed")
                path = self.version_path(pack_id, version)
                if path.exists():
                    destination = self.root / "staging" / uuid4().hex
                    destination.parent.mkdir(exist_ok=True)
                    path.rename(destination)
                    trash = destination
                db.execute("DELETE FROM versions WHERE id=? AND version=?", (pack_id, version))
        except BaseException:
            if trash is not None:
                trash.rename(path)
            raise
        if trash:
            shutil.rmtree(trash)
        return self.status()

    def verify_installed(self, manifest: PackManifest, *, backend_only=False) -> str:
        path = self.version_path(manifest.id, manifest.version)
        checksums = self._checksums(manifest.id, manifest.version)
        if not isinstance(checksums, dict) or "manifest.json" not in checksums:
            raise ValueError("Invalid installed Pack checksums")
        for name, digest in checksums.items():
            # Frontend files are verified when served. A missing/broken view must
            # not prevent the backend or other Packs from starting.
            if backend_only and name not in {'manifest.json', manifest.entrypoints.backend}:
                continue
            asset = path / safe_path(name)
            if not asset.resolve().is_relative_to(path.resolve()) or asset.is_symlink() or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Installed Pack checksum mismatch: {name}")
        actual = PackManifest.model_validate(json_object((path / "manifest.json").read_bytes()))
        if actual != manifest:
            raise ValueError("Installed Pack manifest differs from installation record")
        manifest.check_compatibility()
        return inspect_wheel((path / manifest.entrypoints.backend).read_bytes(), manifest)

    def _checksums(self, pack_id: str, version: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT checksums FROM versions WHERE id=? AND version=?", (pack_id, version)).fetchone()
        if row is None:
            raise ValueError("Pack version is not installed")
        # Bind content to the transactional install record, not to an editable
        # checksum file beside the content it is meant to verify.
        return json_object(row['checksums'].encode('utf-8'))

    def frontend_asset(self, pack_id: str, version: str, name: str) -> Path:
        loaded = self.registry.installed_packs.get(pack_id) if self.registry else None
        if loaded is None or loaded.version != version or not self.registry.is_enabled(pack_id):
            raise ValueError("Pack frontend is not active; restart OAW after installation")
        safe_path(name)
        if not name.startswith(("frontend/", "assets/")):
            raise ValueError("Only frontend/ and assets/ files are public")
        root = self.version_path(pack_id, version)
        path = root / name
        checksums = self._checksums(pack_id, version)
        if name not in checksums or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Pack asset is not in its verified manifest")
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksums[name]:
            raise ValueError("Pack asset checksum mismatch")
        return path

    def status(self) -> dict:
        selected = self.selected()
        loaded = self.registry.installed_packs if self.registry else {}
        with self.connect() as db:
            versions = [dict(row) for row in db.execute("SELECT id,version,manifest,digest FROM versions ORDER BY id,version")]
        for row in versions:
            manifest = PackManifest.model_validate_json(row.pop('manifest'))
            row.update(name=manifest.name, installation="installed", selected=selected.get(row['id']) == manifest,
                       loaded=loaded.get(row['id']) == manifest)
        return {"versions": versions, "restart_required": selected != loaded}
