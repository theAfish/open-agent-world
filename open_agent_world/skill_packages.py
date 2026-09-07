"""Reusable skill toolbox contract for trusted Open Agent World plugins.

Skills are portable instructions and reusable runtime assets. Reading one grants
no additional capabilities; execution composes live Skill and Sandbox access.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import base64
import json
from uuid import uuid4
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, NodeDocumentAction, NodeDocumentDefinition,
    NodeDocumentDownload, NodeTypeDefinition, PluginDescriptor, RelationshipDefinition,
    ResourceValidationError,
    NodeContainerDefinition,
)


class SkillAsset(BaseModel):
    """Binary assets retain their bytes; text files keep the original string form."""
    model_config = ConfigDict(extra="forbid")
    data_base64: str
    media_type: str = "application/octet-stream"


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    node_id: str | None = None
    name: str = Field(default="New skill", min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    instructions: str = ""
    files: dict[str, str | SkillAsset] = Field(default_factory=dict)
    directories: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def file_tree(self):
        # Paths are exported into a real package directory, relative to the skill.
        paths = [*self.files, *self.directories]
        for path in paths:
            if not path or any(part in {"", ".", ".."} for part in path.split("/")) or any(char in path for char in "\\:\x00"):
                raise ValueError("Use relative file paths such as scripts/build.py")
            if path == "SKILL.md":
                raise ValueError("Edit SKILL.md using the Instructions field")
        file_paths = set(self.files) | {"SKILL.md"}
        for path in paths:
            parents = ["/".join(path.split("/")[:i]) for i in range(1, len(path.split("/")))]
            if any(parent in file_paths for parent in parents) or path in self.directories and path in file_paths:
                raise ValueError("A path cannot be both a file and a folder")
        for asset in self.files.values():
            if isinstance(asset, SkillAsset):
                base64.b64decode(asset.data_base64, validate=True)
        return self


class PackageSource(BaseModel):
    plugin_id: str
    version: str


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(default="my.toolbox", pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$", max_length=100)
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(default="Skill Toolbox", min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=120)
    instructions: str = ""
    skills: list[Skill] = Field(default_factory=list)
    source: PackageSource | None = None

    @model_validator(mode="after")
    def unique_skills(self):
        if len({skill.node_id or skill.id for skill in self.skills}) != len(self.skills):
            raise ValueError("Skill IDs must be unique within a toolbox")
        return self


class ToolboxConfig(BaseModel):
    status: Literal["available"] = "available"


class ReadSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str | None = Field(default=None, description="Omit to list skills; supply an ID to read its full instructions and text files.")
    file_path: str | None = Field(default=None, description="Optional path within the selected skill, such as scripts/build.py or assets/logo.png, to read that file. Binary files return base64 and media_type.")

    @model_validator(mode="after")
    def file_needs_skill(self):
        if self.file_path is not None and self.skill_id is None:
            raise ValueError("Choose a skill_id when reading a file")
        return self


class RemoveSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str


def _read(value, arguments):
    ReadSkill.model_validate(arguments)
    return value


def _upsert(value, arguments):
    skill = Skill.model_validate(arguments).model_dump(mode="json")
    def matches(old):
        return old.get("node_id") == skill["node_id"] if skill["node_id"] else old["id"] == skill["id"]
    skills = [{**skill, "node_id": old.get("node_id")} if matches(old) else old for old in value["skills"]]
    if not any(matches(old) for old in value["skills"]):
        skills.append(skill)
    return {**value, "skills": skills}


def _remove(value, arguments):
    key = RemoveSkill.model_validate(arguments).skill_id
    return {**value, "skills": [skill for skill in value["skills"] if (skill.get("node_id") or skill["id"]) != key and skill["id"] != key]}


def _configure(value, arguments):
    # Skill contents and provenance have their own lifecycle.
    fields = {key: item for key, item in arguments.items() if key not in {"skills", "source"}}
    return {**value, **fields}


def _summary(value):
    return {"total": len(value["skills"]), "names": [skill["name"] for skill in value["skills"]]}


def export_plugin(value: dict[str, Any]) -> NodeDocumentDownload:
    package = SkillPackage.model_validate(value)
    # World node identities become portable skill-local IDs in the release.
    package = package.model_copy(update={"skills": [skill.model_copy(update={"id": skill.node_id or skill.id, "node_id": None}) for skill in package.skills]})
    distribution = "oaw-toolbox-" + package.package_id.replace(".", "-")
    module = "oaw_toolbox_" + package.package_id.replace(".", "_").replace("-", "_")
    manifest = f'''[project]
name = "{distribution}"
version = "{package.version}"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.11,<3"]

[project.entry-points."open_agent_world.plugins"]
"{package.package_id}" = "{module}:create_plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{module}"]
'''
    entry = '''import base64
import json
from importlib.resources import files
from open_agent_world.skill_packages import SkillPackage, SkillPackagePlugin

def create_plugin():
    root = files(__package__)
    value = json.loads(root.joinpath("package.json").read_text(encoding="utf-8"))
    for skill in value["skills"]:
        folder = root.joinpath("skills", skill["id"])
        skill["instructions"] = folder.joinpath("SKILL.md").read_text(encoding="utf-8")
        skill["files"] = {
            path: folder.joinpath(path).read_text(encoding="utf-8") if metadata is None else {
                "data_base64": base64.b64encode(folder.joinpath(path).read_bytes()).decode("ascii"),
                "media_type": metadata["media_type"],
            }
            for path, metadata in skill["files"].items()
        }
    package = SkillPackage.model_validate(value)
    return SkillPackagePlugin(package)
'''
    readme = f'''# {package.name}

{package.description}

Author: {package.author or "Unspecified"}
Package: {package.package_id} / {package.version}
Requires Open Agent World Plugin API 1.10.

From an Open Agent World checkout, mount this extracted folder:

    ./scripts/dev.ps1 -AgentRuntime mock -PluginPath /path/to/{distribution}

Or install with `uv add --project backend --editable /path/to/{distribution}` and restart.
The toolbox appears under Tools. Create a card, then connect an Agent using
Use skills. Edit its local contents in the workspace. Existing cards retain
their saved contents when this plugin changes. Create a new card for the new version.

Edit src/{module}/package.json for settings and skills/<id>/ for instructions,
scripts, assets and other files. The manifest lists each file: null for UTF-8 text,
or an object with media_type for binary files. Keep package_id stable
for updates; choose a new package_id when publishing an independent fork.
Scripts are package files; use the Agent's run_skill_script tool with a separately
authorized Sandbox. The host materializes the bundle read-only outside the workspace.
'''
    output = io.BytesIO()
    metadata = package.model_dump(mode="json")
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for skill, item in zip(package.skills, metadata["skills"]):
            folder = f"{distribution}/src/{module}/skills/{skill.id}"
            archive.writestr(f"{folder}/SKILL.md", item.pop("instructions"))
            for directory in skill.directories:
                archive.writestr(f"{folder}/{directory}/", b"")
            for path, asset in skill.files.items():
                archive.writestr(f"{folder}/{path}", asset if isinstance(asset, str) else base64.b64decode(asset.data_base64))
                item["files"][path] = None if isinstance(asset, str) else {"media_type": asset.media_type}
        for path, content in {
            "pyproject.toml": manifest, "README.md": readme,
            f"src/{module}/__init__.py": entry,
            f"src/{module}/package.json": json.dumps(metadata, ensure_ascii=False, indent=2),
        }.items():
            archive.writestr(f"{distribution}/{path}", content)
    return NodeDocumentDownload(f"{distribution}-{package.version}.zip", output.getvalue(), "application/zip")


@dataclass(frozen=True, slots=True)
class SkillContainerDefinition(NodeContainerDefinition):
    member_traits: frozenset[str] = frozenset({"oaw.skill"})
    document_field: str | None = "skills"


def register_skill_package(registration, *, node_type: str, package: SkillPackage, published: bool = True):
    """Register one owned toolbox, relationship and scoped reading capability."""
    value = package.model_dump(mode="json")
    value["source"] = ({"plugin_id": registration.descriptor.id, "version": registration.descriptor.version}
                       if published else None)
    kind = f"{node_type}.read"
    child_type = f"{node_type}.skill"
    register_skill_node(registration, node_type=child_type, user_creatable=not published)

    async def invoke(context, capability, arguments):
        request = ReadSkill.model_validate(arguments)
        snapshot = await context.node_document_action(capability, "read", arguments)
        current = snapshot["value"]
        result = {key: current[key] for key in ("name", "description", "instructions", "author", "version", "source")}
        if request.skill_id is None:
            result["skills"] = [{**{key: skill[key] for key in ("name", "description")}, "id": skill.get("node_id") or skill["id"]} for skill in current["skills"]]
        else:
            skill = next((skill for skill in current["skills"] if (skill.get("node_id") or skill["id"]) == request.skill_id), None)
            if skill is None:
                raise ResourceValidationError("Skill no longer exists; list the toolbox again")
            if request.file_path is not None:
                if request.file_path not in skill["files"]:
                    raise ResourceValidationError("File no longer exists; read the skill again")
                result["file"] = {"path": request.file_path, "content": skill["files"][request.file_path]}
            else:
                result["skill"] = {**skill, "files": {path: asset if isinstance(asset, str) else {
                    "media_type": asset["media_type"], "size_bytes": len(base64.b64decode(asset["data_base64"]))
                } for path, asset in skill["files"].items()}}
        return result

    registration.register_capability(CapabilityDefinition(kind=kind, tool_name="read_skills", target_parameter="toolbox",
        description="List a toolbox's current skills and shared conventions. Supply skill_id to read one member, or skill_id and file_path to read a bundled file. Execution requires an independently authorized Sandbox.",
        input_schema=ReadSkill.model_json_schema()), invoke)
    registration.register_node_type(NodeTypeDefinition(
        id=node_type, label=package.name, description=package.description or "A portable toolbox of skills and shared working instructions.",
        icon="boxes", color="#ac8b57", deck_id="tools", deck_label="Tools", deck_icon="boxes",
        default_name=package.name, default_size=(1100, 650), default_status="available",
        statuses=frozenset({"available"}), config_model=ToolboxConfig,
        traits=frozenset({"oaw.skill-package", "ui.skill-package.v1"}),
        surfaces={"preview": True, "inspector": True, "workspace": True}, templateable=True,
        container=SkillContainerDefinition(member_type=child_type),
        document=NodeDocumentDefinition(model=SkillPackage, initial_value=value,
            actions={"read": NodeDocumentAction(_read, capability_kind=kind, read_only=True),
                     "upsert": NodeDocumentAction(_upsert), "remove": NodeDocumentAction(_remove),
                     "configure": NodeDocumentAction(_configure)},
            summarize=_summary, capture=lambda value: {**value, "skills": []}, downloads={"plugin": export_plugin}, max_size_bytes=16 * 1024 * 1024),
    ))
    registration.register_relationship(RelationshipDefinition(
        id=f"{node_type}.use", label="Use skills", short_label="skills",
        description="Read this toolbox and load individual skills when needed.",
        source_traits=frozenset({"core.agent"}), target_types=frozenset({node_type}), templateable=True,
        capabilities=(CapabilityGrantDefinition(kind=kind),),
    ))


class SkillPackagePlugin:
    """Turn curated data into an ordinary owned plugin contribution."""
    def __init__(self, package: SkillPackage):
        self.package = package
        self.descriptor = PluginDescriptor(id=package.package_id, version=package.version,
            plugin_api_version="1.10", name=package.name, description=package.description)

    def register(self, registration):
        register_skill_package(registration, node_type=f"{self.package.package_id}.toolbox", package=self.package)


def register_skill_node(registration, *, node_type: str, user_creatable: bool = True):
    kind = f"{node_type}.read"
    async def invoke(context, capability, arguments):
        # A direct connection reads only this document, never its parent collection.
        snapshot = await context.node_document_action(capability, "read", {})
        skill = snapshot["value"]
        path = arguments.get("file_path")
        if path is not None:
            if path not in skill["files"]:
                raise ResourceValidationError("File no longer exists; read the skill again")
            return {"file": {"path": path, "content": skill["files"][path]}}
        return {"skill": {**skill, "files": {path: asset if isinstance(asset, str) else {
            "media_type": asset["media_type"], "size_bytes": len(base64.b64decode(asset["data_base64"]))
        } for path, asset in skill["files"].items()}}}
    registration.register_capability(CapabilityDefinition(kind=kind, tool_name="read_skill", target_parameter="skill",
        description="Read one independently authorized Skill. Supply file_path for a bundled file. Access does not include its parent toolbox or siblings.",
        input_schema={"type": "object", "properties": {"file_path": {"type": "string", "description": "Optional file path inside this skill."}}, "additionalProperties": False}), invoke)
    registration.register_node_type(NodeTypeDefinition(id=node_type, label="Skill", description="One independently connected skill, with its own files and settings.",
        icon="wrench", color="#ac8b57", deck_id="tools", deck_label="Tools", deck_icon="boxes", default_name="New skill",
        default_size=(360, 235), default_status="available", statuses=frozenset({"available"}), config_model=ToolboxConfig,
        traits=frozenset({"oaw.skill", "ui.skill.v1"}), user_creatable=user_creatable,
        surfaces={"preview": True, "inspector": True, "workspace": True}, templateable=True,
        document=NodeDocumentDefinition(model=Skill, initial_value={}, max_size_bytes=16 * 1024 * 1024,
            actions={"read": NodeDocumentAction(lambda value, arguments: value, capability_kind=kind, read_only=True)})))
    registration.register_relationship(RelationshipDefinition(id=f"{node_type}.use", label="Use skill", short_label="skill",
        description="Access only this skill, even when it is inside a toolbox.", source_traits=frozenset({"core.agent"}), target_types=frozenset({node_type}), templateable=True,
        capabilities=(CapabilityGrantDefinition(kind=kind),)))
