"""Editable source projects; all inspection/build operations are non-executing.

Project handles record a host user's explicit folder choice. They grant neither
Agent capabilities nor permission to import the project's code into the host.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile, TemporaryDirectory
import threading
from uuid import uuid4

from backend.errors import ConflictError, NotFoundError
from backend.packs.spec import relative_path, read_source_pack

MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_PROJECT_BYTES = 128 * 1024 * 1024
MAX_FILES = 4096
IGNORED = frozenset({'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules', '__pycache__',
                     '.pytest_cache', '.ruff_cache', '.aws', '.ssh', '.codex', '.agents',
                     '.open-agent-world'})
_LOCK = threading.RLock()


def _private(name: str) -> bool:
    return name in IGNORED or name == '.env' or name.startswith(('.env.', '.oaw-save-')) or name.endswith(('.pyc', '.pyo'))


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def project_file(root: Path, name: str, *, exists: bool = True) -> Path:
    """One portable, symlink-free project-relative path for every operation."""
    relative_path(name)
    if any(_private(part) for part in name.split('/')):
        raise ValueError('Private or generated paths are not editable project resources')
    path = root / name
    for segment in (path, *path.parents):
        if _linked(segment):
            raise ValueError('Project paths must not traverse symbolic links or junctions')
        if segment == root:
            break
    if not path.resolve(strict=exists).is_relative_to(root):
        raise ValueError('Project file escapes its authorized root')
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Project files must not be hard linked')
    return path


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def files(root: Path) -> list[dict]:
    result, total = [], 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if not _private(name))
        for name in [*names, *filenames]:
            if _private(name):
                continue
            path = project_file(root, (Path(directory) / name).relative_to(root).as_posix())
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            if total > MAX_PROJECT_BYTES or len(result) >= MAX_FILES:
                raise ValueError('Project exceeds the 4096 file / 128 MiB inspection limit; move build caches outside it')
            data = path.read_bytes()
            editable = size <= MAX_TEXT_BYTES and b'\0' not in data
            if editable:
                try:
                    data.decode('utf-8-sig')
                except UnicodeDecodeError:
                    editable = False
            result.append({'path': path.relative_to(root).as_posix(), 'size_bytes': len(data),
                           'sha256': _digest(data), 'editable': editable})
    return sorted(result, key=lambda item: item['path'])


def revision(entries: list[dict]) -> str:
    return _digest(json.dumps([(item['path'], item['sha256']) for item in entries], separators=(',', ':')).encode())


def read_file(root: Path, name: str) -> dict:
    path = project_file(root, name)
    if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError('Choose a text source file no larger than 2 MiB')
    data = path.read_bytes()
    if b'\0' in data:
        raise ValueError('Binary resources are preserved but cannot be edited as text')
    return {'path': name, 'content': data.decode('utf-8-sig'), 'sha256': _digest(data)}


def write_file(root: Path, name: str, content: str, expected_sha256: str | None) -> dict:
    data = content.encode('utf-8')
    if len(data) > MAX_TEXT_BYTES or '\0' in content:
        raise ValueError('Source text must be NUL-free and no larger than 2 MiB')
    with _LOCK:
        path = project_file(root, name, exists=False)
        current = _digest(path.read_bytes()) if path.is_file() else None
        if current != expected_sha256:
            raise ConflictError(f'{name} changed outside this editor. Reload and compare before saving.')
        path.parent.mkdir(parents=True, exist_ok=True)
        # A temporary sibling keeps readers from seeing a partially written file.
        with NamedTemporaryFile(dir=path.parent, prefix='.oaw-save-', delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            project_file(root, name, exists=False)
            current = _digest(path.read_bytes()) if path.is_file() else None
            if current != expected_sha256:
                raise ConflictError(f'{name} changed while saving. Reload and compare before saving.')
            if path.is_file():
                temporary_path.chmod(stat.S_IMODE(path.stat().st_mode))
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {'path': name, 'content': content, 'sha256': _digest(data)}


def _python_interfaces(root: Path, entries: list[dict], diagnostics: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Best-effort literal interfaces, deliberately not Python evaluation."""
    cards, relationships = {}, []
    for entry in entries:
        if not entry['path'].endswith('.py') or not entry['editable']:
            continue
        try:
            tree = ast.parse(read_file(root, entry['path'])['content'])
        except SyntaxError as exc:
            diagnostics.append({'severity': 'error', 'path': entry['path'],
                                'line': exc.lineno or 1, 'column': exc.offset or 1,
                                'message': exc.msg})
            continue
        except ValueError as exc:
            diagnostics.append({'severity': 'error', 'path': entry['path'], 'message': str(exc)})
            continue
        constants: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants.setdefault(target.id, set()).add(node.value.value)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            function = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, 'attr', '')
            if function not in {'NodeTypeDefinition', 'RelationshipDefinition', 'replace'}:
                continue
            values = {}
            for keyword in call.keywords:
                if keyword.arg == 'resource_actions' and isinstance(keyword.value, ast.Dict):
                    values['actions'] = [{'id': key.value} for key in keyword.value.keys
                                         if isinstance(key, ast.Constant) and isinstance(key.value, str)]
                try:
                    value = keyword.value
                    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                            and value.func.id == 'frozenset' and len(value.args) == 1 and not value.keywords):
                        value = value.args[0]
                    literal = ast.literal_eval(value)
                    values[keyword.arg] = sorted(literal) if isinstance(literal, set) else literal
                except (ValueError, TypeError):
                    pass
            identifier = values.get('id')
            if function == 'replace' and call.args and isinstance(call.args[0], ast.Subscript):
                index = call.args[0].slice
                if isinstance(index, ast.Constant):
                    identifier = index.value
                elif isinstance(index, ast.Name) and len(constants.get(index.id, ())) == 1:
                    identifier = next(iter(constants[index.id]))
            if not isinstance(identifier, str):
                continue
            values['source_path'] = entry['path']
            if function == 'RelationshipDefinition':
                relationships.append(values)
            else:
                cards[identifier] = {**cards.get(identifier, {}), **values}
    return cards, relationships


