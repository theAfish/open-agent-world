"""Read-only, node-scoped credentials and model connections for trusted resource handlers."""
import os
from backend.execution_config import SecretRequirement
from backend.sandbox.settings import SandboxSettingsStore


def resource_secret_resolver(services, node):
    node_id, created_at = node.id, node.created_at

    def resolve(reference: str) -> str | None:
        # A queued job must not inherit credentials from a replacement node.
        current = services.world.get_card(node_id)
        if current.created_at != created_at:
            raise ValueError("The resource that owned this credential no longer exists")
        credentials = services.execution_credentials
        if credentials.configured(node_id, reference):
            return credentials.resolve(node_id, reference)
        settings = SandboxSettingsStore(services.database, services.settings.data_root)
        for key, value in settings.read().environment_variables.items():
            if key.casefold() != reference.casefold():
                continue
            if isinstance(value, SecretRequirement):
                return settings.credentials.resolve(None, value.secret_ref)
            if isinstance(value, str) and value:
                return value
        return os.environ.get(reference)

    return resolve


def resource_model_resolver(services, node):
    """Resolve an OAW model reference for a plugin's own model call.

    The same ownership guard as :func:`resource_secret_resolver`: a queued job must
    not pick up a connection on behalf of a node that has since been replaced.
    ``ModelConnectionStore.resolve_runtime`` already understands ``oaw:default``
    and raises ``ResourceValidationError`` when no usable connection exists.
    """
    from backend.security.model_connections import ModelConnectionStore

    node_id, created_at = node.id, node.created_at

    def resolve(reference: str):
        current = services.world.get_card(node_id)
        if current.created_at != created_at:
            raise ValueError("The resource that owned this model connection no longer exists")
        return ModelConnectionStore(services.llm_settings).resolve_runtime(reference or "oaw:default")

    return resolve
