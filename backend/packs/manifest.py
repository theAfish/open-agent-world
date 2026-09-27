"""The code-free, versioned .oawpack distribution contract."""
from __future__ import annotations

import re
from typing import Literal, Self

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend import __version__
from backend.plugins.registry import _supports_plugin_api
from backend.sandbox.python_runtime import validate_requirements

PACK_ID = r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$"
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

    @field_validator("version")
    @classmethod
    def specifier(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class Dependencies(Model):
    packs: tuple[PackDependency, ...] = Field(default=(), max_length=100)


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
    legions: tuple[str, ...] = Field(min_length=1, max_length=50)

    @field_validator("legions")
    @classmethod
    def paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Content paths must be unique")
        for value in values:
            safe_path(value)
            if not value.startswith("content/") or not value.endswith(".json"):
                raise ValueError("Legions must be JSON files under content/")
        return values


class CreatorMetadata(Model):
    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=120)
    preparation: str = Field(default="", max_length=2000)
    example: str = Field(default="", max_length=2000)
    expected_result: str = Field(default="", max_length=2000)
    accent_color: str = Field(default="#617b72", pattern=r"^#[0-9a-fA-F]{6}$")


class PackManifest(Model):
    schema_version: Literal[1, 2]
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

    @model_validator(mode="after")
    def pack_kind(self) -> Self:
        if self.kind == "plugin":
            if self.schema_version != 1 or self.entrypoints is None or self.content is not None or self.creator is not None:
                raise ValueError("Plugin Packs require schema 1 and code entrypoints")
        elif (self.schema_version != 2 or self.entrypoints is not None or self.content is None
                or self.creator is None or self.runtime.sandbox.python):
            raise ValueError("Content Packs require schema 2, content and creator metadata, without code entrypoints or runtime installs")
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
        if self.id in ids or len(ids) != len(set(ids)):
            raise ValueError("Pack dependencies must be unique and cannot reference self")
        return self

    @property
    def module_name(self) -> str:
        return "oaw_pack_" + self.id.replace(".", "_")

    def check_compatibility(self) -> None:
        if Version(__version__) not in SpecifierSet(self.compatibility.oaw):
            raise ValueError(f"Pack {self.id} requires OAW {self.compatibility.oaw}; host is {__version__}")
        if not _supports_plugin_api(self.compatibility.plugin_api):
            raise ValueError(f"Unsupported Plugin API: {self.compatibility.plugin_api}")