def _source_diagnostic(root: Path, error: Exception) -> dict:
    """Locate declaration/design failures without executing project code."""
    import tomllib
    from backend.packs.interfaces import CardInterface
    diagnostic = {'severity': 'error', 'message': str(error)}
    documents = {}
    for name in ('pyproject.toml', 'oaw.pack.toml'):
        try:
            documents[name] = tomllib.loads(read_file(root, name)['content'])
        except (ValueError, OSError) as exc:
            location = re.search(r'at line (\d+), column (\d+)', str(exc))
            return {**diagnostic, 'path': name, **({'line': int(location[1]), 'column': int(location[2])} if location else {})}
    designs = documents['oaw.pack.toml'].get('designs', {})
    for name in designs.values() if isinstance(designs, dict) else ():
        if not isinstance(name, str):
            continue
        try:
            CardInterface.model_validate(json.loads(read_file(root, name)['content']))
        except json.JSONDecodeError as exc:
            return {**diagnostic, 'path': name, 'line': exc.lineno, 'column': exc.colno}
        except (ValueError, OSError):
            return {**diagnostic, 'path': name}
    return diagnostic


def inspect_project(root: Path) -> dict:
    entries = files(root)
    diagnostics, spec = [], None
    try:
        spec = read_source_pack(root)
        if spec.legacy:
            raise ValueError('Open a Schema 3 source project containing oaw.pack.toml')
    except (ValueError, OSError, KeyError) as exc:
        spec = None
        diagnostics.append(_source_diagnostic(root, exc))
    python_cards, relationships = _python_interfaces(root, entries, diagnostics)
    cards = []
    if spec is not None:
        import tomllib
        declaration = tomllib.loads(read_file(root, 'oaw.pack.toml')['content'])
        design_paths = declaration.get('designs', {})
        contributed = {card.id: card.model_dump(mode='json') for card in spec.contributions.cards}
        identifiers = list(dict.fromkeys([*(spec.node_types or ()), *contributed, *python_cards, *design_paths]))
        for identifier in identifiers:
            row = contributed.get(identifier, python_cards.get(identifier, {}))
            design_path = design_paths.get(identifier)
            design = spec.designs[identifier].model_dump(mode='json') if design_path else None
            cards.append({**row, 'id': identifier, 'name': row.get('name') or row.get('label') or identifier,
                          'label': row.get('name') or row.get('label') or identifier,
                          'source_path': python_cards.get(identifier, {}).get('source_path'),
                          'kind': 'python' if spec.entrypoints else 'declarative',
                          'design_path': design_path, 'design': design,
                          'actions': [{'id': design.get('action', 'run')}] if not spec.entrypoints and design
                                     else python_cards.get(identifier, {}).get('actions', []),
                          'frontend': row.get('frontend', {})})
        if spec.entrypoints:
            diagnostics.append({'severity': 'info', 'message':
                'Python interfaces are inspected statically. Dynamic registrations are validated by the external check command or after trusted installation.'})
        if spec.frontend:
            diagnostics.append({'severity': 'info', 'message':
                'Custom frontend source is preserved. Build packages prebuilt dist/frontend assets; rebuild them externally after editing TypeScript or React.'})
    return {'path': str(root), 'name': spec.name if spec else root.name,
            'distribution_id': spec.distribution_id if spec else None, 'version': spec.version if spec else None,
            'revision': revision(entries), 'files': entries, 'cards': cards,
            'relationships': relationships,
            'spec': spec.model_dump(mode='json') if spec else None, 'diagnostics': diagnostics}


