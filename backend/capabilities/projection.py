"""Operation tools and readable resource selectors over live graph capabilities."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
import unicodedata

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins.registry import CapabilityDefinition, CapabilitySelector


def authorized_resources(services, capabilities, selector: CapabilitySelector):
    """Expand only a declared document action's projected collection membership."""
    targets = {}
    for capability in capabilities:
        if selector.capability_kinds and capability.kind not in selector.capability_kinds:
            continue
        spec = services.plugins.node_type(capability.target_type)
        if selector.document_action:
            action = spec.document.actions.get(selector.document_action) if spec.document else None
            if action is None or not action.read_only or action.capability_kind != capability.kind:
                continue
        target = services.world.get_card(capability.target_id)
        candidates = [target]
        if selector.include_members and selector.document_action and spec.container and spec.container.document_field:
            candidates.extend(services.world.list_members(target.id))
        for node in candidates:
            if not selector.target_traits or selector.target_traits <= services.plugins.node_type(node.type).traits:
                targets[node.id] = node
    return targets


def target_aliases(targets):
    """Readable, deterministic aliases; qualify only collisions, without caching."""
    groups = defaultdict(list)
    for node in targets.values():
        base = re.sub(r"[^\w]+", "_", unicodedata.normalize("NFKC", node.name).casefold()).strip("_") or "target"
        groups[base].append(node.id)
    aliases = {}
    reserved = set(groups)
    for base, ids in sorted(groups.items()):
        if len(ids) == 1:
            aliases[base] = ids[0]
            continue
        for node_id in sorted(ids):
            digest = hashlib.sha256(node_id.encode()).hexdigest()
            length = 6
            alias = f"{base}__{digest[:length]}"
            while alias in reserved or alias in aliases:
                length += 1
                if length > len(digest):
                    raise ResourceValidationError("Unable to disambiguate resource names")
                alias = f"{base}__{digest[:length]}"
            aliases[alias] = node_id
    return aliases


def resolve_target(value, targets, parameter):
    if not isinstance(value, str) or not value:
        raise ResourceValidationError(f"Supply a {parameter} selector")
    aliases = target_aliases(targets)
    # Accept current aliases, exact names, or explicit node IDs. An ambiguous
    # spelling must never pick the first graph edge or broaden authorization.
    matches = {node.id for node in targets.values() if node.id == value or node.name == value}
    if value in aliases:
        matches.add(aliases[value])
    if len(matches) > 1:
        raise ResourceValidationError(f"Ambiguous {parameter} {value!r}; use a qualified alias from the tool listing")
    if not matches:
        raise PermissionDeniedError(f"No currently authorized {parameter} matches {value!r}; refresh the tool listing")
    return next(iter(matches))


def selector_schema(targets):
    aliases = target_aliases(targets)
    return {"type": "string", "enum": list(aliases),
            "description": "Current authorized targets: " + "; ".join(
                f"{alias} ({targets[node_id].name})" for alias, node_id in aliases.items()
            ) + ". Use a listed alias; an exact unambiguous name or node ID is also accepted. Selectors are rechecked on every invocation."}


@dataclass
class ProjectedOperation:
    definition: CapabilityDefinition
    capabilities: list
    resources: dict
    selectors: dict

    @property
    def id(self):
        return f"operation:{self.definition.tool_name}"

    def schema(self):
        schema = deepcopy(dict(self.definition.input_schema))
        schema.setdefault("type", "object")
        schema["properties"] = {self.definition.target_parameter: selector_schema(self.resources),
            **{s.parameter: selector_schema(self.selectors[s.parameter]) for s in self.definition.selectors
               if s.required or self.selectors[s.parameter]},
            **schema.get("properties", {})}
        schema["required"] = [self.definition.target_parameter, *(s.parameter for s in self.definition.selectors if s.required),
                              *schema.get("required", [])]
        schema.setdefault("additionalProperties", False)
        return schema


def project_operations(services, agent_id):
    capabilities = services.capabilities.derive(agent_id).capabilities
    scopes = {(c.kind, c.target_id) for c in capabilities}
    groups = {}
    for capability in capabilities:
        definition = services.plugins.capability_definition(capability.kind)
        if any((kind, capability.target_id) not in scopes for kind in definition.target_capabilities):
            continue
        if definition.tool_name not in groups:
            selectors = {s.parameter: authorized_resources(services, capabilities, s) for s in definition.selectors}
            groups[definition.tool_name] = ProjectedOperation(definition, [], {}, selectors)
        group = groups[definition.tool_name]
        group.capabilities.append(capability)
        group.resources[capability.target_id] = services.world.get_card(capability.target_id)
    return [group for group in groups.values() if all(
        group.selectors[s.parameter] for s in group.definition.selectors if s.required)]


def resolve_operation(services, agent_id, operation_id, arguments):
    operation = next((op for op in project_operations(services, agent_id) if op.id == operation_id), None)
    if operation is None:
        raise PermissionDeniedError("This operation is not currently available; refresh the tool listing")
    definition = operation.definition
    values = dict(arguments)
    target_id = resolve_target(values.pop(definition.target_parameter, None), operation.resources, definition.target_parameter)
    matches = [c for c in operation.capabilities if c.target_id == target_id]
    if len(matches) != 1:
        raise ResourceValidationError("Multiple capability kinds implement this operation for the selected target")
    selected = matches[0]
    for selector in definition.selectors:
        if selector.argument in values and selector.argument != selector.parameter:
            raise ResourceValidationError(f"Use {selector.parameter}, not the internal {selector.argument} field")
        if not selector.required and selector.parameter not in values:
            continue
        values[selector.argument] = resolve_target(values.pop(selector.parameter, None), operation.selectors[selector.parameter], selector.parameter)
    return authorize_invocation(services, agent_id, selected.id, values), values


def authorize_invocation(services, agent_id, capability_id, values):
    """Check every declared scope, including calls using legacy internal IDs."""
    live = services.capabilities.capability_for_id(agent_id, capability_id)
    definition = services.plugins.capability_definition(live.kind)
    for kind in definition.target_capabilities:
        services.capabilities.capability_for_id(agent_id, f"{kind}:{live.target_id}")
    current = services.capabilities.derive(agent_id).capabilities
    for selector in definition.selectors:
        if not selector.required and selector.argument not in values:
            continue
        value = values.get(selector.argument)
        if not isinstance(value, str) or value not in authorized_resources(services, current, selector):
            raise PermissionDeniedError(f"Access to {selector.parameter} was revoked")
    return live
