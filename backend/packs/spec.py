"""Static Pack declarations shared by source discovery and distribution tooling.

Reading a declaration never imports a plugin, installs packages, or inspects the
developer's current Python environment. Dependency groups are explicit references;
Sandbox environments do not inherit host or other Pack requirements.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
from typing import Any, Iterable, Literal, Self
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from backend.packs.interfaces import CardInterface

ENTRY_POINT_GROUP = "open_agent_world.plugins"
MAX_METADATA_BYTES = 2 * 1024 * 1024
IDENTIFIER = r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$"


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def relative_path(value: str) -> str:
    """Use one portable path spelling, including when validating on POSIX."""
    if (not value or len(value) > 240 or "\\" in value or ":" in value
            or value.startswith("/") or any(ord(char) < 32 for char in value)):
        raise ValueError(f"Unsafe Pack path: {value!r}")
    for part in value.split("/"):
        if (part in {"", ".", ".."} or part.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError(f"Unsafe Pack path: {value!r}")
    return value


def _requirements(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or len(value) > 2000 or "\n" in value or "\r" in value:
            raise ValueError("Python dependencies must be bounded PEP 508 requirements")
        parsed = Requirement(value)
        # Direct references are metadata only here; the installation provider
        # separately decides which sources it can safely prepare.
        normalized = str(parsed)
        if normalized not in result:
            result.append(normalized)
    if len(result) > 500:
        raise ValueError("At most 500 direct Python dependencies are supported")
    return tuple(result)


class BackendComponent(SpecModel):
    project: str = "pyproject.toml"
    mode: Literal["host-adapter"] = "host-adapter"
    python: tuple[str, ...] = ()
    requires_python: str = ""

    _path = field_validator("project")(relative_path)
    _python = field_validator("python")(_requirements)

    @field_validator("requires_python")
    @classmethod
    def python_version(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class CompatibilitySpec(SpecModel):
    plugin_api: str = Field(pattern=r"^\d+\.\d+$")
    oaw: str = ">=0.1.0,<1"
    frontend_api: Literal[1] = 1

    @field_validator("oaw")
    @classmethod
    def oaw_version(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class FrontendComponent(SpecModel):
    entry: str
    project: str | None = None
    mode: Literal["host-build", "pack-build"] = "host-build"

    @field_validator("entry", "project")
    @classmethod
    def paths(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None


class ExecutableArtifact(SpecModel):
    platform: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    url: str | None = None
    path: str | None = None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive: Literal["raw", "zip", "tar"] = "raw"
    executable: str
    size_bytes: int | None = Field(default=None, gt=0, le=2 * 1024 * 1024 * 1024)

    _executable = field_validator("executable")(relative_path)

    @field_validator("path")
    @classmethod
    def local_path(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def origin(self) -> Self:
        if (self.url is None) == (self.path is None):
            raise ValueError("Executable artifacts require exactly one HTTPS URL or Pack-relative path")
        if self.url is not None:
            parts = urlsplit(self.url)
            if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                    or parts.password is not None or parts.fragment or len(self.url) > 4000):
                raise ValueError("Executable artifact URL must be HTTPS without credentials or fragments")
        return self


class EnvironmentSpec(SpecModel):
    """Resolved recipe, not a claim that an environment has been installed."""
    target: Literal["host-worker", "sandbox"]
    provider: Literal["python", "node", "executable", "external"] = "python"
    python: tuple[str, ...] = ()
    node: tuple[str, ...] = ()
    requires_python: str = ""
    prepare: Literal["on-use", "install", "manual"] = "on-use"
    management: Literal["managed", "legacy-shared", "external"] = "managed"
    platforms: tuple[str, ...] = ()
    entrypoint: str | None = None
    resources: tuple[str, ...] = ()
    requirement_files: tuple[str, ...] = ()
    verify_imports: tuple[str, ...] = ()
    verify_script: str | None = None
    lock: str | None = None
    lock_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    prerequisites: tuple[str, ...] = ()
    dependency_groups: tuple[str, ...] = ()
    artifacts: tuple[ExecutableArtifact, ...] = ()
    executable_names: tuple[str, ...] = ()
    verify_argv: tuple[str, ...] = ("--version",)
    verify_pattern: str | None = None
    accept_external: bool = False

    _python = field_validator("python")(_requirements)

    @field_validator("requires_python")
    @classmethod
    def python_version(cls, value: str) -> str:
        SpecifierSet(value)
        return value

    @field_validator("resources", "requirement_files")
    @classmethod
    def paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(relative_path(value) for value in values)

    @field_validator("entrypoint", "verify_script", "lock")
    @classmethod
    def entry(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None

    @field_validator("verify_imports")
    @classmethod
    def modules(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value) for value in values):
            raise ValueError("Verification imports must be Python module names")
        return values

    @model_validator(mode="after")
    def target_management(self) -> Self:
        if self.management == "legacy-shared" and (self.target != "sandbox" or self.provider != "python"):
            raise ValueError("legacy-shared is only valid for Sandbox Python")
        if self.provider == "python" and self.node:
            raise ValueError("Use a separate Node environment for Node requirements")
        if self.provider == "node" and self.python:
            raise ValueError("Use a separate Python environment for Python requirements")
        if self.provider == "executable" and (self.python or self.node):
            raise ValueError("Executable environments cannot include Python or Node package requirements")
        if self.provider != "executable" and (self.artifacts or self.executable_names or self.accept_external or self.verify_pattern):
            raise ValueError("Executable artifact and discovery fields require provider=executable")
        if len({artifact.platform for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("Executable artifact platforms must be unique")
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,119}", name) for name in self.executable_names):
            raise ValueError("Executable discovery names must be bare program names")
        if len(self.verify_argv) > 30 or any(len(value) > 1000 or "\x00" in value for value in self.verify_argv):
            raise ValueError("Executable verification arguments exceed limits")
        if self.verify_pattern is not None:
            if len(self.verify_pattern) > 500:
                raise ValueError("Executable verification pattern exceeds limits")
            re.compile(self.verify_pattern)
        if self.verify_script and (self.target != "host-worker" or self.management != "managed" or self.provider != "python"):
            raise ValueError("Verification scripts require a managed Python host worker")
        if any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in self.artifact_hashes.values()):
            raise ValueError("Artifact hashes must be SHA-256 digests")
        for path in self.artifact_hashes:
            relative_path(path)
        return self


class FeatureSpec(SpecModel):
    environments: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    optional: bool = False
    description: str = Field(default="", max_length=1000)


class PrerequisiteSpec(SpecModel):
    kind: Literal["executable", "model-connection", "credential", "service", "resource", "license", "hardware"]
    description: str = Field(default="", max_length=1000)
    executable: str | None = None
    configuration: str | None = None
    optional: bool = False


class SpecDependency(SpecModel):
    id: str = Field(pattern=IDENTIFIER, max_length=128)
    kind: Literal["distribution", "owner", "pack"] = "distribution"
    version: str = ""
    optional: bool = False

    @field_validator("version")
    @classmethod
    def specifier(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class CardContribution(SpecModel):
    """Passive cards compile to the existing NodeTypeDefinition contract."""
    id: str | None = Field(default=None, pattern=IDENTIFIER, max_length=128)
    name: str | None = None
    description: str = ""
    icon: str = "box"
    color: str = "#518bba"
    frontend: dict[str, str] = Field(default_factory=dict)
    config_schema: dict[str, Any] | str = Field(default_factory=lambda: {"type": "object", "properties": {}})
    default_name: str | None = None
    size: tuple[float, float] = (300, 200)
    statuses: tuple[str, ...] = ("ready",)
    default_status: str | None = None
    deck_id: str | None = None
    deck_name: str | None = None

    @model_validator(mode="after")
    def valid_card(self) -> Self:
        import math
        if not self.statuses or len(set(self.statuses)) != len(self.statuses) or any(not value for value in self.statuses):
            raise ValueError('Card statuses must be nonempty and unique')
        if self.default_status is not None and self.default_status not in self.statuses:
            raise ValueError('Card default_status must belong to statuses')
        if any(not math.isfinite(value) or value <= 0 for value in self.size):
            raise ValueError('Card size must contain positive finite dimensions')
        if set(self.frontend) - {'preview', 'body', 'settings', 'workspace'} or any(not value for value in self.frontend.values()):
            raise ValueError('Unknown or empty card frontend surface')
        return self


class Contributions(SpecModel):
    cards: tuple[CardContribution, ...] = Field(default=(), max_length=1000)


class PackSpec(SpecModel):
    schema_version: Literal[3] = 3
    distribution_id: str = Field(pattern=IDENTIFIER, max_length=128)
    version: str
    name: str
    owner: str | None = Field(default=None, pattern=IDENTIFIER, max_length=128)
    packs: tuple[str, ...] = ()
    node_types: tuple[str, ...] | None = Field(default=None, max_length=1000)
    entrypoints: dict[str, str] = Field(default_factory=dict)
    backend: BackendComponent
    frontend: FrontendComponent | None = None
    environments: dict[str, EnvironmentSpec] = Field(default_factory=dict)
    features: dict[str, FeatureSpec] = Field(default_factory=dict)
    prerequisites: dict[str, PrerequisiteSpec] = Field(default_factory=dict)
    dependencies: tuple[SpecDependency, ...] = ()
    dependency_groups: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    requirement_origins: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    source_files: dict[str, str] = Field(default_factory=dict)
    runtime_resources: tuple[str, ...] = ()
    source_root: Path | None = Field(default=None, exclude=True)
    legacy: bool = False
    compatibility: CompatibilitySpec | None = None
    contributions: Contributions = Field(default_factory=Contributions)
    designs: dict[str, CardInterface] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if not self.designs:
            # Preserve historical source receipts and generated adapter bytes.
            data.pop("designs", None)
        return data

    @field_validator("version")
    @classmethod
    def canonical_version(cls, value: str) -> str:
        if str(Version(value)) != value:
            raise ValueError("Pack version must use canonical PEP 440 spelling")
        return value

    @model_validator(mode="after")
    def references(self) -> Self:
        if self.designs and (self.compatibility is None or Version(self.compatibility.plugin_api) < Version('1.39')):
            raise ValueError("Card interface resources require Plugin API 1.39 or newer")
        if any(not re.fullmatch(IDENTIFIER, key) for key in self.designs):
            raise ValueError("Designs must reference valid card identifiers")
        if self.node_types is not None and self.designs.keys() - set(self.node_types):
            raise ValueError("Design references a card outside the declared node_types")
        if self.node_types is not None and (len(set(self.node_types)) != len(self.node_types) or any(
                not re.fullmatch(IDENTIFIER, value) or value == "core" or value.startswith("core.")
                for value in self.node_types)):
            raise ValueError("Owned node types must be unique non-core identifiers")
        if len(set(self.packs)) != len(self.packs) or any(not re.fullmatch(IDENTIFIER, value) for value in self.packs):
            raise ValueError("Pack IDs must be unique valid identifiers")
        for collection in (self.environments, self.features, self.prerequisites, self.dependency_groups):
            if any(not re.fullmatch(IDENTIFIER, value) for value in collection):
                raise ValueError("Declaration names must be valid identifiers")
        for name, feature in self.features.items():
            if set(feature.environments) - self.environments.keys():
                raise ValueError(f"Feature {name} references unknown environments")
            if set(feature.prerequisites) - self.prerequisites.keys():
                raise ValueError(f"Feature {name} references unknown prerequisites")
        for name, environment in self.environments.items():
            if set(environment.prerequisites) - self.prerequisites.keys():
                raise ValueError(f"Environment {name} references unknown prerequisites")
        identities = [(item.kind, item.id) for item in self.dependencies]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate Pack dependency identity")
        for item in self.dependencies:
            if ((item.kind == "distribution" and item.id == self.distribution_id)
                    or (item.kind == "owner" and item.id == self.owner)
                    or (item.kind == "pack" and item.id in self.packs)):
                raise ValueError("Pack dependency cannot reference itself")
        return self

    @property
    def spec_digest(self) -> str:
        payload = self.model_dump(mode="json")
        if not self.contributions.cards:
            # New optional syntax must not invalidate existing adapter receipts.
            payload.pop("contributions")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def legacy_python_requirements(self) -> tuple[str, ...]:
        return _requirements(requirement for environment in self.environments.values()
                             if environment.management == "legacy-shared" for requirement in environment.python)


class _Source(SpecModel):
    path: str
    kind: Literal["project", "optional", "group", "requirements"] = "project"
    group: str | None = None

    _path = field_validator("path")(relative_path)

    @model_validator(mode="after")
    def group_kind(self) -> Self:
        if (self.kind in {"optional", "group"}) != (self.group is not None):
            raise ValueError("optional/group sources require a group; project/requirements do not")
        return self


class _Group(SpecModel):
    include: tuple[str, ...] = ()
    sources: tuple[_Source, ...] = ()
    python: tuple[str, ...] = ()

    _python = field_validator("python")(_requirements)


class _Environment(_Group):
    target: Literal["host-worker", "sandbox"]
    provider: Literal["python", "node", "executable", "external"] = "python"
    node: tuple[str, ...] = ()
    requires_python: str = ""
    prepare: Literal["on-use", "install", "manual"] = "on-use"
    management: Literal["managed", "legacy-shared", "external"] = "managed"
    platforms: tuple[str, ...] = ()
    entrypoint: str | None = None
    resources: tuple[str, ...] = ()
    resource_directories: tuple[str, ...] = ()
    verify_imports: tuple[str, ...] = ()
    verify_script: str | None = None
    lock: str | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    prerequisites: tuple[str, ...] = ()
    artifacts: tuple[ExecutableArtifact, ...] = ()
    executable_names: tuple[str, ...] = ()
    verify_argv: tuple[str, ...] = ("--version",)
    verify_pattern: str | None = None
    accept_external: bool = False


class _Distribution(SpecModel):
    id: str
    version: str | None = None
    name: str | None = None


class _Plugin(SpecModel):
    owner: str | None = None
    packs: tuple[str, ...] | None = None
    node_types: tuple[str, ...] | None = None


class _Components(SpecModel):
    backend: BackendComponent = Field(default_factory=BackendComponent)
    frontend: FrontendComponent | None = None


class _Declaration(SpecModel):
    schema_version: Literal[3]
    distribution: _Distribution
    plugin: _Plugin = Field(default_factory=_Plugin)
    contributions: Contributions = Field(default_factory=Contributions)
    designs: dict[str, str] = Field(default_factory=dict)
    components: _Components = Field(default_factory=_Components)
    dependency_groups: dict[str, _Group] = Field(default_factory=dict, alias="dependency-groups")
    environments: dict[str, _Environment] = Field(default_factory=dict)
    features: dict[str, FeatureSpec] = Field(default_factory=dict)
    prerequisites: dict[str, PrerequisiteSpec] = Field(default_factory=dict)
    dependencies: tuple[SpecDependency, ...] = ()
    compatibility: CompatibilitySpec | None = None
    runtime_resources: tuple[str, ...] = Field(default=(), alias="runtime-resources")
    runtime_resource_directories: tuple[str, ...] = Field(default=(), alias="runtime-resource-directories")


class _Reader:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.files: dict[str, str] = {}
        self.origins: dict[str, list[str]] = {}
        self.requirement_files: set[str] = set()

    def read(self, relative: str, *, max_bytes: int = MAX_METADATA_BYTES) -> bytes:
        path = self.root / relative_path(relative)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ValueError(f"Pack source escapes its root or is not a file: {relative}")
        if resolved.stat().st_size > max_bytes:
            raise ValueError(f"Pack declaration file exceeds {max_bytes} bytes: {relative}")
        content = resolved.read_bytes()
        self.files[relative] = hashlib.sha256(content).hexdigest()
        return content

    def toml(self, relative: str) -> dict[str, Any]:
        return tomllib.loads(self.read(relative).decode("utf-8-sig"))

    def directory(self, relative: str) -> tuple[str, ...]:
        root = self.root / relative_path(relative)
        if not root.resolve(strict=True).is_relative_to(self.root) or not root.is_dir() or root.is_symlink():
            raise ValueError(f"Unsafe Pack resource directory: {relative}")
        result = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError(f"Unsafe Pack resource: {path.relative_to(self.root)}")
            if path.is_file() and not any(part in {"__pycache__", ".git"} for part in path.parts) and path.suffix not in {".pyc", ".pyo"}:
                name = path.relative_to(self.root).as_posix()
                self.read(name, max_bytes=128 * 1024 * 1024)
                result.append(name)
            if len(result) > 4096:
                raise ValueError("Pack resource directory exceeds 4096 files")
        return tuple(result)

    def record(self, values: Iterable[str], origin: str) -> tuple[str, ...]:
        requirements = _requirements(values)
        for requirement in requirements:
            origins = self.origins.setdefault(requirement, [])
            if origin not in origins:
                origins.append(origin)
        return requirements

    def requirements(self, path: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
        if path in stack:
            raise ValueError(f"Cyclic requirements includes: {' -> '.join((*stack, path))}")
        self.requirement_files.add(path)
        output: list[str] = []
        for line in self.read(path).decode("utf-8-sig").splitlines():
            value = re.split(r"\s+#", line, maxsplit=1)[0].strip()
            if not value or value.startswith("#"):
                continue
            include = re.fullmatch(r"(?:-r\s*|--requirement(?:\s+|=))(.+)", value)
            if include:
                child = str(PurePosixPath(path).parent / relative_path(include.group(1).strip()))
                output.extend(self.requirements(child, (*stack, path)))
            elif value.startswith("-") or value.endswith("\\"):
                raise ValueError(f"Unsupported installer option/continuation in {path}: {value}")
            else:
                output.extend(self.record([value], path))
        return _requirements(output)

    def source(self, source: _Source) -> tuple[str, ...]:
        if source.kind == "requirements":
            return self.requirements(source.path)
        data = self.toml(source.path)
        project = data.get("project", {})
        if source.kind == "project":
            if "dependencies" in project.get("dynamic", []):
                raise ValueError(f"Dynamic dependencies cannot be inspected statically: {source.path}")
            values = project.get("dependencies", [])
        elif source.kind == "optional":
            try:
                values = project["optional-dependencies"][source.group]
            except KeyError as exc:
                raise ValueError(f"Unknown optional dependency group {source.group} in {source.path}") from exc
        else:
            return self.pep_group(data.get("dependency-groups", {}), source.path, source.group or "")
        if not isinstance(values, list):
            raise ValueError(f"Dependencies must be arrays: {source.path}")
        return self.record(values, f"{source.path}:{source.kind}" + (f":{source.group}" if source.group else ""))

    def pep_group(self, groups: dict, path: str, name: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
        aliases: dict[str, str] = {}
        for key in groups:
            canonical = canonicalize_name(key)
            if canonical in aliases:
                raise ValueError(f"Ambiguous dependency group spelling in {path}: {key}")
            aliases[canonical] = key
        normalized = canonicalize_name(name)
        if normalized in stack:
            raise ValueError(f"Cyclic dependency groups in {path}: {' -> '.join((*stack, normalized))}")
        if normalized not in aliases:
            raise ValueError(f"Unknown dependency group {name} in {path}")
        values = groups[aliases[normalized]]
        if not isinstance(values, list):
            raise ValueError(f"Dependency group {name} in {path} must be an array")
        result: list[str] = []
        for value in values:
            if isinstance(value, str):
                result.extend(self.record([value], f"{path}:group:{name}"))
            elif isinstance(value, dict) and set(value) == {"include-group"} and isinstance(value["include-group"], str):
                result.extend(self.pep_group(groups, path, value["include-group"], (*stack, normalized)))
            else:
                raise ValueError(f"Unsupported dependency group entry in {path}: {name}")
        return _requirements(result)


def read_source_pack(root: Path) -> PackSpec:
    """Resolve one source Pack without executing Python, npm, or build hooks.

    Old pyproject-only plugins retain unknown runtime identity until registration;
    identity is never guessed from the Python distribution or entry-point name.
    """
    reader = _Reader(Path(root))
    project_file = reader.toml("pyproject.toml")
    project = project_file.get("project", {})
    entrypoints = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    if not isinstance(entrypoints, dict) or any(not isinstance(value, str) for value in entrypoints.values()):
        raise ValueError(f"{ENTRY_POINT_GROUP} must contain string entry points")
    explicit = (reader.root / "oaw.pack.toml").exists()
    declaration = _Declaration.model_validate(reader.toml("oaw.pack.toml")) if explicit else None
    if not entrypoints and not (declaration and declaration.contributions.cards):
        raise ValueError(f"No {ENTRY_POINT_GROUP} entry points or declarative cards in {root}")
    version = project.get("version", "0" if not explicit else None)
    if not isinstance(version, str):
        raise ValueError("Source Packs require a static project.version")
    backend_python = reader.source(_Source(path="pyproject.toml"))
    backend = BackendComponent(python=backend_python, requires_python=project.get("requires-python", ""))
    legacy_python = project_file.get("tool", {}).get("open-agent-world", {}).get("runtime", {}).get("python")
    environments: dict[str, EnvironmentSpec] = {}
    if legacy_python is not None:
        if not isinstance(legacy_python, list):
            raise ValueError("Legacy runtime.python must be an array")
        environments["legacy-sandbox"] = EnvironmentSpec(
            target="sandbox", management="legacy-shared", python=reader.record(legacy_python, "pyproject.toml:runtime.python"))
    if not explicit:
        # Legacy folders only promised entry points. A placeholder distribution
        # is deliberately distinct from runtime identity, resolved after loading.
        project_name = project.get("name") or "legacy." + hashlib.sha256(
            json.dumps(entrypoints, sort_keys=True).encode()).hexdigest()[:20]
        return PackSpec(distribution_id=project_name, version=version, name=project_name,
                        backend=backend, environments=environments, entrypoints=entrypoints, source_files=reader.files,
                        source_root=reader.root, legacy=True,
                        requirement_origins={key: tuple(value) for key, value in reader.origins.items()})

    owner = declaration.plugin.owner or declaration.distribution.id
    packs = declaration.plugin.packs if declaration.plugin.packs is not None else (owner + ".default",)
    cards = []
    for card in declaration.contributions.cards:
        schema = json.loads(reader.read(card.config_schema)) if isinstance(card.config_schema, str) else card.config_schema
        identifier = card.id or (owner if len(declaration.contributions.cards) == 1 else None)
        if identifier is None:
            raise ValueError("Multiple declarative cards require explicit IDs")
        cards.append(card.model_copy(update={"id": identifier, "config_schema": schema}))
    contributions = Contributions(cards=tuple(cards))
    node_types = declaration.plugin.node_types
    if cards:
        if len(packs) != 1 or (declaration.dependencies and not entrypoints):
            raise ValueError("Declarative cards use one Pack; plugin dependencies require an advanced entrypoint")
        inferred = tuple(card.id for card in cards)
        if node_types is not None and (not set(inferred) <= set(node_types) or (not entrypoints and set(node_types) != set(inferred))):
            raise ValueError("Declared node_types disagree with contributions.cards")
        node_types = node_types if entrypoints else inferred
        from backend.plugins.declarative import config_model
        for card in cards:
            config_model(card.config_schema)().model_dump(mode="json")
    designs = {identifier: CardInterface.model_validate_json(reader.read(path).decode('utf-8-sig'))
               for identifier, path in declaration.designs.items()}
    compatibility = declaration.compatibility
    if cards or designs:
        required_api = '1.39' if designs else '1.38' if entrypoints else '1.37'
        compatibility = compatibility or CompatibilitySpec(plugin_api=required_api)
        if Version(compatibility.plugin_api) < Version(required_api):
            raise ValueError(f"These declarative contributions require Plugin API {required_api} or newer")
    if declaration.distribution.version is not None and declaration.distribution.version != version:
        raise ValueError("oaw.pack.toml and pyproject.toml versions disagree")
    if declaration.components.backend.project != "pyproject.toml":
        raise ValueError("Host adapter project must be the entry-point pyproject.toml")
    if declaration.components.backend.python or declaration.components.backend.requires_python:
        raise ValueError("Host Python dependencies must be declared only in pyproject.toml")
    frontend = declaration.components.frontend
    if frontend:
        reader.read(frontend.entry)
        if frontend.project:
            data = json.loads(reader.read(frontend.project))
            if not isinstance(data, dict):
                raise ValueError("Frontend project must contain a package.json object")
            for lock in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
                path = str(PurePosixPath(frontend.project).parent / lock)
                if (reader.root / path).exists():
                    reader.read(path)
    groups: dict[str, tuple[str, ...]] = {}

    def resolve_group(name: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
        if name in stack:
            raise ValueError(f"Cyclic Pack dependency groups: {' -> '.join((*stack, name))}")
        if name not in declaration.dependency_groups:
            raise ValueError(f"Unknown Pack dependency group: {name}")
        if name not in groups:
            groups[name] = resolve(declaration.dependency_groups[name], f"group:{name}", (*stack, name))
        return groups[name]

    def resolve(group: _Group, origin: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
        values = list(reader.record(group.python, f"oaw.pack.toml:{origin}"))
        for include in group.include:
            values.extend(resolve_group(include, stack))
        for source in group.sources:
            values.extend(reader.source(source))
        return _requirements(values)

    for name in declaration.dependency_groups:
        resolve_group(name)
    for name, environment in declaration.environments.items():
        # Keep each environment's requirement files even when a shared group was
        # already cached. File provenance is recovered from the resolved origins.
        python = resolve(environment, f"environment:{name}")
        requirement_files = sorted({origin for requirement in python for origin in reader.origins.get(requirement, [])
                                    if origin in reader.requirement_files})
        data = environment.model_dump(exclude={"include", "sources", "python", "resource_directories", "resources"})
        resources = tuple(dict.fromkeys((*environment.resources, *environment.artifact_hashes,
            *(path for directory in environment.resource_directories for path in reader.directory(directory)))))
        lock_digest = hashlib.sha256(reader.read(environment.lock)).hexdigest() if environment.lock else None
        resolved = EnvironmentSpec(**data, python=python, dependency_groups=environment.include, lock_digest=lock_digest, resources=resources,
                                   requirement_files=tuple(requirement_files))
        if name in environments and environments[name] != resolved:
            raise ValueError(f"Conflicting legacy and explicit environment declarations: {name}")
        for path in (*resolved.resources, *(value for value in (resolved.entrypoint, resolved.verify_script) if value)):
            reader.read(path)
        for path, expected in resolved.artifact_hashes.items():
            if hashlib.sha256(reader.read(path)).hexdigest() != expected:
                raise ValueError(f"Artifact hash mismatch: {path}")
        for artifact in resolved.artifacts:
            if artifact.path is not None:
                content = reader.read(artifact.path, max_bytes=128 * 1024 * 1024)
                if (hashlib.sha256(content).hexdigest() != artifact.sha256
                        or (artifact.size_bytes is not None and len(content) != artifact.size_bytes)):
                    raise ValueError(f"Executable artifact hash or size mismatch: {artifact.path}")
        environments[name] = resolved
    # Two independent legacy-shared declarations may only coexist if they agree;
    # silently replacing a descriptor/pyproject requirement hides missing packages.
    if legacy_python is not None:
        explicit = [value for name, value in environments.items()
                    if name != "legacy-sandbox" and value.management == "legacy-shared"]
        if explicit and any(set(value.python) != set(environments["legacy-sandbox"].python) for value in explicit):
            raise ValueError("Conflicting legacy runtime.python and explicit legacy-shared requirements")
    runtime_resources = tuple(dict.fromkeys((*declaration.runtime_resources,
        *(path for directory in declaration.runtime_resource_directories for path in reader.directory(directory)))))
    for path in runtime_resources:
        reader.read(path, max_bytes=128 * 1024 * 1024)
    return PackSpec(distribution_id=declaration.distribution.id, version=version,
                    name=declaration.distribution.name or project["name"], owner=owner,
                    packs=packs, node_types=node_types, contributions=contributions, designs=designs,
                    entrypoints=entrypoints, backend=backend, frontend=frontend,
                    environments=environments, features=declaration.features, prerequisites=declaration.prerequisites,
                    dependencies=declaration.dependencies, dependency_groups=groups, source_files=reader.files,
                    compatibility=compatibility,
                    runtime_resources=runtime_resources,
                    requirement_origins={key: tuple(value) for key, value in reader.origins.items()}, source_root=reader.root)


def discover_source_packs(directories: Iterable[Path]) -> tuple[PackSpec, ...]:
    """Discover immediate plugin roots, rejecting ambiguous static identities."""
    result: list[PackSpec] = []
    roots: set[Path] = set()
    identities: dict[tuple[str, str], Path] = {}
    for directory in directories:
        if not Path(directory).is_dir():
            continue
        for root in sorted(Path(directory).iterdir()):
            if not root.is_dir() or not (root / "pyproject.toml").is_file() or root.resolve() in roots:
                continue
            roots.add(root.resolve())
            reader = _Reader(root)
            data = reader.toml("pyproject.toml")
            if not data.get("project", {}).get("entry-points", {}).get(ENTRY_POINT_GROUP) and not (root / "oaw.pack.toml").is_file():
                continue
            spec = read_source_pack(root)
            declarations = [("distribution", spec.distribution_id)]
            if spec.owner:
                declarations.append(("owner", spec.owner))
            declarations.extend(("pack", pack) for pack in spec.packs)
            for identity in declarations:
                if identity in identities:
                    raise ValueError(f"Duplicate {identity[0]} {identity[1]} in {identities[identity]} and {root}")
                identities[identity] = root
            result.append(spec)
    return tuple(result)