def scaffold(root: Path, name: str, distribution_id: str | None = None, design: dict | None = None) -> None:
    if root.exists():
        raise ValueError('Create requires a new directory; existing projects are opened without replacing files')
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'card'
    if not slug[0].isalpha():
        slug = 'card-' + slug
    identifier = distribution_id or f'local.{slug[:60]}-{uuid4().hex[:10]}'
    from backend.packs.manifest import PACK_ID
    if len(identifier) > 120 or not re.fullmatch(PACK_ID, identifier):
        raise ValueError('Choose a valid distribution ID such as local.my-card')
    from backend.packs.factory_models import FaceDesign, FunctionDesign
    design = design or {'face': FaceDesign(title=name).model_dump(mode='json'),
              'function': FunctionDesign().model_dump(mode='json'), 'action': 'run',
              'result_key': 'result', 'surfaces': ['preview', 'body', 'settings', 'workspace']}
    from backend.packs.interfaces import CardInterface
    design = CardInterface.model_validate(design).model_dump(mode='json')
    card_id = identifier + '.card'
    root.mkdir(parents=True)
    (root / 'designs').mkdir()
    (root / 'designs/card.json').write_text(json.dumps(design, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (root / 'pyproject.toml').write_text(f'[project]\nname = {json.dumps(identifier.replace(".", "-"))}\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = []\n', encoding='utf-8')
    (root / 'oaw.pack.toml').write_text(
        f'schema_version = 3\n\n[distribution]\nid = {json.dumps(identifier)}\nname = {json.dumps(name, ensure_ascii=False)}\n'
        f'\n[compatibility]\nplugin_api = "1.39"\n\n[plugin]\nowner = {json.dumps(identifier)}\npacks = [{json.dumps(identifier)}]\n'
        f'\n[designs]\n{json.dumps(card_id)} = "designs/card.json"\n'
        f'\n[[contributions.cards]]\nid = {json.dumps(card_id)}\nname = {json.dumps(name, ensure_ascii=False)}\n', encoding='utf-8')
    (root / 'README.md').write_text(
        f'# {name}\n\nThis is an editable Schema 3 Pack project. Visual edits live in `designs/card.json`.\n'
        '\nInspect: `python -m open_agent_world.pack doctor .`\n'
        '\nBuild: `python -m open_agent_world.pack build-project . card.oawpack`\n'
        '\nValidate trusted code in a disposable profile: `python -m open_agent_world.pack check . --lifecycle`\n'
        '\nPython and custom frontend builds stay in your external editor or authorized Sandbox.\n', encoding='utf-8')


class PackProjects:
    """Use the existing profile settings store only for authorized root handles."""
    def __init__(self, data_root: Path, database):
        self.data_root = Path(data_root).resolve()
        self.directory = self.data_root / 'pack-projects'
        self.database = database

    def _records(self) -> dict:
        with self.database.locked() as connection:
            row = connection.execute("SELECT value_json FROM application_settings WHERE key = 'pack-projects'").fetchone()
        return json.loads(row['value_json']) if row else {}

    def _validate_root(self, raw: str, *, exists: bool = True) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError('Choose an absolute source project directory on the backend host')
        resolved = path.resolve(strict=exists)
        if (exists or resolved.exists()) and not resolved.is_dir():
            raise ValueError('Choose an existing project directory')
        for segment in (path, *path.parents):
            if _linked(segment):
                raise ValueError('Project roots must not traverse links or junctions')
        if resolved.is_relative_to(self.directory) and resolved != self.directory:
            return resolved
        from backend.sandbox.manager import SandboxManager
        from backend.sandbox.registry import SandboxRuntimeRegistry
        from backend.security.execution_folders import inspect_environment_path
        validated, _ = inspect_environment_path(raw, SandboxManager(self.data_root, SandboxRuntimeRegistry()),
                                                kind='folder', allow_missing=not exists)
        return Path(validated)

    def root(self, identifier: str) -> Path:
        record = self._records().get(identifier)
        if record is None:
            raise NotFoundError('Open this Pack project before accessing its files')
        return self._validate_root(record['path'])

    def list(self) -> dict:
        return {'projects': [{'id': key, **value} for key, value in self._records().items()],
                'default_directory': str(self.directory)}

    def open(self, path: str) -> dict:
        root = self._validate_root(path)
        if not (root / 'oaw.pack.toml').is_file() or not (root / 'pyproject.toml').is_file():
            raise ValueError('Open a source directory containing oaw.pack.toml and pyproject.toml, not a compiled archive')
        snapshot = inspect_project(root)
        with self.database.transaction(immediate=True) as connection:
            records = self._records()
            identifier = next((key for key, record in records.items() if record['path'] == str(root)), uuid4().hex)
            records[identifier] = {'path': str(root), 'name': snapshot['name'], 'distribution_id': snapshot['distribution_id']}
            connection.execute("INSERT INTO application_settings (key,value_json) VALUES ('pack-projects',?) "
                               "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(records),))
        return {'id': identifier, **snapshot}

    def create(self, name: str, distribution_id: str | None = None, directory: str | None = None,
               design: dict | None = None) -> dict:
        if directory:
            root = self._validate_root(directory, exists=False)
        else:
            self.directory.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'card'
            root = self.directory / f'{slug}-{uuid4().hex[:8]}'
        scaffold(root, name, distribution_id, design)
        return self.open(str(root))

    def inspect(self, identifier: str) -> dict:
        return {'id': identifier, **inspect_project(self.root(identifier))}

    def add_card(self, identifier: str, name: str, expected_revision: str, design: dict | None = None) -> dict:
        import tomllib
        from packaging.version import Version
        from backend.packs.interfaces import CardInterface
        from backend.packs.factory_models import FaceDesign
        validated = CardInterface.model_validate(design or {'face': FaceDesign(title=name).model_dump(mode='json')})
        with _LOCK:
            checked = self.check(identifier, expected_revision)
            if not checked['ok']:
                raise ValueError('Resolve project diagnostics before adding a Card')
            root = self.root(identifier)
            manifest = read_file(root, 'oaw.pack.toml')
            declaration = tomllib.loads(manifest['content'])
            if checked['spec']['entrypoints'] or declaration.get('plugin', {}).get('node_types') is not None:
                raise ValueError('Add advanced Cards in the source registration and oaw.pack.toml, then reload the project')
            if Version(declaration.get('compatibility', {}).get('plugin_api', '0')) < Version('1.39'):
                raise ValueError('Set compatibility.plugin_api to 1.39 before adding visual Cards to this project')
            if any(not card.get('id') for card in declaration.get('contributions', {}).get('cards', [])):
                raise ValueError('Give existing Cards explicit IDs in oaw.pack.toml before adding another Card')
            card_id = checked['spec']['owner'] + '.card-' + uuid4().hex[:8]
            text = manifest['content'].rstrip() + f'\n\n[[contributions.cards]]\nid = {json.dumps(card_id)}\nname = {json.dumps(name, ensure_ascii=False)}\n'
            write_file(root, 'oaw.pack.toml', text, manifest['sha256'])
            snapshot = self.inspect(identifier)
            return self.save_design(identifier, card_id, validated.model_dump(mode='json'), snapshot['revision'])

    def set_version(self, identifier: str, version: str, expected_revision: str) -> dict:
        import tomllib
        from packaging.version import Version
        if str(Version(version)) != version:
            raise ValueError('Use a canonical version such as 0.2.0')
        with _LOCK:
            checked = self.check(identifier, expected_revision)
            if not checked['ok']:
                raise ValueError('Resolve project diagnostics before changing its version')
            if checked['spec']['entrypoints']:
                raise ValueError('Update the Python PluginDescriptor and project version together in Code or your external editor')
            root = self.root(identifier)
            python_project = read_file(root, 'pyproject.toml')
            manifest = read_file(root, 'oaw.pack.toml')
            declaration = tomllib.loads(manifest['content'])

            def replace_version(text: str, table: str) -> str:
                lines = text.splitlines(keepends=True)
                active = False
                for index, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith('['):
                        active = bool(re.fullmatch(r'\[\s*' + re.escape(table) + r'\s*\]\s*(?:#.*)?', stripped))
                    if active and re.match(r'\s*version\s*=', line):
                        replaced, count = re.subn(r'^(\s*version\s*=\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\')',
                                                 lambda match: match[1] + json.dumps(version), line)
                        if count != 1:
                            break
                        lines[index] = replaced
                        return ''.join(lines)
                raise ValueError(f'Edit the [{table}] version in Code; this project uses a nonstandard TOML declaration')

            python_text = replace_version(python_project['content'], 'project')
            manifest_text = (replace_version(manifest['content'], 'distribution')
                             if 'version' in declaration.get('distribution', {}) else manifest['content'])
            tomllib.loads(python_text)
            tomllib.loads(manifest_text)
            saved = write_file(root, 'pyproject.toml', python_text, python_project['sha256'])
            if manifest_text != manifest['content']:
                try:
                    write_file(root, 'oaw.pack.toml', manifest_text, manifest['sha256'])
                except BaseException:
                    # Restore only our own write. A concurrent external edit is
                    # never overwritten by rollback.
                    write_file(root, 'pyproject.toml', python_project['content'], saved['sha256'])
                    raise
        return self.inspect(identifier)

    def save_design(self, identifier: str, card_id: str, design: dict, expected_revision: str) -> dict:
        """Update only the selected JSON resource and, once, its TOML binding."""
        from backend.packs.interfaces import CardInterface
        import tomllib
        from packaging.version import Version
        validated = CardInterface.model_validate(design)
        with _LOCK:
            checked = self.check(identifier, expected_revision)
            if not checked['ok']:
                raise ValueError('Resolve project diagnostics before editing its design')
            if card_id not in {card['id'] for card in checked['cards']}:
                raise ValueError('Choose a Card declared by this project')
            root = self.root(identifier)
            manifest = read_file(root, 'oaw.pack.toml')
            declaration = tomllib.loads(manifest['content'])
            if Version(declaration.get('compatibility', {}).get('plugin_api', '0')) < Version('1.39'):
                raise ValueError('Visual bindings require Plugin API 1.39. Update compatibility.plugin_api and your Python PluginDescriptor together, then Check again.')
            path = declaration.get('designs', {}).get(card_id)
            expected = None
            if path:
                expected = read_file(root, path)['sha256']
            else:
                path = f'designs/card-{_digest(card_id.encode())[:16]}.json'
                if project_file(root, path, exists=False).exists():
                    raise ValueError(f'{path} already exists without a design binding; bind it in oaw.pack.toml first')
            content = validated.model_dump_json(indent=2) + '\n'
            if not declaration.get('designs', {}).get(card_id):
                binding = f'{json.dumps(card_id)} = {json.dumps(path)}\n'
                text = manifest['content']
                # Preserve every existing line/comment. An unusual inline table
                # remains a code-level edit instead of being reserialized.
                header = re.search(r'(?m)^\s*\[\s*designs\s*\]\s*(?:#.*)?$', text)
                if header:
                    position = text.find('\n', header.end())
                    position = len(text) if position < 0 else position + 1
                    text = text[:position] + ('\n' if position == len(text) and not text.endswith('\n') else '') + binding + text[position:]
                elif 'designs' not in declaration:
                    text = text.rstrip() + '\n\n[designs]\n' + binding
                else:
                    raise ValueError('Use a [designs] table in oaw.pack.toml to attach designs visually')
                # Validate TOML before either file changes; code and unrelated
                # declaration sections are never reconstructed by the editor.
                tomllib.loads(text)
                write_file(root, path, content, None)
                write_file(root, 'oaw.pack.toml', text, manifest['sha256'])
            else:
                write_file(root, path, content, expected)
        return self.inspect(identifier)

    def check(self, identifier: str, expected_revision: str | None = None) -> dict:
        result = self.inspect(identifier)
        if expected_revision is not None and result['revision'] != expected_revision:
            raise ConflictError('Project files changed outside this editor. Reload before checking or building.')
        from backend.packs.build import doctor
        report = None
        if result['spec'] is not None:
            root = self.root(identifier)
            report = doctor(root)
            spec = read_source_pack(root)
            if not spec.entrypoints:
                try:
                    from backend.plugins.declarative import declarative_plugin
                    from backend.plugins.registry import PluginRegistry
                    PluginRegistry().install(declarative_plugin(spec), source_spec=spec)
                except (ValueError, RuntimeError) as exc:
                    result['diagnostics'].append({'severity': 'error', 'message': str(exc)})
        return {**result, 'ok': not any(item['severity'] == 'error' for item in result['diagnostics']),
                'level': 'static', 'doctor': report}

    def build(self, identifier: str, expected_revision: str, *, backend_wheel: str | None = None,
              frontend_dir: str | None = None, dependency_wheels: str | None = None) -> tuple[bytes, str]:
        root = self.root(identifier)
        checked = self.check(identifier, expected_revision)
        if not checked['ok']:
            raise ValueError('Resolve the project diagnostics before building')
        spec = read_source_pack(root)
        if backend_wheel is None and spec.entrypoints:
            wheels = sorted((root / 'dist').glob('*.whl'))
            if len(wheels) != 1:
                raise ValueError('Build Python in your external editor or authorized Sandbox, then select its wheel (dist/*.whl)')
            backend_wheel = wheels[0].relative_to(root).as_posix()
        if frontend_dir is None and spec.frontend:
            frontend_dir = 'dist/frontend'
        inputs = {key: project_file(root, value) if value else None for key, value in {
            'backend_wheel': backend_wheel, 'frontend_dir': frontend_dir, 'dependency_wheels': dependency_wheels}.items()}
        if inputs['backend_wheel']:
            self._verify_wheel_sources(root, inputs['backend_wheel'])
        from backend.packs.build import build_project
        from backend.packs.archive import inspect_archive
        # The temporary artifact is outside the source, keeping project revisions stable.
        with TemporaryDirectory(prefix='oaw-project-build-') as temporary:
            output = build_project(root, Path(temporary) / 'pack.oawpack', **inputs)
            data = output.read_bytes()
        if self.inspect(identifier)['revision'] != expected_revision:
            raise ConflictError('Project changed during build. Reload and build the current files.')
        manifest = inspect_archive(data).manifest
        return data, f'{manifest.id}-{manifest.version}.oawpack'

    @staticmethod
    def _verify_wheel_sources(root: Path, wheel: Path) -> None:
        """The IDE never reports a stale compiled wheel as the edited source."""
        import tomllib
        from zipfile import ZipFile
        declaration = tomllib.loads(read_file(root, 'pyproject.toml')['content'])
        packages = declaration.get('tool', {}).get('hatch', {}).get('build', {}).get('targets', {}).get('wheel', {}).get('packages', [])
        with ZipFile(wheel) as archive:
            packaged = {name: archive.read(name) for name in archive.namelist() if name.endswith('.py')}
        source = {}
        for package in packages:
            directory = project_file(root, package)
            for path in directory.rglob('*.py'):
                path = project_file(root, path.relative_to(root).as_posix())
                source[directory.name + '/' + path.relative_to(directory).as_posix()] = path.read_bytes()
        if source != packaged:
            raise ValueError('The Python wheel does not match the current source files. Rebuild it in your external editor or authorized Sandbox before exporting.')
