"""Read-only, node-scoped credentials for trusted resource handlers."""
import os
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
        variables = SandboxSettingsStore(services.database, services.settings.data_root).read().environment_variables
        return next((value for key, value in variables.items()
                     if key.casefold() == reference.casefold() and value), None) or os.environ.get(reference)

    return resolve
