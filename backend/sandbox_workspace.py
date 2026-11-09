"""User and live-capability workspace operations using existing Sandbox backends."""
import base64
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.execution_config import configuration_summary
from backend.node_documents import read_document
from backend.skill_runtime import SKILL_SELECTOR
from backend.capabilities.projection import authorized_resources
from open_agent_world.skill_packages import Skill, SkillAsset
from backend.sandbox.models import SandboxNetworkError
from backend.sandbox.task_workspace import task_path, ensure_directory, path_for, record_shared_input


def require_skill(services, skill_id, agent_id=None):
    if agent_id is not None:
        allowed = authorized_resources(services, services.capabilities.derive(agent_id).capabilities, SKILL_SELECTOR)
        if skill_id not in allowed:
            raise PermissionDeniedError("No current access to this Skill")
    node = services.world.get_card(skill_id)
    if "oaw.skill" not in services.plugins.node_type(node.type).traits:
        raise ResourceValidationError("Select a Skill")
    snapshot = read_document(services, skill_id)
    return snapshot, Skill.model_validate(snapshot["value"])


async def copy_skill(services, sandbox_id, skill_id, source, destination, overwrite=False, agent_id=None):
    async with services._node_mutation():
        services._require_card_type(sandbox_id, "sandbox")
        if agent_id:
            services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        _, skill = require_skill(services, skill_id, agent_id)
        content = skill.instructions if source == "SKILL.md" else skill.files.get(source)
        if content is None:
            raise ResourceValidationError("Skill resource is missing")
        data = content.data_base64 if isinstance(content, SkillAsset) else base64.b64encode(content.encode()).decode()
        await ensure_directory(services, sandbox_id, agent_id)
        return await services._require_sandbox_backend().file_operation(sandbox_id, "write", root="workspace",
            path=task_path(services, sandbox_id, destination, agent_id), data=data, overwrite=overwrite, create_parents=True)


async def diagnostics(services, sandbox_id, destination=None):
    info = await services.get_sandbox(sandbox_id)
    summary = configuration_summary(services, sandbox_id)
    if destination:
        parsed = urlsplit(destination)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ResourceValidationError("Use an HTTP(S) destination without credentials, query or fragment")
        if not info.network_enabled:
            raise ResourceValidationError("Networking is disabled; enable a supported mode in settings first")
        argv = ["curl", "--disable",
            *([] if info.network_transport == "proxy_tcp" else ["--noproxy", "*"]),
            "--silent", "--show-error", "--output",
            "NUL" if info.platform == "windows" else "/dev/null", "--max-time", "8",
            "--write-out", "%{http_code}", "--", destination]
        try:
            result = await services.execute_sandbox(sandbox_id, argv, timeout_seconds=12, _keep_on_disconnect=True)
        except SandboxNetworkError as exc:
            return {"status": "network_setup_failed", "configuration": summary,
                "workspace_access": str(info.workspace_access), "network_enabled": info.network_enabled,
                "network_reason": str(exc), "stdout": "", "stderr": "", "exit_code": None}
        code = result.stdout.strip()
        missing = result.exit_code in {127, 9009} or (result.exit_code != 0 and any(text in result.stderr.lower() for text in ("no such file", "not found")))
        status = ("missing_tool" if missing else
            "dns_failed" if result.exit_code == 6 else
            "tls_verification_failed" if result.exit_code in {51, 60, 77, 83, 90, 91} else
            "connection_failed" if result.exit_code else
            "authentication_failed" if code in {"401", "403", "407"} else
            "http_error" if code.isdecimal() and int(code) >= 400 else "connected")
    else:
        command = ('ver & echo Workspace & cd & ' + ' & '.join(
            f'(where {tool} >nul 2>nul && {tool} --version || echo {tool}: missing)' for tool in ("python", "python3", "node", "curl", "git"))
            if info.platform == "windows" else
            'printf "Workspace: "; pwd; test -r . && echo readable; test -w . && echo writable; '
            'for tool in python3 python node curl git; do if command -v "$tool"; then "$tool" --version; else printf "%s: missing\\n" "$tool"; fi; done')
        result = await services.execute_sandbox(sandbox_id, command=command, timeout_seconds=20, _keep_on_disconnect=True)
        status = "checked"
    return {"status": status, "configuration": summary, "workspace_access": str(info.workspace_access), "network_enabled": info.network_enabled,
        "network_reason": info.network_reason, "stdout": result.stdout, "stderr": result.stderr,
        "exit_code": result.exit_code, "note": "Missing interpreters/packages are not installed. Code receiving a secret can read it."}


