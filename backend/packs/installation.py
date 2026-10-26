"""Immutable local versions and transactional selection for the next startup."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
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
        self._requests = {}
        self.path = self.root / "installations.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT NOT NULL, version TEXT NOT NULL, manifest TEXT NOT NULL,
                    digest TEXT NOT NULL, checksums TEXT NOT NULL, PRIMARY KEY(id,version));
                CREATE TABLE IF NOT EXISTS selections (id TEXT PRIMARY KEY, version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS startup_receipts (
                    id TEXT NOT NULL, version TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY(id, version));
                CREATE TABLE IF NOT EXISTS source_states (id TEXT PRIMARY KEY, enrolled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS identities (
                    id TEXT NOT NULL, version TEXT NOT NULL, checksums TEXT NOT NULL, PRIMARY KEY(id,version));
                CREATE TABLE IF NOT EXISTS distribution_owners (id TEXT PRIMARY KEY, owner TEXT NOT NULL);
                INSERT OR IGNORE INTO identities SELECT id,version,checksums FROM versions;
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, pack_id TEXT NOT NULL,
                    version TEXT NOT NULL, state TEXT NOT NULL, error TEXT, updated_at REAL NOT NULL);
            """)
            for row in db.execute('SELECT id,manifest FROM versions').fetchall():
                manifest = PackManifest.model_validate_json(row['manifest'])
                db.execute('INSERT OR IGNORE INTO distribution_owners VALUES (?,?)', (row['id'], manifest.owner_id))
        self._recover_operations()

    def source_enrolled(self, pack_id):
        with self.connect() as db:
            row = db.execute('SELECT enrolled FROM source_states WHERE id=?', (pack_id,)).fetchone()
        return row is None or bool(row['enrolled'])

    def source_specs(self):
        if self.registry is None:
            return {}
        return {spec.distribution_id: spec for owner, spec in self.registry.pack_specs.items()
                if self.registry.pack_sources.get(owner) != 'installed'}

    def resource_ids(self, manifest, source_root=None):
        from backend.packs.lifecycle import environment_resources
        from backend.packs.planning import manifest_spec
        spec = manifest_spec(manifest)
        root = source_root or self.version_path(manifest.id, manifest.version)
        result = environment_resources(spec, root)
        adapter = getattr(manifest, 'adapter', None)
        if adapter:
            result.update('adapter:' + wheel.sha256 for wheel in adapter.wheels)
        if manifest.kind == 'plugin':
            try:
                result.add('modules:' + self._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend])
            except (ValueError, KeyError):
                pass  # Unpublished preparation has no materialized code yet.
        return result

    def source_resource_ids(self, spec):
        from backend.packs.lifecycle import environment_resources
        from backend.packs.adapters import source_adapter_resource_ids
        root = self.registry.pack_roots.get(spec.owner) or spec.source_root
        return environment_resources(spec, root) | source_adapter_resource_ids(spec, self.root.parent)

    def source_module_names(self, spec):
        """Reserve every adapter/engine package without importing the source."""
        result = {entry.split(':', 1)[0].split('.', 1)[0] for entry in spec.entrypoints.values()}
        root = self.registry.pack_roots.get(spec.owner) or spec.source_root
        if root is None:
            return result
        import tomllib
        from backend.packs.spec import MAX_METADATA_BYTES
        root = Path(root).resolve()
        path = root / safe_path(spec.backend.project)
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES):
            raise ValueError(f"Cannot verify source module ownership for {spec.owner}")
        project = tomllib.loads(path.read_text(encoding='utf-8-sig'))
        packages = (project.get('tool', {}).get('hatch', {}).get('build', {})
                    .get('targets', {}).get('wheel', {}).get('packages', []))
        if not isinstance(packages, list) or any(not isinstance(item, str) for item in packages):
            raise ValueError(f"Invalid source package roots for {spec.owner}")
        result.update(Path(safe_path(item)).name for item in packages)
        return result

    def reconcile_resources(self, *, collect=False):
        from backend.packs.lifecycle import ManagedResourceLedger
        ledger = ManagedResourceLedger(self.root.parent)
        # Serialize the authoritative snapshot with selections/enrollment writes.
        # Another host cannot overwrite fresh claims using an older snapshot.
        with self.connect(write=True) as db:
            manifests = self.selected(db)
            selected = {'selected:' + key: self.resource_ids(manifest) for key, manifest in manifests.items()}
            # A partial discovery has no authority to release retained source
            # resources. A healthy restart reconciles this snapshot normally.
            discovery_complete = not (self.registry and (self.registry.load_failures
                or not getattr(self.registry, 'source_discovery_complete', True)))
            sources = ({'source:' + key: self.source_resource_ids(spec) for key, spec in self.source_specs().items()
                       if self.source_enrolled(key)} if discovery_complete else {})
            ledger.replace_claims('selected:', selected)
            # A recovery manager without source discovery has no authority to
            # release source references established by an active application.
            if self.registry is not None:
                if discovery_complete:
                    ledger.replace_claims('source:', sources)
                self.registry.enrolled_pack_owners = {manifest.owner_id for manifest in manifests.values()} | {
                    spec.owner for key, spec in self.source_specs().items() if self.source_enrolled(key)}
                self.registry.pack_profile_id = hashlib.sha256(str(self.root.parent).encode()).hexdigest()
                self.registry.pack_profile_revision = ledger.next_revision()
            terminal = [row['digest'] for row in db.execute("SELECT digest FROM operations WHERE state IN ('failed','completed')")]
            for digest in terminal:
                ledger.release_consumer('pending:' + digest)
        return ledger.collect() if collect and discovery_complete else []

    def _begin_request(self, key):
        from backend.packs.environments import installation_lock
        if key in self._requests:
            raise RuntimeError('This Pack operation is already prepared or running')
        stack = ExitStack()
        stack.enter_context(installation_lock(self.root / 'request-locks' / (key + '.lock'), timeout=0))
        self._requests[key] = stack
        return stack

    def _end_request(self, key):
        if stack := self._requests.pop(key, None):
            stack.close()

    def __del__(self):
        for stack in getattr(self, '_requests', {}).values():
            stack.close()

    def pin_loaded_resources(self):
        if self.registry is None or getattr(self.registry, '_resource_pin_registered', False):
            return
        from backend.packs.lifecycle import ManagedResourceLedger
        resources = set()
        for manifest in self.registry.installed_packs.values():
            resources.update(self.resource_ids(manifest))
        for spec in self.source_specs().values():
            if self.registry.has_plugin(spec.owner):
                resources.update(self.source_resource_ids(spec))
        ManagedResourceLedger(self.root.parent).pin_process(resources, label='loaded-packs')
        self.registry._resource_pin_registered = True

    def uninstall_plan(self, pack_id):
        selected = self.selected()
        manifest = selected.get(pack_id)
        spec = self.source_specs().get(pack_id) if manifest is None else None
        if manifest is None and (spec is None or not self.source_enrolled(pack_id)):
            raise ValueError('Pack is not enrolled')
        owner = manifest.owner_id if manifest else spec.owner
        provided = set(manifest.provided_pack_ids if manifest else spec.packs)
        blockers = []
        for other in selected.values():
            if other.id != pack_id and any(dep.id in provided for dep in other.dependencies.packs):
                blockers.append('Required by Pack ' + other.id)
        for key, candidate in self.source_specs().items():
            if key != pack_id and self.source_enrolled(key):
                if any(not dep.optional and (dep.kind == 'pack' and dep.id in provided
                        or dep.kind == 'owner' and dep.id == owner
                        or dep.kind == 'distribution' and dep.id == pack_id) for dep in candidate.dependencies):
                    blockers.append('Required by Pack ' + key)
                elif self.registry.has_plugin(candidate.owner):
                    descriptor = next(item for item in self.registry.plugins() if item.id == candidate.owner)
                    if owner in descriptor.requires_plugins:
                        blockers.append('Required by Pack ' + key)
        self.reconcile_resources()
        from backend.packs.lifecycle import ManagedResourceLedger
        resources = self.resource_ids(manifest) if manifest else self.source_resource_ids(spec)
        rows = ManagedResourceLedger(self.root.parent).preview(resources,
            releasing=('selected:' + pack_id, 'source:' + pack_id))
        environments = manifest.environments if manifest else spec.environments
        for name, declaration in environments.items():
            if declaration.target == 'sandbox' and declaration.management == 'managed':
                rows.append({'id': 'sandbox:' + owner + '/' + name, 'kind': 'sandbox-environment', 'action': 'defer',
                    'reason': 'References are reconciled on the next authorized Sandbox profile command; active commands and other Pack references are retained'})
        loaded = bool(self.registry and self.registry.has_plugin(owner))
        return {'id': pack_id, 'owner': owner, 'can_uninstall': not blockers,
            'blockers': blockers, 'resources': rows, 'restart_required': loaded,
            'source': 'installed' if manifest else self.registry.pack_sources.get(owner, 'development')}

    def restore_source(self, pack_id):
        spec = self.source_specs().get(pack_id)
        if spec is None:
            raise ValueError('Source Pack is unavailable')
        identities = {spec.distribution_id, spec.owner, *spec.packs}
        # Dependencies are checked before enrollment, without importing code.
        from backend.packs.planning import manifest_spec
        def check_selection(selected):
            for item in selected.values():
                if identities & {item.id, item.owner_id, *item.provided_pack_ids}:
                    raise ValueError('A selected distribution already owns this source Pack identity')
                if set(spec.node_types or ()) & set(item.node_types or ()):
                    raise ValueError('A selected distribution already owns a declared source card type')
                if item.kind == 'plugin' and self.source_module_names(spec) & set(item.module_names):
                    raise ValueError('A selected distribution already owns a source Python module')
            available = [candidate for key, candidate in self.source_specs().items()
                         if self.source_enrolled(key) or key == pack_id]
            available.extend(manifest_spec(item) for item in selected.values())
            for dependency in spec.dependencies:
                matches = [candidate for candidate in available if
                           (dependency.kind == 'pack' and dependency.id in candidate.packs
                            or dependency.kind == 'owner' and dependency.id == candidate.owner
                            or dependency.kind == 'distribution' and dependency.id == candidate.distribution_id)]
                if dependency.optional and not matches:
                    continue
                if not matches or not any(Version(item.version) in SpecifierSet(dependency.version) for item in matches):
                    raise ValueError('Restore a compatible dependency first: ' + dependency.id)
        check_selection(self.selected())
        from backend.packs.planning import prepare_environments
        from types import SimpleNamespace
        root = self.registry.pack_roots.get(spec.owner) or spec.source_root
        from backend.packs.adapters import prepare_source_adapter
        from backend.packs.lifecycle import ManagedResourceLedger
        ledger = ManagedResourceLedger(self.root.parent)
        key = 'restore-' + hashlib.sha256(pack_id.encode()).hexdigest()
        stack = self._begin_request(key)
        try:
            consumer = stack.enter_context(ledger.lease_many(self.source_resource_ids(spec), label='restoring-source'))
            prepare_source_adapter(spec, self.root.parent, consumer=consumer)
            prepare_environments(SimpleNamespace(owner_id=spec.owner, environments=spec.environments), root, self.root.parent)
            with self.connect(write=True) as db:
                # Preparation may wait on network/processes. Commit only against
                # the current selection and source graph under its write lock.
                selected = self.selected(db)
                check_selection(selected)
                from backend.packs.adapters import check_adapter_dependencies, source_adapter_declaration
                adapters = [item.adapter for item in selected.values()]
                adapters.extend(source_adapter_declaration(candidate, self.root.parent)
                                for key, candidate in self.source_specs().items()
                                if key == pack_id or (self.registry.has_plugin(candidate.owner) and self.source_enrolled(key)))
                check_adapter_dependencies(adapters, allow_existing=True)
                db.execute('INSERT INTO source_states VALUES (?,1) ON CONFLICT(id) DO UPDATE SET enrolled=1', (pack_id,))
            self.reconcile_resources()
        finally:
            self._end_request(key)
        return self.status()

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
        providers = {}
        external_owners = set()
        identities, modules, node_owners = {}, {}, {}
        for candidate in selected.values():
            for node_type in candidate.node_types or ():
                if node_type in node_owners and node_owners[node_type] != candidate.id:
                    raise ValueError(f"Conflicting node type ownership: {node_type}")
                node_owners[node_type] = candidate.id
            for identity in {candidate.id, candidate.owner_id, *candidate.provided_pack_ids}:
                if identity in identities and identities[identity] != candidate.id:
                    raise ValueError(f"Conflicting distribution/runtime identity: {identity}")
                identities[identity] = candidate.id
            if candidate.kind == 'plugin':
                for module in candidate.module_names:
                    if module in modules:
                        raise ValueError(f"Conflicting backend module ownership: {module}")
                    modules[module] = candidate.id
            if candidate.owner_id in external_owners:
                raise ValueError(f"Duplicate runtime owner: {candidate.owner_id}")
            external_owners.add(candidate.owner_id)
            for provided in candidate.provided_pack_ids:
                if provided in providers:
                    raise ValueError(f"Duplicate provided Pack: {provided}")
                providers[provided] = candidate
        if registry is not None:
            installed = {m.owner_id for m in registry.installed_packs.values()}
            for node in registry.catalog(include_disabled=True).node_types:
                if node.id in node_owners and node.plugin_id not in installed:
                    raise ValueError(f"Node type {node.id} conflicts with bundled/development ownership: {node.plugin_id}")
            source_modules = {}
            for plugin in registry.plugins():
                if plugin.id not in installed:
                    owners[plugin.id] = plugin.id
                    source_spec = registry.pack_specs.get(plugin.id)
                    if source_spec is not None:
                        # A source release can have a distribution identity
                        # distinct from both its runtime owner and its Packs.
                        for identity in {source_spec.distribution_id, *source_spec.packs}:
                            owners[identity] = plugin.id
                        # An adapter may own additional worker/engine packages
                        # that its factory has not imported. Reserve those roots
                        # from static build metadata, without loading any code.
                        for module in self.source_module_names(source_spec):
                            source_modules[module] = plugin.id
                if registry.is_enabled(plugin.id) and plugin.id not in installed:
                    declarations[plugin.id] = registry.runtime_requirements.get(plugin.id, plugin.python_requirements)
            for pack in registry.catalog(include_disabled=True).packs:
                if pack.plugin_id not in installed:
                    source_spec = registry.pack_specs.get(pack.plugin_id)
                    if source_spec and not self.source_enrolled(source_spec.distribution_id):
                        continue
                    bundled[pack.id] = next(p.version for p in registry.plugins() if p.id == pack.plugin_id)
                    owners[pack.id] = pack.plugin_id
            for module, distribution in modules.items():
                if module in source_modules:
                    raise ValueError(f"Backend module {module} in {distribution} conflicts with bundled/development ownership: {source_modules[module]}")
        for manifest in selected.values():
            if {manifest.id, manifest.owner_id, *manifest.provided_pack_ids} & owners.keys():
                raise ValueError(f"Pack ID conflicts with bundled/development ownership: {manifest.id}")
            manifest.check_compatibility()
            enabled = registry is None or not registry.has_plugin(manifest.owner_id) or registry.is_enabled(manifest.owner_id)
            if enabled:
                declarations[manifest.id] = manifest.runtime.sandbox.python
            for dependency in manifest.dependencies.packs:
                candidate = providers.get(dependency.id)
                source_spec = registry.pack_specs.get(owners.get(dependency.id)) if registry else None
                distribution = candidate.id if candidate else (source_spec.distribution_id if source_spec else None)
                if dependency.distribution and distribution != dependency.distribution:
                    raise ValueError(f"Pack {dependency.id} must be provided by {dependency.distribution}")
                version = candidate.version if candidate else bundled.get(dependency.id)
                if version is None or Version(version) not in SpecifierSet(dependency.version):
                    raise ValueError(f"Pack {manifest.id} requires missing/incompatible Pack {dependency.id}{dependency.version}")
                owner = candidate.owner_id if candidate else owners[dependency.id]
                if enabled and registry and registry.has_plugin(owner) and not registry.is_enabled(owner):
                    raise ValueError(f"Pack {manifest.id} requires disabled Pack {dependency.id}")
        pending = {key: {providers[d.id].id for d in m.dependencies.packs if d.id in providers} for key, m in selected.items()}
        while pending:
            ready = {key for key, deps in pending.items() if not deps & pending.keys()}
            if not ready:
                raise ValueError("Cyclic Pack dependencies: " + ", ".join(sorted(pending)))
            pending = {key: deps for key, deps in pending.items() if key not in ready}
        if check_requirements:
            aggregate_requirements(declarations)
        from backend.packs.adapters import check_adapter_dependencies, source_adapter_declaration
        adapters = [manifest.adapter for manifest in selected.values() if manifest.kind == 'plugin']
        if registry is not None:
            adapters.extend(manifest.adapter for manifest in registry.installed_packs.values()
                            if manifest.kind == 'plugin' and selected.get(manifest.id) != manifest)
            for owner, spec in registry.pack_specs.items():
                if (registry.has_plugin(owner) and registry.pack_sources.get(owner) != 'installed'
                        and self.source_enrolled(spec.distribution_id)):
                    adapters.append(source_adapter_declaration(spec, self.root.parent))
        check_adapter_dependencies(adapters, allow_existing=True)

    def inspect(self, data: bytes) -> InspectedPack:
        pack = inspect_archive(data)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM versions WHERE id=? AND version=?", (pack.manifest.id, pack.manifest.version)).fetchone():
                raise ValueError("Pack version is already installed and immutable")
            self._check_identity(db, pack)
            self._check_selection({**self.selected(db), pack.manifest.id: pack.manifest})
            self._check_content(pack)
        return pack

    def _check_content(self, pack: InspectedPack) -> None:
        if pack.manifest.kind == "content":
            from backend.packs.content import content_plugin
            if self.registry is None:
                raise ValueError("Content Pack validation requires a loaded plugin registry")
            content_plugin(pack.manifest, pack.files, self.registry)

    def operations(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM operations ORDER BY updated_at DESC LIMIT 50')]

    def _operation_lock(self, digest):
        return self.root / 'operation-locks' / (digest + '.lock')

    def _recover_operations(self):
        # A second host must never label an installation failed while the first
        # still holds its OS lock. No package commands run during recovery.
        from backend.packs.environments import installation_lock
        with self.connect() as db:
            interrupted = [dict(row) for row in db.execute("SELECT * FROM operations WHERE state NOT IN ('completed','failed')")]
        for row in interrupted:
            try:
                with installation_lock(self.root / 'request-locks' / (row['digest'] + '.lock'), timeout=0), installation_lock(self._operation_lock(row['digest']), timeout=0):
                    with self.connect(write=True) as db:
                        installed = db.execute('SELECT digest FROM versions WHERE id=? AND version=?',
                            (row['pack_id'], row['version'])).fetchone()
                        if not installed and row['state'] == 'selecting':
                            # Publication can outlive a rolled-back SQLite commit.
                            # Preserve the owned orphan for diagnosis, but release
                            # its immutable version path for a verified retry.
                            target = self.version_path(row['pack_id'], row['version'])
                            if target.exists():
                                if target.is_symlink() or not target.resolve().is_relative_to((self.root / 'installed').resolve()):
                                    raise ValueError('Interrupted Pack publication escapes installation root')
                                recovery = self.root / 'staging' / ('interrupted-' + uuid4().hex)
                                recovery.parent.mkdir(parents=True, exist_ok=True)
                                target.rename(recovery)
                        completed = installed and installed['digest'] == row['digest']
                        db.execute("UPDATE operations SET state=?,error=?,updated_at=? WHERE id=? AND state NOT IN ('completed','failed')",
                            ('completed' if completed else 'failed', None if completed else
                             'Installation was interrupted; retry to reuse verified environments', time.time(), row['id']))
            except RuntimeError:
                continue

    def _operation(self, pack, state, error=None):
        with self.connect(write=True) as db:
            previous = db.execute('SELECT id FROM operations WHERE digest=? ORDER BY updated_at DESC LIMIT 1', (pack.digest,)).fetchone()
            operation_id = previous['id'] if previous else uuid4().hex
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,error=excluded.error,updated_at=excluded.updated_at',
                (operation_id, pack.digest, pack.manifest.id, pack.manifest.version, state, error, time.time()))
        return operation_id

    def interrupt_preparation(self, data):
        """Make a cancelled request retryable after its worker has settled."""
        from backend.packs.environments import installation_lock
        digest = hashlib.sha256(data).hexdigest()
        try:
            with installation_lock(self._operation_lock(digest), timeout=0):
                with self.connect(write=True) as db:
                    db.execute("UPDATE operations SET state='failed',error=?,updated_at=? WHERE digest=? AND state='prepared'",
                        ('Installation was interrupted; retry to reuse verified environments', time.time(), digest))
        except RuntimeError:
            pass
        self._end_request(digest)

    def plan(self, data: bytes, *, source='archive') -> dict:
        from backend.packs.planning import plan
        pack = inspect_archive(data)
        result = plan(self, pack.manifest, source=source)
        from backend.packs.build import passive_wheel_spec
        passive = pack.manifest.kind == 'content' or passive_wheel_spec(
            pack.files[pack.manifest.entrypoints.backend], pack.manifest) is not None
        result['restart_required'] = not (passive and self.registry is not None
            and not self.registry.has_plugin(pack.manifest.owner_id))
        result['sha256'] = pack.digest
        try:
            self.inspect(data)
        except (ValueError, OSError) as exc:
            if str(exc) not in result['blockers']:
                result['blockers'].append(str(exc))
        return result

    @staticmethod
    def _check_expected(pack, expected_id=None, expected_version=None, expected_sha256=None):
        if expected_sha256 is not None and pack.digest != expected_sha256:
            raise ValueError("Pack SHA-256 differs from the requested artifact")
        if ((expected_id is not None and pack.manifest.id != expected_id)
                or (expected_version is not None and pack.manifest.version != expected_version)):
            raise ValueError("Pack identity differs from the requested Pack/version")

    def prepare(self, data: bytes, *, expected_id=None, expected_version=None, expected_sha256=None) -> None:
        """Network/process preparation happens before the world's mutation gate."""
        pack = self.inspect(data)
        self._check_expected(pack, expected_id, expected_version, expected_sha256)
        from backend.packs.environments import installation_lock
        self._begin_request(pack.digest)
        try:
            with installation_lock(self._operation_lock(pack.digest)):
                self._prepare(pack)
        except BaseException:
            self._end_request(pack.digest)
            raise

    def _prepare(self, pack):
        from backend.packs.planning import prepare_environments
        self._operation(pack, 'preparing')
        stage = self.root / 'staging' / ('prepare-' + uuid4().hex)
        stage.mkdir(parents=True)
        try:
            for name, content in pack.files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            from backend.packs.lifecycle import ManagedResourceLedger
            ledger = ManagedResourceLedger(self.root.parent)
            resources = self.resource_ids(pack.manifest, stage)
            self._requests[pack.digest].enter_context(ledger.lease_many(resources, label='preparing-pack'))
            for resource in resources:
                ledger.claim('pending:' + pack.digest, resource)
            from backend.packs.adapters import prepare_adapters
            prepare_adapters(pack.manifest.adapter, pack.files, self.root.parent, consumer='pending:' + pack.digest)
            prepare_environments(pack.manifest, stage, self.root.parent)
            self._operation(pack, 'prepared')
        except BaseException as exc:
            self._operation(pack, 'failed', type(exc).__name__)
            raise
        finally:
            if stage.resolve().parent != (self.root / 'staging').resolve():
                raise ValueError('Preparation directory escapes Pack staging')
            shutil.rmtree(stage)

    def install(self, data: bytes, *, expected_id: str | None = None,
                expected_version: str | None = None, expected_sha256: str | None = None,
                prepare_environments: bool = True) -> dict:
        pack = inspect_archive(data)
        manifest = pack.manifest
        self._check_content(pack)
        self._check_expected(pack, expected_id, expected_version, expected_sha256)
        if prepare_environments:
            self.prepare(data)
        from backend.packs.environments import installation_lock
        if pack.digest not in self._requests:
            self._begin_request(pack.digest)
        try:
            with installation_lock(self._operation_lock(pack.digest)):
                return self._install_prepared(pack)
        finally:
            self._end_request(pack.digest)

    def _install_prepared(self, pack):
        manifest = pack.manifest
        self._operation(pack, 'selecting')
        stage = self.root / "staging" / uuid4().hex
        stage.mkdir(parents=True)
        published = None
        try:
            for name, content in pack.files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            from backend.packs.planning import prepare_environments as check_environments
            check_environments(manifest, stage, self.root.parent, prepare=False)
            from backend.packs.adapters import prepared_adapter_paths
            prepared_adapter_paths(manifest.adapter, self.root.parent)
            receipt = self._prepare_startup_receipt(manifest, pack.files, pack.backend_entry,
                                                    consumer='pending:' + pack.digest)
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
                db.execute("INSERT OR REPLACE INTO startup_receipts VALUES (?,?,?)",
                           (manifest.id, manifest.version, json.dumps(receipt)))
                db.execute("INSERT OR IGNORE INTO identities VALUES (?,?,?)", (manifest.id, manifest.version, pack.files['checksums.json'].decode('utf-8')))
                db.execute('INSERT OR IGNORE INTO distribution_owners VALUES (?,?)', (manifest.id, manifest.owner_id))
                db.execute("INSERT INTO selections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version", (manifest.id, manifest.version))
        except BaseException as exc:
            # SQLite rolls back a failed write/commit. Return only the directory
            # published by this operation to staging so a retry stays possible.
            if published is not None:
                published.rename(stage)
            self._operation(pack, 'failed', type(exc).__name__)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        self._operation(pack, 'completed')
        self.reconcile_resources()
        return self.status()

    @staticmethod
    def _check_identity(db, pack: InspectedPack) -> None:
        owner = db.execute('SELECT owner FROM distribution_owners WHERE id=?', (pack.manifest.id,)).fetchone()
        if owner and owner['owner'] != pack.manifest.owner_id:
            raise ValueError('A distribution must preserve its runtime owner across versions and removal')
        previous = db.execute("SELECT checksums FROM identities WHERE id=? AND version=?", (pack.manifest.id, pack.manifest.version)).fetchone()
        if previous and json.loads(previous['checksums']) != json_object(pack.files['checksums.json']):
            raise ValueError("Pack version identity is immutable, including after removal; publish a new version")

    def prepare_activation(self, pack_id: str, version: str):
        with self.connect() as db:
            row = db.execute('SELECT manifest FROM versions WHERE id=? AND version=?', (pack_id, version)).fetchone()
        if row is None:
            raise ValueError('Pack version is not installed')
        manifest = PackManifest.model_validate_json(row['manifest'])
        self.verify_installed(manifest)
        self._check_selection({**self.selected(), pack_id: manifest})
        from backend.packs.lifecycle import ManagedResourceLedger
        ledger = ManagedResourceLedger(self.root.parent)
        consumer = f'activation:{pack_id}:{version}'
        key = self._activation_key(pack_id, version)
        if key in self._requests:
            return
        stack = self._begin_request(key)
        stack.enter_context(ledger.lease_many(self.resource_ids(manifest), label='activating-pack'))
        try:
            from backend.packs.planning import prepare_environments
            from backend.packs.adapters import prepare_adapters
            path = self.version_path(pack_id, version)
            files = {wheel.path: (path / wheel.path).read_bytes() for wheel in manifest.adapter.wheels}
            prepare_adapters(manifest.adapter, files, self.root.parent, consumer=consumer)
            self._prepare_backend_modules(manifest, consumer=consumer)
            prepare_environments(manifest, path, self.root.parent)
        except BaseException:
            self._end_request(key)
            raise
        finally:
            ledger.release_consumer(consumer)

    @staticmethod
    def _activation_key(pack_id, version):
        return 'activate-' + hashlib.sha256(f'{pack_id}@{version}'.encode()).hexdigest()

    def interrupt_activation(self, pack_id, version):
        self._end_request(self._activation_key(pack_id, version))

    def activate(self, pack_id: str, version: str, *, prepare=True) -> dict:
        if prepare:
            self.prepare_activation(pack_id, version)
        try:
            return self._activate_prepared(pack_id, version)
        finally:
            self.interrupt_activation(pack_id, version)

    def _activate_prepared(self, pack_id, version):
        with self.connect() as db:
            row = db.execute("SELECT manifest FROM versions WHERE id=? AND version=?", (pack_id, version)).fetchone()
        if row is not None:
            manifest = PackManifest.model_validate_json(row['manifest'])
            from backend.packs.planning import prepare_environments
            prepare_environments(manifest, self.version_path(pack_id, version), self.root.parent, prepare=False)
        with self.connect(write=True) as db:
            row = db.execute("SELECT manifest FROM versions WHERE id=? AND version=?", (pack_id, version)).fetchone()
            if row is None:
                raise ValueError("Pack version is not installed")
            manifest = PackManifest.model_validate_json(row['manifest'])
            self.verify_installed(manifest)
            self._check_selection({**self.selected(db), pack_id: manifest})
            if manifest.kind == "content":
                from backend.packs.content import content_plugin
                path = self.version_path(pack_id, version)
                content_plugin(manifest, {name: (path / name).read_bytes() for name in manifest.content.paths}, self.registry)
            db.execute("INSERT INTO selections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version", (pack_id, version))
        self.reconcile_resources()
        return self.status()

    def uninstall(self, pack_id: str) -> dict:
        # Loaded code is pinned until process exit. The caller enforces live
        # world/provider usage; retained archives do not pin unused dependencies.
        self.pin_loaded_resources()
        plan = self.uninstall_plan(pack_id)
        if plan['blockers']:
            raise ValueError('; '.join(plan['blockers']))
        with self.connect(write=True) as db:
            selected = self.selected(db)
            if pack_id in selected:
                del selected[pack_id]
                self._check_selection(selected)
                db.execute("DELETE FROM selections WHERE id=?", (pack_id,))
            else:
                db.execute('INSERT INTO source_states VALUES (?,0) ON CONFLICT(id) DO UPDATE SET enrolled=0', (pack_id,))
        self.reconcile_resources(collect=True)
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
                db.execute("DELETE FROM startup_receipts WHERE id=? AND version=?", (pack_id, version))
        except BaseException:
            if trash is not None:
                trash.rename(path)
            raise
        if trash:
            shutil.rmtree(trash)
        return self.status()

    def _prepare_startup_receipt(self, manifest, files, entry, *, consumer):
        """Persist only metadata already accepted by the full archive inspector."""
        receipt = self._startup_metadata(manifest, files, entry)
        if manifest.kind == 'plugin':
            self._materialize_backend(manifest, files[manifest.entrypoints.backend],
                                      receipt['backend_sha256'], consumer=consumer)
        return receipt

    @staticmethod
    def _startup_metadata(manifest, files, entry):
        from email.parser import BytesParser
        import io
        from zipfile import ZipFile
        receipt = {'schema': 1, 'entrypoint': entry, 'backend_sha256': None,
                   'requires_python': '', 'requires': []}
        if manifest.kind == 'content':
            return receipt
        wheel = files[manifest.entrypoints.backend]
        digest = json_object(files['checksums.json'])[manifest.entrypoints.backend]
        receipt['backend_sha256'] = digest
        with ZipFile(io.BytesIO(wheel)) as archive:
            metadata_path = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
            metadata = BytesParser().parsebytes(archive.read(metadata_path))
            declaration = metadata_path.removesuffix('METADATA') + 'oaw.pack.json'
            if declaration in archive.namelist():
                from backend.packs.spec import PackSpec
                spec = PackSpec.model_validate_json(archive.read(declaration))
                if (spec.owner, spec.version, spec.distribution_id, spec.packs, spec.node_types) != (
                        manifest.owner_id, manifest.version, manifest.id, manifest.provided_pack_ids, manifest.node_types):
                    raise ValueError('Wheel contributions differ from distribution ownership')
                if not spec.entrypoints or not (spec.contributions.cards or spec.designs):
                    raise ValueError('Expected advanced entrypoint with declarative contributions')
                if (tuple(spec.entrypoints.values()) != (entry,)
                        or spec.compatibility.plugin_api != manifest.compatibility.plugin_api):
                    raise ValueError('Wheel contribution entrypoint/API differs from the distribution')
                receipt['contribution_spec'] = spec.model_dump(mode='json')
        receipt.update(requires_python=metadata.get('Requires-Python', ''),
                       requires=metadata.get_all('Requires-Dist', []))
        from backend.packs.build import passive_wheel_spec
        passive = passive_wheel_spec(wheel, manifest)
        if passive is not None:
            receipt['passive_spec'] = passive.model_dump(mode='json')
        return receipt

    def _materialize_backend(self, manifest, wheel, digest, *, consumer):
        from backend.packs.modules import materialize_modules
        from backend.packs.lifecycle import ManagedResourceLedger
        ledger = ManagedResourceLedger(self.root.parent)
        target = self.root / 'cache/modules' / digest
        # Claim before preparation so concurrent GC cannot remove the result.
        ledger.register('modules:' + digest, target, 'modules')
        ledger.claim(consumer, 'modules:' + digest)
        return materialize_modules(wheel, manifest, self.root / 'cache/modules', expected_digest=digest)

    def _prepare_backend_modules(self, manifest, *, consumer):
        if manifest.kind == 'plugin':
            digest = self._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend]
            wheel = (self.version_path(manifest.id, manifest.version) / manifest.entrypoints.backend).read_bytes()
            self._materialize_backend(manifest, wheel, digest, consumer=consumer)

    def _startup_receipt(self, manifest):
        with self.connect() as db:
            row = db.execute('SELECT record FROM startup_receipts WHERE id=? AND version=?',
                             (manifest.id, manifest.version)).fetchone()
        return json_object(row['record'].encode()) if row else None

    def startup_entrypoint(self, manifest: PackManifest) -> str | None:
        """Check an installed version, with one full migration for old installs."""
        from backend.packs.modules import check_module_roots, _regular
        path = self.version_path(manifest.id, manifest.version)
        check_module_roots(path, ('manifest.json',), boundary=self.root)
        actual = PackManifest.model_validate(json_object((path / 'manifest.json').read_bytes()))
        if actual != manifest:
            raise ValueError('Installed Pack manifest differs from installation record')
        manifest.check_compatibility()
        receipt = self._startup_receipt(manifest)
        if receipt is None:
            from backend.packs.environments import installation_lock
            from backend.startup_progress import startup_stage
            with installation_lock(self.root / 'startup-locks' / (self._activation_key(manifest.id, manifest.version) + '.lock')):
                receipt = self._startup_receipt(manifest)
                if receipt is None:
                    with startup_stage(f'upgrade Pack installation record ({manifest.id})'):
                        entry = self.verify_installed(manifest, backend_only=True)
                        files = {'checksums.json': json.dumps(self._checksums(manifest.id, manifest.version)).encode()}
                        if manifest.kind == 'plugin':
                            files[manifest.entrypoints.backend] = (path / manifest.entrypoints.backend).read_bytes()
                        receipt = self._prepare_startup_receipt(manifest, files, entry,
                                                               consumer='selected:' + manifest.id)
                        with self.connect(write=True) as db:
                            db.execute('INSERT OR REPLACE INTO startup_receipts VALUES (?,?,?)',
                                       (manifest.id, manifest.version, json.dumps(receipt)))
        if (not isinstance(receipt, dict) or type(receipt.get('schema')) is not int or receipt['schema'] != 1
                or set(receipt) - {'passive_spec', 'contribution_spec'} != {'schema', 'entrypoint', 'backend_sha256', 'requires_python', 'requires'}
                or not isinstance(receipt['requires_python'], str) or not isinstance(receipt['requires'], list)
                or any(not isinstance(value, str) for value in receipt['requires'])):
            raise ValueError('Unsupported Pack startup receipt; verify or restore the installation')
        if manifest.kind == 'content':
            return None
        digest = self._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend]
        if receipt.get('backend_sha256') != digest:
            raise ValueError('Pack startup receipt differs from installation record')
        import re
        entry = receipt.get('entrypoint')
        if not isinstance(entry, str) or not re.fullmatch(re.escape(manifest.module_name) + r'(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', entry):
            raise ValueError('Invalid installed Pack entry point')
        _regular(path / manifest.entrypoints.backend, path, directory=False)
        from backend.packs.archive import check_backend_python
        check_backend_python(receipt['requires_python'])
        return entry

    def verify_integrity(self, manifest: PackManifest) -> None:
        """Explicit full verification; never import, repair or select Pack code."""
        entry = self.verify_installed(manifest)
        files = {'checksums.json': json.dumps(self._checksums(manifest.id, manifest.version)).encode()}
        if manifest.kind == 'plugin':
            files[manifest.entrypoints.backend] = (self.version_path(manifest.id, manifest.version) / manifest.entrypoints.backend).read_bytes()
        receipt = self._startup_receipt(manifest)
        if receipt is not None:
            verified = self._startup_metadata(manifest, files, entry)
            if 'passive_spec' not in receipt:
                verified.pop('passive_spec', None)  # Older verified receipts remain valid.
            if receipt != verified:
                raise ValueError('Pack startup receipt differs from verified archive metadata')
        from backend.packs.adapters import prepared_adapter_paths
        prepared_adapter_paths(manifest.adapter, self.root.parent, read_only=True)
        if manifest.kind == 'plugin':
            from backend.packs.modules import verify_modules
            verify_modules(files[manifest.entrypoints.backend],
                           manifest, self.root / 'cache/modules',
                           expected_digest=self._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend])

    def verify_installed(self, manifest: PackManifest, *, backend_only=False) -> str | None:
        path = self.version_path(manifest.id, manifest.version)
        checksums = self._checksums(manifest.id, manifest.version)
        if not isinstance(checksums, dict) or "manifest.json" not in checksums:
            raise ValueError("Invalid installed Pack checksums")
        for name, digest in checksums.items():
            # Frontend files are verified when served. A missing/broken view must
            # not prevent the backend or other Packs from starting.
            if backend_only and manifest.kind == "plugin" and name not in {'manifest.json', manifest.entrypoints.backend} and not name.startswith(('runtime/', 'backend/dependencies/')):
                continue
            asset = path / safe_path(name)
            if not asset.resolve().is_relative_to(path.resolve()) or asset.is_symlink() or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Installed Pack checksum mismatch: {name}")
        actual = PackManifest.model_validate(json_object((path / "manifest.json").read_bytes()))
        if actual != manifest:
            raise ValueError("Installed Pack manifest differs from installation record")
        manifest.check_compatibility()
        if manifest.kind == "content":
            from backend.packs.content import read_presets
            if not set(manifest.content.paths) <= checksums.keys():
                raise ValueError("Content templates are missing installed checksums")
            from backend.packs.factory_cards import read_recipes
            content_files = {name: (path / name).read_bytes() for name in manifest.content.paths}
            read_presets(manifest, content_files)
            read_recipes(manifest, content_files)
            return None
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
        if loaded is None or loaded.version != version or not self.registry.is_enabled(loaded.owner_id):
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
            row.update(name=manifest.name, owner=manifest.owner_id, source="installed", kind=manifest.kind, creator=manifest.creator.model_dump() if manifest.creator else None,
                       installation="installed", selected=selected.get(row['id']) == manifest,
                       loaded=loaded.get(row['id']) == manifest)
        source_restart = any(self.source_enrolled(key) != self.registry.has_plugin(spec.owner)
                             and not self.registry.unavailable_reason(spec.owner)
                             for key, spec in self.source_specs().items())
        return {"versions": versions, "restart_required": selected != loaded or source_restart,
                "failures": self.registry.load_failures if self.registry else []}
