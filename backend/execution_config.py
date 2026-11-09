"""Portable execution documents; credential bindings are deliberately host-private."""
from __future__ import annotations

import json
from typing import Any, Literal
from backend.errors import ResourceValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from backend.plugins.documents import NodeDocumentAction, NodeDocumentDefinition
from backend.plugins.registry import CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, NodeTypeDefinition, RelationshipDefinition
from backend.environment_values import EnvironmentOperation, EnvironmentValue
from backend.sandbox.environment import environment_step, validate_command_environment
from backend.security.execution_folders import FolderRequirement, FileRequirement, PathRequirement, PATH_REQUIREMENTS, folder_store, GlobalExecutionFolderStore
from backend.sandbox.models import FolderMount, SandboxValidationError

TARGET_VARIABLE = "OAW_TARGET_CONFIG_JSON"
ENVIRONMENT_SELECTOR = CapabilitySelector("environment", "environment_id", capability_kinds=frozenset({"environment.use"}),
    target_traits=frozenset({"core.environment"}), required=False)
TARGET_SELECTOR = CapabilitySelector("target", "target_id", capability_kinds=frozenset({"compute_target.read"}),
    target_traits=frozenset({"core.compute-target"}), required=False)
EXECUTION_SELECTORS = (ENVIRONMENT_SELECTOR, TARGET_SELECTOR)


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available"] = "available"


class SecretRequirement(EnvironmentOperation):
    model_config = ConfigDict(extra="forbid")
    secret_ref: StrictStr = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")


class EnvironmentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: dict[str, StrictStr | EnvironmentValue | SecretRequirement | FolderRequirement | FileRequirement | PathRequirement] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_variables(self):
        validate_profile_variables(self.variables)
        return self


def validate_profile_variables(variables, *, allow_host=False):
    for name, value in variables.items():
        if name.upper() == "PATH" and isinstance(value, (SecretRequirement, FileRequirement)):
            raise SandboxValidationError("PATH extensions require directory values or folder references")
    validate_command_environment({key: value if isinstance(value, str) else environment_step(value, "bound-path" if not isinstance(value, EnvironmentValue) else value.value)
                                  for key, value in variables.items()}, allow_target=False, allow_host=allow_host)


class ComputeTarget(BaseModel):
    """Plugins may specialize config with their own Pydantic model and validation."""
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = ""
    provider_id: StrictStr = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def json_configuration(self):
        json.dumps(self.model_dump(mode="json"), allow_nan=False)
        return self


async def _read(context, capability, arguments):
    return await context.node_document_action(capability, "read", arguments)


def _read_document(value, arguments):
    if arguments:
        raise ValueError("This read operation takes no arguments")
    return value