# These tools use the same file backend and path boundary as the Sandbox UI.
# They never execute user/model code or accept host filesystem paths.


class WorkspaceList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["workspace", "all", "shared"] = Field(default="workspace", description="workspace uses the assigned directory; all reads the full Sandbox root (other directories are read only); shared reads the Sandbox's explicit shared/ input folder.")
    path: str = Field(default="", max_length=4096,
        description="Workspace-relative directory. Omit or use an empty string for the workspace root; "
                    "'.' is not accepted. Use forward slashes and no parent traversal.")
    cursor: str = Field(default="", max_length=8192)
    query: str = Field(default="", max_length=256)

    @field_validator('scope', mode='before')
    @classmethod
    def legacy_scope(cls, value):
        return 'workspace' if value == 'task' else value


class WorkspacePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["workspace", "all", "shared"] = Field(default="workspace", description="Use all for read-only access to other workspace files, or shared for explicit shared/ inputs.")
    path: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)

    @field_validator('scope', mode='before')
    @classmethod
    def legacy_scope(cls, value):
        return 'workspace' if value == 'task' else value


async def inspect_workspace(services, capability, operation, options):
    expected = {"sandbox.list_files": ("list", WorkspaceList), "sandbox.read_file": ("preview", WorkspacePreview)}
    kind = expected.get(capability.kind)
    if kind is None or operation != kind[0] or not isinstance(options, kind[1]):
        raise ResourceValidationError("Use the declared read-only Sandbox file operation")
    async with services._node_mutation(read_only=True):
        live = services.capabilities.capability_for_id(capability.agent_id, capability.id)
        if live.kind != capability.kind or live.target_id != capability.target_id:
            raise PermissionDeniedError("Sandbox file access was revoked")
        services.capabilities.require_sandbox_execute(capability.agent_id, live.target_id)
        workspace = await ensure_directory(services, live.target_id, capability.agent_id)
        arguments = options.model_dump(exclude={"offset", "limit", "scope"})
        prefix = "shared" if options.scope == "shared" else "" if options.scope == "all" else workspace["directory"]
        arguments["path"] = path_for(prefix, options.path)
        try:
            result = await services._require_sandbox_backend().file_operation(
                live.target_id, operation, root="workspace", **arguments)
        except FileNotFoundError:
            if options.scope == 'shared' and operation == 'list' and not options.path:
                return {'sandbox_id': live.target_id, 'path': '', 'entries': [], 'exists': False,
                    'scope': 'shared', 'note': 'No shared directory exists; no files were created.'}
            raise ResourceValidationError(f"Path not found: {options.path or '.'}. File paths are relative to "
                + ('shared/.' if options.scope == 'shared' else 'the assigned workspace directory; do not prefix that folder name again. Use scope=all for read-only access to the full workspace.')) from None
    if operation == "list":
        if prefix:
            result["entries"] = [{**entry, "path": entry["path"][len(prefix) + 1:]} for entry in result.get("entries", [])]
        return {"sandbox_id": live.target_id, "path": options.path, "query": options.query,
                "scope": "recursive filename search" if options.query.strip() else "directory",
                **result}
    if result.get("state") != "text":
        # Images/binary files are not serialized into an LLM text tool reply.
        return {"sandbox_id": live.target_id, "path": options.path,
                "state": result.get("state"), **({"limit": result["limit"]} if "limit" in result else {})}
    text = result["text"]
    if options.scope == "shared":
        record_shared_input(services, live.target_id, options.path, capability.agent_id)
    if options.scope == "all":
        record_shared_input(services, live.target_id, options.path, capability.agent_id, root="")
    offset = min(len(text), options.offset)
    end = min(len(text), offset + options.limit)
    return {"sandbox_id": live.target_id, "path": options.path, "state": "text",
            "text": text[offset:end], "offset": offset, "total_chars": len(text),
            "truncated": end < len(text), "next_offset": end if end < len(text) else None}
