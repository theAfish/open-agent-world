"""Opt-in public surfaces for existing plugin views and business handlers."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.publication_scope import public_value


def default_deployment(node):
    """The administrator publishes the node's existing registered business API."""
    fields = node.config_model.model_json_schema().get("properties", {})
    return NodeDeploymentDefinition(surface=DeploymentSurface(
        config_fields=frozenset(key for key in public_value(fields) if not fields[key].get("writeOnly")),
        document="edit" if node.document is not None else "none",
        resource_actions={name: frozenset({"*"}) for name in node.resource_actions},
        execution=node.execution is not None, file_preview="core.file-viewer" in node.traits,
    ), shared_sections=True)


def deployment_for(node, section=None):
    contract = node.deployment
    if contract is not None and ((contract.surface is not None and (section is None or contract.shared_sections)) or section in contract.sections):
        return contract
    return default_deployment(node)


class DeploymentSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Opt in to the existing document API as a whole. Resolved into a frozen
    # action/download allowlist at publication, never inferred for legacy cards.
    document: Literal["none", "read", "edit"] = "none"
    config_fields: frozenset[str] = frozenset()
    document_fields: frozenset[str] = frozenset()
    summary_fields: frozenset[str] = frozenset()
    document_actions: frozenset[str] = frozenset()
    downloads: frozenset[str] = frozenset()
    # Each resource action explicitly projects its top-level result fields.
    resource_actions: dict[str, frozenset[str]] = Field(default_factory=dict)
    execution: bool = False
    file_preview: bool = False


class DeploymentAccess(DeploymentSurface):
    """Resolved release permissions, including the section presentation policy."""
    shared_sections: bool = False


class NodeDeploymentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # None forbids publishing the whole card; individual sections can still be published.
    surface: DeploymentSurface | None = None
    sections: dict[str, DeploymentSurface] = Field(default_factory=dict)
    # Whole-card business access shared by its ordinary layout sections.
    # Use explicit sections for distinct permissions within one card.
    shared_sections: bool = False

    @classmethod
    def from_document(cls, *, read_only: bool = False):
        """Publish a document-backed view without repeating its API schema.

        The author opts in to full document/action responses and exporters.
        Config, resource handlers, execution and cross-card operations stay private.
        Use explicit surfaces instead when only part of the document is public.
        """
        return cls(surface=DeploymentSurface(document="read" if read_only else "edit"), shared_sections=True)

    def validate_node(self, node):
        if self.shared_sections and (self.surface is None or self.sections):
            raise ValueError("Shared sections need a whole-card surface and no per-section overrides")
        if not node.frontend.get("workspace") and not node.frontend.get("body"):
            raise ValueError("Deployable plugin nodes need an existing body or workspace view")
        for name in self.sections:
            if not name or len(name) > 100:
                raise ValueError("Deployment sections need nonempty names of at most 100 characters")
        for surface in ([self.surface] if self.surface is not None else []) + list(self.sections.values()):
            if not surface.config_fields <= node.config_model.model_fields.keys():
                raise ValueError("Unknown public config field")
            if surface.document != "none" or surface.document_fields or surface.document_actions or surface.downloads or surface.summary_fields:
                if node.document is None:
                    raise ValueError("Public document operations require a node document")
                if not surface.document_fields <= node.document.model.model_fields.keys():
                    raise ValueError("Unknown public document field")
                if not surface.document_actions <= node.document.actions.keys():
                    raise ValueError("Unknown public document action")
                if not surface.downloads <= node.document.downloads.keys():
                    raise ValueError("Unknown public document download")
            if not surface.resource_actions.keys() <= node.resource_actions.keys():
                raise ValueError("Unknown public resource action")
            if surface.execution and node.execution is None:
                raise ValueError("Public execution requires a node execution handler")
            if surface.file_preview and "core.file-viewer" not in node.traits:
                raise ValueError("Public file preview requires a file viewer")


def resolve_surface(surface, node):
    """Freeze shorthand against this version of the registered business API."""
    if surface.document == "none":
        return surface
    document = node.document
    if document is None:
        raise ValueError("Public document operations require a node document")
    return surface.model_copy(update={
        "document_fields": surface.document_fields | frozenset(document.model.model_fields),
        "document_actions": surface.document_actions | {
            name for name, action in document.actions.items()
            if surface.document == "edit" or action.read_only
        },
        "downloads": surface.downloads | frozenset(document.downloads),
    })


def merged_surfaces(surfaces):
    result = DeploymentSurface().model_dump(mode="json")
    for surface in surfaces:
        value = surface.model_dump(mode="json")
        for key in ("config_fields", "document_fields", "summary_fields", "document_actions", "downloads"):
            result[key] = sorted(set(result[key]) | set(value[key]))
        for action, fields in value["resource_actions"].items():
            result["resource_actions"][action] = sorted(set(result["resource_actions"].get(action, [])) | set(fields))
        result["execution"] |= value["execution"]
        result["file_preview"] |= value["file_preview"]
        if value["document"] == "edit" or result["document"] == "none":
            result["document"] = value["document"]
    return result


def project_document(snapshot, access):
    if access.get("document", "none") != "none":
        # Read actions may return a search result or detail projection rather
        # than the persisted document shape. Preserve their original contract.
        return {key: snapshot[key] for key in ("revision", "value", "summary") if key in snapshot}
    return {"revision": snapshot["revision"],
            "value": {key: value for key, value in snapshot["value"].items() if key in access["document_fields"]},
            "summary": {key: value for key, value in snapshot.get("summary", {}).items() if key in access["summary_fields"]}}
