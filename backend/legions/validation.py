"""Pure Legion validation shared by capture, distribution and instantiation."""
from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

from backend.errors import GraphValidationError, PluginCompatibilityError, PluginUnavailableError
from backend.legions.models import LegionRecord
from backend.plugins.template import NodeTemplateDependency, NodeTemplateHandler

if TYPE_CHECKING:
    from backend.plugins.registry import PluginRegistry


def compatibility_issues(record: LegionRecord, registry: PluginRegistry) -> list[str]:
    if record.blueprint.format_version != 1:
        return [
            f"blueprint format {record.blueprint.format_version} is not supported"
        ]
    issues: list[str] = []
    nodes = {node.key: node for node in record.blueprint.nodes}
    if len(nodes) != len(record.blueprint.nodes):
        issues.append("blueprint contains duplicate node keys")
    for node in record.blueprint.nodes:
        if not registry.has_plugin(node.plugin_id):
            issues.append(
                f"node type {node.type!r} requires missing plugin {node.plugin_id!r}"
            )
            continue
        if not registry.is_enabled(node.plugin_id):
            issues.append(f"node type {node.type!r} requires disabled plugin {node.plugin_id!r}")
            continue
        try:
            definition = registry.node_type(node.type)
            owner = registry.node_type_owner_id(node.type)
        except (GraphValidationError, PluginUnavailableError, ValueError) as error:
            issues.append(str(error))
            continue
        if owner != node.plugin_id:
            issues.append(
                f"node type {node.type!r} is now owned by {owner!r}, not "
                f"{node.plugin_id!r}"
            )
        if not definition.templateable:
            issues.append(f"node type {node.type!r} is no longer templateable")
        if (
            definition.template_status is not None
            and node.status != definition.template_status
        ):
            issues.append(
                f"node type {node.type!r} now requires template status "
                f"{definition.template_status!r}, not {node.status!r}"
            )
        validated_config = node.config
        config_is_valid = True
        try:
            registry.validate_status(node.type, node.status)
            validated_config = registry.validate_config(node.type, node.config)
            if validated_config != node.config:
                issues.append(
                    f"node type {node.type!r} portable configuration is no longer "
                    "accepted unchanged"
                )
            restored_status = str(
                validated_config.get("status", definition.default_status)
            )
            if restored_status != node.status:
                issues.append(
                    f"node type {node.type!r} template status {node.status!r} "
                    f"does not match its portable configuration ({restored_status!r})"
                )
        except GraphValidationError as error:
            issues.append(str(error))
            config_is_valid = False
        if node.initial_document is not None:
            if definition.document is None:
                issues.append(f"node type {node.type!r} no longer provides its document contract")
            else:
                try:
                    definition.document.model.model_validate(node.initial_document)
                except ValueError as error:
                    issues.append(f"node type {node.type!r} document is incompatible: {error}")
        handler = definition.template_handler
        if handler is not None and config_is_valid:
            try:
                declared_dependencies = {
                    (dependency.kind, dependency.id)
                    for dependency in template_dependencies(
                        handler, validated_config
                    )
                }
            except PluginCompatibilityError as error:
                issues.append(
                    f"node type {node.type!r} template dependencies are invalid: "
                    f"{error}"
                )
                declared_dependencies = set()
            stored_dependencies = {
                (dependency.kind, dependency.id)
                for dependency in node.dependencies
            }
            for dependency_kind, dependency_id in sorted(
                declared_dependencies - stored_dependencies
            ):
                issues.append(
                    f"node type {node.type!r} requires unrecorded template "
                    f"dependency {dependency_kind.replace('_', ' ')} "
                    f"{dependency_id!r}"
                )
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency in node.dependencies:
            dependency_key = (dependency.kind, dependency.id)
            if dependency_key in seen_dependencies:
                issues.append(
                    f"node type {node.type!r} contains duplicate template dependency "
                    f"{dependency.kind.replace('_', ' ')} {dependency.id!r}"
                )
                continue
            seen_dependencies.add(dependency_key)
            try:
                dependency_owner = registry.owner_id(
                    dependency.kind, dependency.id
                )
            except ValueError:
                if not registry.has_plugin(dependency.plugin_id):
                    issues.append(
                        f"node type {node.type!r} template dependency "
                        f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                        f"requires missing plugin {dependency.plugin_id!r}"
                    )
                else:
                    issues.append(
                        f"node type {node.type!r} requires missing template "
                        f"dependency {dependency.kind.replace('_', ' ')} "
                        f"{dependency.id!r}"
                    )
                continue
            if dependency_owner != dependency.plugin_id:
                issues.append(
                    f"node type {node.type!r} template dependency "
                    f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                    f"is now owned by {dependency_owner!r}, not "
                    f"{dependency.plugin_id!r}"
                )
            elif not registry.is_enabled(dependency.plugin_id):
                issues.append(
                    f"node type {node.type!r} template dependency "
                    f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                    f"requires unavailable plugin {dependency.plugin_id!r}"
                )
        if node.payload is None and handler is not None:
            issues.append(
                f"node type {node.type!r} now requires template payload that the "
                "saved Legion does not contain"
            )
        elif node.payload is not None:
            if handler is None:
                issues.append(
                    f"node type {node.type!r} no longer provides its template handler"
                )
            elif node.payload_version is None or not handler.supports_payload_version(
                node.payload_version
            ):
                issues.append(
                    f"node type {node.type!r} template payload version "
                    f"{node.payload_version!r} is unsupported"
                )
            else:
                try:
                    handler.validate_payload(node.payload, node.payload_version)
                except PluginCompatibilityError as error:
                    issues.append(
                        f"node type {node.type!r} template payload is invalid: "
                        f"{error}"
                    )
    for edge in record.blueprint.edges:
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        if source is None or target is None:
            issues.append(f"edge {edge.key!r} references an unknown node")
            continue
        if not registry.has_plugin(edge.plugin_id):
            issues.append(
                f"relationship {edge.relationship!r} requires missing plugin "
                f"{edge.plugin_id!r}"
            )
            continue
        try:
            definition = registry.relationship(edge.relationship)
            owner = registry.relationship_owner_id(edge.relationship)
            if owner != edge.plugin_id:
                issues.append(
                    f"relationship {edge.relationship!r} is now owned by {owner!r}, "
                    f"not {edge.plugin_id!r}"
                )
            if not definition.templateable:
                issues.append(
                    f"relationship {edge.relationship!r} is no longer templateable"
                )
            registry.validate_relationship_order(
                source.type, target.type, edge.relationship
            )
            registry.validate_direction(
                edge.relationship, edge.direction.value
            )
        except (GraphValidationError, PluginUnavailableError, ValueError) as error:
            issues.append(str(error))
    return list(dict.fromkeys(issues))

def template_dependencies(
    handler: NodeTemplateHandler, config: Mapping[str, Any]
) -> tuple[NodeTemplateDependency, ...]:
    dependencies = handler.dependencies(config)
    if not isinstance(dependencies, tuple):
        raise PluginCompatibilityError(
            "template dependencies must be returned as a tuple"
        )
    if not all(
        isinstance(dependency, NodeTemplateDependency)
        for dependency in dependencies
    ):
        raise PluginCompatibilityError(
            "template dependencies must be NodeTemplateDependency values"
        )
    keys = [(dependency.kind, dependency.id) for dependency in dependencies]
    if len(keys) != len(set(keys)):
        raise PluginCompatibilityError(
            "template dependencies must not contain duplicates"
        )
    return dependencies
