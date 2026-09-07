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
    skill_id: StrictStr = Field(description="World node ID returned by reading an authorized Skill or listing its Toolbox.")
    script_path: StrictStr = Field(validation_alias=AliasChoices("script", "script_path"),
        description="Relative bundled file, for example scripts/check.py.")
    argv: list[StrictStr] = Field(default_factory=list, description="Script arguments as an argv array. An explicitly selected shell interpreter applies its own argument rules.")
    interpreter: list[StrictStr] = Field(default_factory=list, description="Optional installed interpreter argv prefix, e.g. [\"python3\"]. Omit for a directly executable file. Use the Sandbox inspect tool to choose its runtime tools.")

    @model_validator(mode="after")
    def validate_command(self):
        bundle_path(self.script_path)
        if any("\0" in part for part in [*self.argv, *self.interpreter]) or (self.interpreter and not self.interpreter[0]):
            raise ValueError("Invalid script argument or interpreter")
        return self


def skill_script_schema():
    schema = RunSkillScript.model_json_schema()
    del schema["properties"]["skill_id"]
    del schema["properties"]["environment_id"]
    del schema["properties"]["target_id"]
    schema["required"].remove("skill_id")
    return schema


def resolve_skill_mount(services, agent_id: str, sandbox_id: str, request: RunSkillScript):
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
    mount = RuntimeMount(bundle, len(request.interpreter))
    argv = [*request.interpreter, request.script_path, *request.argv]
    # Membership is validated before any backend receives the request.
    from pathlib import PurePosixPath
    mount.command(argv, PurePosixPath("/.oaw") / bundle.key)
    return argv, mount
