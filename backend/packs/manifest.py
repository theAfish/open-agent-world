"""The code-free, versioned .oawpack distribution contract."""
from __future__ import annotations

import re
from typing import Literal, Self

from packaging.specifiers import SpecifierSet
from packaging.requirements import Requirement
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend import __version__
from backend.plugins.registry import _supports_plugin_api
from backend.packs.metadata import CreatorMetadata, PACK_ID
from backend.sandbox.python_runtime import validate_requirements

FRONTEND_API_VERSION = 1


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def safe_path(value: str) -> str:
    # Canonical on Windows and POSIX, including NTFS streams/device names.
    if not value or len(value) > 240 or "\\" in value or value.startswith("/"):
        raise ValueError(f"Unsafe archive path: {value!r}")
    for part in value.split("/"):
        if (not re.fullmatch(r"[A-Za-z0-9_@+.!()-][A-Za-z0-9_@+.!() -]*", part)
                or part in {".", ".."} or part.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError(f"Unsafe archive path: {value!r}")
    return value


class Compatibility(Model):
    oaw: str = Field(min_length=1, max_length=100)
    plugin_api: str = Field(pattern=r"^\d+\.\d+$")
    frontend_api: Literal[1]

    @field_validator("oaw")
    @classmethod
    def specifier(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class PackDependency(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$", max_length=128)
    version: str = Field(default="", max_length=100)
    distribution: str | None = Field(default=None, pattern=PACK_ID, max_length=120)

    @field_validator("version")
    @classmethod
    def specifier(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class Dependencies(Model):
    packs: tuple[PackDependency, ...] = Field(default=(), max_length=100)


class AdapterWheel(Model):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$", max_length=160)
    version: str
    requires: tuple[str, ...] = ()
    requires_python: str = ""
    tags: tuple[str, ...]
    roots: tuple[str, ...]
    namespace_roots: tuple[str, ...] = ()
    file_hashes: dict[str, str] = Field(default_factory=dict)
    source: str | None = None

    @field_validator("path")
    @classmethod
    def wheel_path(cls, value):
        safe_path(value)
        if not value.startswith("backend/dependencies/") or not value.endswith(".whl"):
            raise ValueError("Adapter dependency wheels belong under backend/dependencies/")
        return value

    @model_validator(mode="after")
    def metadata(self):
        Version(self.version)
        SpecifierSet(self.requires_python)
        for requirement in self.requires:
            Requirement(requirement)
        if not self.tags or not self.roots or len(self.roots) != len(set(self.roots)):
            raise ValueError("Adapter wheels require unique roots and compatibility tags")
        for root in self.roots:
            safe_path(root)
            if "/" in root or root in {"backend", "open_agent_world"} or root.startswith("oaw_"):
                raise ValueError("Adapter dependencies cannot claim host or Pack modules")
        if not set(self.namespace_roots) <= set(self.roots):
            raise ValueError("Namespace roots must belong to the dependency wheel")
        for path, digest in self.file_hashes.items():
            safe_path(path)
            if path.split('/')[0] not in self.roots or not re.fullmatch(r'[a-f0-9]{64}', digest):
                raise ValueError("Dependency file hashes must describe owned library files")
        return self


class AdapterDependencies(Model):
    requirements: tuple[str, ...] = ()
    wheels: tuple[AdapterWheel, ...] = Field(default=(), max_length=300)

    @model_validator(mode="after")
    def declarations(self):
        for requirement in self.requirements:
            Requirement(requirement)
        if len({wheel.name for wheel in self.wheels}) != len(self.wheels):
            raise ValueError("Adapter dependency distributions must be unique")
        if len({wheel.path for wheel in self.wheels}) != len(self.wheels):
            raise ValueError("Adapter dependency paths must be unique")
        return self


class SandboxRuntime(Model):
    python: tuple[str, ...] = ()

    @field_validator("python")
    @classmethod
    def requirements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_requirements(value))


class Runtime(Model):
    sandbox: SandboxRuntime = Field(default_factory=SandboxRuntime)


class Entrypoints(Model):
    backend: str
    frontend: str

    @field_validator("backend", "frontend")
    @classmethod
    def path(cls, value: str) -> str:
        return safe_path(value)

    @model_validator(mode="after")
    def types(self) -> Self:
        if not self.backend.startswith("backend/") or not self.backend.endswith(".whl"):
            raise ValueError("backend entry must be a wheel under backend/")
        if not self.frontend.startswith("frontend/") or not self.frontend.endswith(".js"):
            raise ValueError("frontend entry must be an ES module under frontend/")
        return self


class Content(Model):
    legions: tuple[str, ...] = Field(default=(), max_length=50)
    cards: tuple[str, ...] = Field(default=(), max_length=50)

    @property
    def paths(self) -> tuple[str, ...]:
        return self.legions + self.cards

    @model_validator(mode="after")
    def entries(self):
        if not self.paths or len(set(self.paths)) != len(self.paths):
            raise ValueError("Content requires unique card or Legion paths")
        return self

    @field_validator("legions", "cards")
    @classmethod
    def validate_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Content paths must be unique")
        for value in values:
            safe_path(value)
            if not value.startswith("content/") or not value.endswith(".json"):
                raise ValueError("Content must be JSON files under content/")
        return values


class PackManifest(Model):
    schema_version: Literal[2, 3]
    kind: Literal["plugin", "content"] = "plugin"
    id: str = Field(pattern=PACK_ID, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    compatibility: Compatibility
    dependencies: Dependencies = Field(default_factory=Dependencies)
    runtime: Runtime = Field(default_factory=Runtime)
    entrypoints: Entrypoints | None = None
    content: Content | None = None
    creator: CreatorMetadata | None = None
    # Version 3 separates the immutable distribution from persisted runtime IDs.
    owner: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$", max_length=128)
    packs: tuple[str, ...] = Field(default=(), max_length=100)
    node_types: tuple[str, ...] | None = Field(default=None, max_length=1000)
    backend_modules: tuple[str, ...] = Field(default=(), max_length=20)
    environments: dict[str, "EnvironmentSpec"] = Field(default_factory=dict, max_length=100)
    features: dict[str, "FeatureSpec"] = Field(default_factory=dict, max_length=100)
    prerequisites: dict[str, "PrerequisiteSpec"] = Field(default_factory=dict, max_length=100)
    adapter: AdapterDependencies = Field(default_factory=AdapterDependencies)

    @model_validator(mode="after")
    def pack_kind(self) -> Self:
        if self.kind == "plugin":
            if self.schema_version != 3 or self.entrypoints is None or self.content is not None or self.creator is not None:
                raise ValueError("Plugin Packs require schema 3 and code entrypoints")
        elif (self.schema_version != 2 or self.entrypoints is not None or self.content is None
                or self.creator is None or self.runtime.sandbox.python):
            raise ValueError("Content Packs require schema 2, content and creator metadata, without code entrypoints or runtime installs")
        if self.schema_version != 3 and (self.owner is not None or self.packs or self.node_types is not None or self.backend_modules
                or self.environments or self.features or self.prerequisites or self.adapter.requirements or self.adapter.wheels
                or any(dependency.distribution for dependency in self.dependencies.packs)):
            raise ValueError("Explicit ownership and environment declarations require code schema 3")
        if self.schema_version == 3:
            if self.node_types is not None and (len(set(self.node_types)) != len(self.node_types) or any(
                    not re.fullmatch(r"[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*", value)
                    or value == "core" or value.startswith("core.") for value in self.node_types)):
                raise ValueError("Owned node types must be unique non-core identifiers")
            if self.owner_id == "core" or self.owner_id.startswith("core."):
                raise ValueError("External distributions cannot claim core ownership")
            if not self.packs or len(set(self.packs)) != len(self.packs):
                raise ValueError("Schema 3 requires unique provided Pack IDs")
            if any(key != self.owner_id and not key.startswith(self.owner_id + ".") for key in self.packs):
                raise ValueError("Provided Pack IDs must belong to the runtime owner")
            if not self.backend_modules or len(set(self.backend_modules)) != len(self.backend_modules) or any(
                    not re.fullmatch(r"oaw_[a-z][a-z0-9_]*", name) for name in self.backend_modules):
                raise ValueError("Backend modules must declare unique private oaw_ package names")
            for key in (*self.environments, *self.features, *self.prerequisites):
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", key):
                    raise ValueError("Invalid environment, feature or prerequisite ID")
            for feature in self.features.values():
                if not set(feature.environments) <= self.environments.keys() or not set(feature.prerequisites) <= self.prerequisites.keys():
                    raise ValueError("Feature references an undeclared environment or prerequisite")
            for environment in self.environments.values():
                if not set(environment.prerequisites) <= self.prerequisites.keys():
                    raise ValueError("Environment references an undeclared prerequisite")
            legacy = [environment for environment in self.environments.values() if environment.management == 'legacy-shared']
            if legacy and {str(Requirement(item)) for environment in legacy for item in environment.python} != {
                    str(Requirement(item)) for item in self.runtime.sandbox.python}:
                raise ValueError("Legacy environment declarations must equal runtime.sandbox.python")
        return self

    @field_validator("version")
    @classmethod
    def canonical_version(cls, value: str) -> str:
        safe_path(value)
        if str(Version(value)) != value:
            raise ValueError("version must be a canonical PEP 440 version")
        return value

    @model_validator(mode="after")
    def unique_dependencies(self) -> Self:
        ids = [d.id for d in self.dependencies.packs]
        if {self.id, *self.provided_pack_ids} & set(ids) or len(ids) != len(set(ids)) or any(
                dependency.distribution == self.id for dependency in self.dependencies.packs):
            raise ValueError("Pack dependencies must be unique and cannot reference self")
        return self

    @property
    def module_name(self) -> str:
        return self.backend_modules[0]

    @property
    def module_names(self) -> tuple[str, ...]:
        return self.backend_modules

    @property
    def owner_id(self) -> str:
        return self.owner or self.id

    @property
    def provided_pack_ids(self) -> tuple[str, ...]:
        return self.packs or (self.id,)

    def check_compatibility(self) -> None:
        if Version(__version__) not in SpecifierSet(self.compatibility.oaw):
            raise ValueError(f"Pack {self.id} requires OAW {self.compatibility.oaw}; host is {__version__}")
        if not _supports_plugin_api(self.compatibility.plugin_api):
            raise ValueError(f"Unsupported Plugin API: {self.compatibility.plugin_api}")


# Source and archive specifications use the same normalized runtime contract.
from backend.packs.spec import EnvironmentSpec, FeatureSpec, PrerequisiteSpec
PackManifest.model_rebuild()
