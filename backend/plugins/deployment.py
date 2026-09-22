"""Opt-in public surfaces for existing plugin views and business handlers."""
from pydantic import BaseModel, ConfigDict, Field


class DeploymentSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    config_fields: frozenset[str] = frozenset()
    document_fields: frozenset[str] = frozenset()
    summary_fields: frozenset[str] = frozenset()
    document_actions: frozenset[str] = frozenset()
    downloads: frozenset[str] = frozenset()
    # Each resource action explicitly projects its top-level result fields.
    resource_actions: dict[str, frozenset[str]] = Field(default_factory=dict)
    execution: bool = False


class NodeDeploymentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # None forbids publishing the whole card; individual sections can still be published.
    surface: DeploymentSurface | None = None
    sections: dict[str, DeploymentSurface] = Field(default_factory=dict)

    def validate_node(self, node):
        if not node.frontend.get("workspace") and not node.frontend.get("body"):
            raise ValueError("Deployable plugin nodes need an existing body or workspace view")
        for name in self.sections:
            if not name or len(name) > 100:
                raise ValueError("Deployment sections need nonempty names of at most 100 characters")
        for surface in ([self.surface] if self.surface is not None else []) + list(self.sections.values()):
            if not surface.config_fields <= node.config_model.model_fields.keys():
                raise ValueError("Unknown public config field")
            if surface.document_fields or surface.document_actions or surface.downloads or surface.summary_fields:
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


def merged_surfaces(surfaces):
    result = DeploymentSurface().model_dump(mode="json")
    for surface in surfaces:
        value = surface.model_dump(mode="json")
        for key in ("config_fields", "document_fields", "summary_fields", "document_actions", "downloads"):
            result[key] = sorted(set(result[key]) | set(value[key]))
        for action, fields in value["resource_actions"].items():
            result["resource_actions"][action] = sorted(set(result["resource_actions"].get(action, [])) | set(fields))
        result["execution"] |= value["execution"]
    return result


def project_document(snapshot, access):
    return {"revision": snapshot["revision"],
            "value": {key: value for key, value in snapshot["value"].items() if key in access["document_fields"]},
            "summary": {key: value for key, value in snapshot.get("summary", {}).items() if key in access["summary_fields"]}}
