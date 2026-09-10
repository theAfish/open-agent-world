"""Portable execution documents; credential bindings are deliberately host-private."""
from __future__ import annotations

import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from backend.plugins.documents import NodeDocumentAction, NodeDocumentDefinition
from backend.plugins.registry import CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, NodeTypeDefinition, RelationshipDefinition
from backend.sandbox.environment import validate_command_environment

TARGET_VARIABLE = "OAW_TARGET_CONFIG_JSON"
ENVIRONMENT_SELECTOR = CapabilitySelector("environment", "environment_id", capability_kinds=frozenset({"environment.use"}),
    target_traits=frozenset({"core.environment"}), required=False)
TARGET_SELECTOR = CapabilitySelector("target", "target_id", capability_kinds=frozenset({"compute_target.read"}),
    target_traits=frozenset({"core.compute-target"}), required=False)
EXECUTION_SELECTORS = (ENVIRONMENT_SELECTOR, TARGET_SELECTOR)


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available"] = "available"


class SecretRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret_ref: StrictStr = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")


class EnvironmentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: dict[str, StrictStr | SecretRequirement] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_variables(self):
        validate_command_environment({key: value if isinstance(value, str) else "" for key, value in self.variables.items()}, allow_target=False)
        return self


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
    registry.register_relationship(RelationshipDefinition(id="environment.default", label="Default environment",
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
        registry.register_relationship(RelationshipDefinition(id=kind, label="Use" if node_id == "environment" else "Read target",
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
            if isinstance(value, SecretRequirement):
                value = resolve_environment_secret(services, environment_id, key, value.secret_ref)
                secrets.append(value)
            environment[key] = value
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


def effective_variables(services, sandbox_id, environment_id=None):
    from backend.node_documents import read_document
    profile_id = environment_id or linked_environment(services, sandbox_id)
    layers = [(profile_id, "invocation" if environment_id else "linked"), (sandbox_id, "local")]
    effective = {}
    for node_id, source in layers:
        if node_id is None:
            continue
        profile = EnvironmentProfile.model_validate(read_document(services, node_id)["value"])
        for name, value in profile.variables.items():
            # Overrides are portable and case insensitive, like validation.
            effective[name.upper()] = (name, value, node_id, source)
    return profile_id, list(effective.values())


def resolve_sandbox_configuration(services, sandbox_id, environment_id=None, target_id=None):
    _, variables = effective_variables(services, sandbox_id, environment_id)
    environment, secrets = {}, []
    for name, value, owner, _ in variables:
        if isinstance(value, SecretRequirement):
            value = resolve_environment_secret(services, owner, name, value.secret_ref)
            secrets.append(value)
        environment[name] = value
    if target_id:
        target_env, _ = resolve_execution_configuration(services, None, target_id)
        environment.update(target_env)
    validate_command_environment(environment)
    return environment, tuple(secrets)


def configuration_summary(services, sandbox_id):
    profile_id, variables = effective_variables(services, sandbox_id)
    result = []
    for name, value, owner, source in variables:
        secret = isinstance(value, SecretRequirement)
        result.append({"name": name, "source": source, "owner": owner, "secret": secret,
            "value": None if secret else value,
            "configured": services.execution_credentials.configured(owner, value.secret_ref) if secret else True})
    return {"profile_id": profile_id, "variables": result, "ready": all(v["configured"] for v in result)}
