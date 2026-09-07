from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import AgentNodeTemplateHandler


class CodexCardConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_source: Literal["auto", "desktop", "cli", "manual"] = Field(
        default="auto", title="Local Codex", description="Auto prefers the desktop App's native runtime, then CLI.")
    codex_command: str = Field(default="", title="Native executable", description="Used when Local Codex is manual. Select codex.exe, not a shell wrapper.")
    workspace_path: str = Field(default="", title="Project folder", description="Absolute folder where Codex reads, edits and runs commands. Required before running.")
    model: str = Field(default="default", title="Model", description="default inherits local Codex settings; or enter an explicit Codex model ID.")
    reasoning_effort: Literal["default", "minimal", "low", "medium", "high", "xhigh", "max"] = Field(
        default="default", title="Reasoning effort", description="Must be supported by the selected model. default inherits Codex settings.")
    session_mode: Literal["continue", "fresh"] = Field(default="continue", title="Session mode", description="continue remembers each OAW conversation; fresh starts a new session on each Run.")
    codex_sandbox: Literal["read-only", "workspace-write"] = Field(default="workspace-write", title="Native file access", description="Codex sandbox for project files. OAW tool access follows graph connections.")
    system_instruction: str = Field(default="You are Codex working in Open Agent World. Help with the project and connected resources.", title="Additional instructions", json_schema_extra={"format": "textarea"})
    runtime_provider_id: Literal["openai.codex"] = "openai.codex"
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: Literal[False] = False


class CodexCardTemplate(AgentNodeTemplateHandler):
    # Machine paths deliberately stay local. Restored cards need a project folder.
    portable_config_fields = AgentNodeTemplateHandler.portable_config_fields | frozenset({
        "reasoning_effort", "session_mode", "codex_sandbox", "inherit_legion_model",
    })