def register_execution_configuration(registry):
    registry.register_relationship(RelationshipDefinition(canvas_requires_confirmation=True, id="environment.default", label="Default environment",
        short_label="defaults", description="Live base profile shared with all executions authorized for this Sandbox; local values override it.",
        source_traits=frozenset({"core.environment"}), target_traits=frozenset({"core.sandbox"}), templateable=True))
    for node_id, label, trait, model, kind, tool in (
        ("environment", "Environment Profile", "core.environment", EnvironmentProfile, "environment.use", "inspect_environment_profile"),
        ("compute-target", "Compute Target", "core.compute-target", ComputeTarget, "compute_target.read", "read_compute_target"),
    ):
        registry.register_capability(CapabilityDefinition(kind, tool,
            "Inspect the selected configuration document. Secret references are unbound requirements; values are never returned.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False}), _read)
        registry.register_node_type(NodeTypeDefinition(id=node_id, label=label,
            description="Optional configuration selected explicitly for each Sandbox command.", icon="wrench", color="#687f79",
            deck_id="objects", deck_label="Objects", deck_icon="boxes", default_name=label, default_size=(340, 240),
            default_status="available", statuses=frozenset({"available"}), config_model=EmptyConfig,
            traits=frozenset({trait, "ui.execution-config.v1"}), templateable=True,
            document=NodeDocumentDefinition(model=model,
                actions={"read": NodeDocumentAction(_read_document, capability_kind=kind, read_only=True)})))
        registry.register_relationship(RelationshipDefinition(canvas_requires_confirmation=True, id=kind, label="Use" if node_id == "environment" else "Read target",
            short_label="use", description="Allow explicit selection for a command; never inject automatically.",
            source_traits=frozenset({"core.agent"}), target_traits=frozenset({trait}), templateable=True,
            capabilities=(CapabilityGrantDefinition(kind),)))


def resolve_execution_configuration(services, environment_id, target_id):
    """All scopes must be checked by the caller BEFORE reading documents/secrets."""
    from backend.node_documents import read_document
    environment = {}
    secrets = []
    if environment_id is not None:
        profile = EnvironmentProfile.model_validate(read_document(services, environment_id)["value"])
        for key, value in profile.variables.items():
            requirement = value
            if isinstance(value, PATH_REQUIREMENTS):
                raise SandboxValidationError("Folder variables require Sandbox execution")
            if isinstance(value, SecretRequirement):
                value = resolve_environment_secret(services, environment_id, key, value.secret_ref)
                secrets.append(value)
            text = value if isinstance(value, str) else value.value
            environment[key] = text if getattr(requirement, "operation", "replace") == "replace" else [environment_step(requirement, text)]
    if target_id is not None:
        target = read_document(services, target_id)["value"]
        environment[TARGET_VARIABLE] = json.dumps(target, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    validate_command_environment(environment)
    return environment, tuple(secrets)


def resolve_environment_secret(services, owner, name, reference):
    if not services.execution_credentials.configured(owner, reference):
        from backend.errors import ResourceValidationError
        node = services.world.get_card(owner)
        raise ResourceValidationError(
            f"Secret variable {name!r} is unbound on {node.name!r} ({owner}); "
            "enter its secret in Environment variables and save, or remove the unused variable"
        )
    return services.execution_credentials.resolve(owner, reference)


def linked_environment(services, sandbox_id):
    profiles = [e.source for e in services.world.list_edges_to(sandbox_id) if e.relationship == "environment.default"]
    if len(profiles) > 1:
        raise ValueError("A Sandbox accepts at most one default Environment Profile")
    return profiles[0] if profiles else None


def shared_environment(services):
    source = services.execution_environment_source
    return source.read() if source is not None else None


def _effective_variable_steps(services, sandbox_id, environment_id, shared):
    from backend.node_documents import read_document
    from backend.sandbox.settings import SandboxSettingsStore
    from backend.sandbox.environment import HOST_ENVIRONMENT_NAMES
    profile_id = environment_id or linked_environment(services, sandbox_id)
    defaults = SandboxSettingsStore(services.database, services.settings.data_root).read()
    layers = [("shared", "shared", shared.variables if shared else {}),
              ("global", "global", {name: value for name, value in defaults.environment_variables.items()
                                    if name.upper() not in HOST_ENVIRONMENT_NAMES})]
    for node_id, source in ((profile_id, "invocation" if environment_id else "linked"), (sandbox_id, "local")):
        if node_id is not None:
            profile = EnvironmentProfile.model_validate(read_document(services, node_id)["value"])
            layers.append((node_id, source, profile.variables))
    effective = {}
    for owner, source, variables in layers:
        for name, value in variables.items():
            # Replacement discards lower layers before any private bindings are read.
            step = (name, value, owner, source)
            if getattr(value, "operation", "replace") == "replace":
                effective[name.upper()] = [step]
            else:
                effective.setdefault(name.upper(), []).append(step)
    return profile_id, list(effective.values())


def effective_variables(services, sandbox_id, environment_id=None):
    profile_id, chains = _effective_variable_steps(services, sandbox_id, environment_id, shared_environment(services))
    return profile_id, [chain[-1] for chain in chains]


def resolve_sandbox_configuration(services, sandbox_id, environment_id=None, target_id=None, *, folder_mounts=None):
    shared = shared_environment(services)
    _, chains = _effective_variable_steps(services, sandbox_id, environment_id, shared)
    environment, secrets = _resolve_environment_chains(services, chains, shared, folder_mounts=folder_mounts)
    if target_id:
        target_env, _ = resolve_execution_configuration(services, None, target_id)
        environment.update(target_env)
    validate_command_environment(environment)
    return environment, secrets


def runtime_environment_defaults(services):
    """Share operator-approved tool variables, never the hosting process environment."""
    shared = shared_environment(services)
    chains = [[(name,value,'shared','shared')] for name,value in shared.variables.items()] if shared else []
    environment, secrets = _resolve_environment_chains(services, chains, shared)
    validate_command_environment(environment)
    return environment, secrets


def _resolve_environment_chains(services, chains, shared, *, folder_mounts=None):
    environment, secrets = {}, []
    for chain in chains:
        name = chain[-1][0]
        structured = len(chain) > 1 or getattr(chain[0][1], "operation", "replace") != "replace"
        steps = []
        for index, (_, value, owner, source) in enumerate(chain):
            text = value if isinstance(value, str) else getattr(value, "value", "")
            if isinstance(value, PATH_REQUIREMENTS):
                if folder_mounts is None:
                    raise SandboxValidationError("This caller does not support folder variables")
                store = GlobalExecutionFolderStore(services.database, services.settings.data_root) if source == "global" else folder_store(services)
                try:
                    binding = store.resource(owner, value)
                except ResourceValidationError:
                    raise ResourceValidationError(f"Path variable {name!r} requires rebinding; choose its file or folder again and save") from None
                if name.upper() == "PATH" and binding.kind != "folder":
                    raise SandboxValidationError("PATH extensions require a folder")
                folder_mounts.append(FolderMount(name, binding.path, binding.access, binding.kind, index if structured else None))
                text = binding.path
            if isinstance(value, SecretRequirement):
                if source == "shared":
                    text = shared.secrets[value.secret_ref]
                elif source == "global":
                    from backend.security.execution_credentials import GlobalExecutionCredentialStore
                    try:
                        text = GlobalExecutionCredentialStore(services.database, services.settings.data_root).resolve(None, value.secret_ref)
                    except ResourceValidationError:
                        raise ResourceValidationError(f"Global secret variable {name!r} requires rebinding in Settings > Sandbox") from None
                else:
                    text = resolve_environment_secret(services, owner, name, value.secret_ref)
                secrets.append(text)
            steps.append(environment_step(value, text))
        environment[name] = steps if structured else steps[0]["value"]
    return environment, tuple(secrets)


def _configuration_summaries(services, variables, shared):
    from backend.security.execution_credentials import GlobalExecutionCredentialStore
    global_credentials = GlobalExecutionCredentialStore(services.database, services.settings.data_root)
    result = []
    for name, value, owner, source in variables:
        secret = isinstance(value, SecretRequirement)
        folder = isinstance(value, PATH_REQUIREMENTS)
        configured = True
        shown = value.value if isinstance(value, EnvironmentValue) else value
        folder_access = None
        binding_error = None
        available = None
        binding = None
        if secret:
            store = global_credentials if source == "global" else services.execution_credentials
            configured = value.secret_ref in shared.secrets if source == "shared" else store.configured(owner, value.secret_ref)
            available = configured if source == "shared" else store.readable(owner, value.secret_ref)
            if configured and not available:
                binding_error = "Re-enter the secret and save."
        if folder:
            store = GlobalExecutionFolderStore(services.database, services.settings.data_root) if source == "global" else folder_store(services)
            configured = store.configured(owner, value.reference)
            if configured:
                try:
                    binding = store.folder(owner, value.reference)
                except (ResourceValidationError, ValueError):
                    available = False
                    binding_error = "Choose the file or folder again and save."
            shown = binding.path if binding else None
            folder_access = binding.access.value if binding else None
            if binding:
                from backend.sandbox.settings import SandboxSettingsStore
                try:
                    if value.kind != "path" and binding.kind != value.kind:
                        raise SandboxValidationError("Path type changed; choose the file or folder again and save")
                    if name.upper() == "PATH" and binding.kind != "folder":
                        raise SandboxValidationError("PATH extensions require a folder")
                    SandboxSettingsStore(services.database, services.settings.data_root).validator.validate_environment_path(binding.path, binding.kind)
                    available = True
                except (ValueError, OSError, SandboxValidationError) as error:
                    available = False
                    binding_error = str(error)
        result.append({"name": name, "source": source, "owner": owner, "secret": secret,
            "operation": getattr(value, "operation", "replace"), "separator": getattr(value, "separator", None),
            "folder": folder, "kind": binding.kind if folder and binding else None, "value": None if secret else shown,
            **({"error": binding_error, "available": available,
                "status": "unconfigured" if not configured else
                    ("rebind_required" if secret or binding is None else "path_unavailable") if available is False else "configured"} if secret or folder else {}),
            **({"access": folder_access, "validation_scope": "host_path"} if folder else {}),
            "configured": configured})
    return result


def configuration_summary(services, sandbox_id):
    shared = shared_environment(services)
    profile_id, chains = _effective_variable_steps(services, sandbox_id, None, shared)
    summaries = iter(_configuration_summaries(services, [step for chain in chains for step in chain], shared))
    result = []
    for chain in chains:
        steps = [next(summaries) for _ in chain]
        shown = dict(steps[-1])
        if len(steps) > 1:
            shown["steps"] = steps
            shown["configured"] = all(step["configured"] for step in steps)
            if any(step.get("available") is False for step in steps):
                shown["available"] = False
            if any(step["secret"] for step in steps):
                shown.update(secret=True, value=None)
        result.append(shown)
    return {"profile_id": profile_id, "variables": result,
            "ready": all(v["configured"] and v.get("available") is not False for v in result)}
