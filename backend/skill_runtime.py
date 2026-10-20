"""Compose live Skill document access with ordinary Sandbox execution."""
from __future__ import annotations

import base64
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictStr, model_validator

from backend.errors import PermissionDeniedError
from backend.node_documents import read_document
from backend.plugins.registry import CapabilitySelector
from backend.capabilities.projection import authorized_resources
from backend.sandbox.materialization import RuntimeBundle, RuntimeMount, bundle_path
from open_agent_world.skill_packages import Skill, SkillAsset


SKILL_SELECTOR = CapabilitySelector(parameter="skill", argument="skill_id", target_traits=frozenset({"oaw.skill"}),
    document_action="read", include_members=True)


class RunSkillScript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: StrictStr | None = None
    target_id: StrictStr | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=36000, strict=True, allow_inf_nan=False)
    wait_seconds: float | None = Field(default=None, ge=0, le=60, strict=True, allow_inf_nan=False,
        description="Seconds to await the result before returning an operation_id. Wait later with wait_sandbox_operation; do not resubmit running work.")
    skill_id: StrictStr = Field(description="World node ID returned by reading an authorized Skill or listing its Toolbox.")
    script_path: StrictStr = Field(validation_alias=AliasChoices("script", "script_path"),
        description="Relative bundled file, for example scripts/check.py.")
    argv: list[StrictStr] = Field(default_factory=list, description="Script arguments as an argv array. An explicitly selected shell interpreter applies its own argument rules.")
    interpreter: list[StrictStr] = Field(default_factory=list, description="Optional installed interpreter argv prefix, e.g. [\"python3\"]. Python (.py) entries default to the Sandbox's python3; other entries must be directly executable when omitted.")

    @model_validator(mode="after")
    def validate_command(self):
        bundle_path(self.script_path)
        if any("\0" in part for part in [*self.argv, *self.interpreter]) or (self.interpreter and not self.interpreter[0]):
            raise ValueError("Invalid script argument or interpreter")
        return self


def skill_script_schema():
    schema = RunSkillScript.model_json_schema()
    # Public host calls retain synchronous defaults; Agent tools opt into yielding.
    schema["properties"]["wait_seconds"] = {"type": "number", "minimum": 0, "maximum": 60,
        "default": 1, "description": "Host observation budget. Use wait_sandbox_operation with the returned operation_id for unfinished work."}
    del schema["properties"]["skill_id"]
    del schema["properties"]["environment_id"]
    del schema["properties"]["target_id"]
    schema["required"].remove("skill_id")
    return schema


def resolve_skill_mount(services, agent_id: str, sandbox_id: str, request: RunSkillScript,
                        *, runtime_requirements=()):
    """Called at command submission, never from saved equipment or tool state."""
    services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
    skill_node = services.world.get_card(request.skill_id)
    allowed = authorized_resources(services, services.capabilities.derive(agent_id).capabilities, SKILL_SELECTOR)
    if skill_node.id not in allowed:
        raise PermissionDeniedError("Agent has no current access to this Skill")
    skill = Skill.model_validate(read_document(services, skill_node.id)["value"])
    # Only declared bundle bytes are projected. Node config, defaults, provider
    # credentials, sessions and host environment never become runtime files.
    files = [("SKILL.md", skill.instructions.encode("utf-8"))]
    files.extend((path, base64.b64decode(asset.data_base64, validate=True) if isinstance(asset, SkillAsset)
                  else asset.encode("utf-8")) for path, asset in skill.files.items())
    bundle = RuntimeBundle(f"skills/{skill_node.id}", tuple(files), tuple(skill.directories))
    interpreter = request.interpreter or (["python3"] if request.script_path.endswith(".py") else [])
    mount = RuntimeMount(bundle, len(interpreter))
    argv = [*interpreter, request.script_path, *request.argv]
    # Membership is validated before any backend receives the request.
    from pathlib import PurePosixPath
    mount.command(argv, PurePosixPath("/.oaw") / bundle.key)
    references = (*runtime_requirements, *skill.runtime_requirements)
    if references:
        from backend.sandbox.pack_profiles import bind_profiles
        return bind_profiles(services.plugins, references, bundle,
                             interpreter, request.script_path, request.argv)
    return argv, mount
