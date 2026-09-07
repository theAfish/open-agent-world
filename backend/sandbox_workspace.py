"""User and live-capability workspace operations using existing Sandbox backends."""
import base64
from urllib.parse import urlsplit

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.execution_config import configuration_summary
from backend.node_documents import read_document
from backend.skill_runtime import SKILL_SELECTOR
from backend.capabilities.projection import authorized_resources
from open_agent_world.skill_packages import Skill, SkillAsset
from backend.sandbox.models import SandboxNetworkError


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
        return await services._require_sandbox_backend().file_operation(sandbox_id, "write", root="workspace",
            path=destination, data=data, overwrite=overwrite)


async def diagnostics(services, sandbox_id, destination=None):
    info = await services.get_sandbox(sandbox_id)
    summary = configuration_summary(services, sandbox_id)
    if destination:
        parsed = urlsplit(destination)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ResourceValidationError("Use an HTTP(S) destination without credentials, query or fragment")
        if not info.network_enabled:
            raise ResourceValidationError("Networking is disabled; enable a supported mode in settings first")
        argv = ["curl", "--disable", "--noproxy", "*", "--silent", "--show-error", "--output",
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
