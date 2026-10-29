"""Common request and credential boundary for a published application."""
from fastapi import HTTPException
from contextlib import contextmanager
from contextvars import ContextVar


_business_mutation = ContextVar("publication_business_mutation", default=False)


@contextmanager
def business_mutation():
    """Only scoped server handlers may enable application-owned output changes."""
    token = _business_mutation.set(True)
    try:
        yield
    finally:
        _business_mutation.reset(token)


def in_business_mutation():
    return _business_mutation.get()


PRIVATE_FIELDS = frozenset({
    "api_key", "api_key_encrypted", "password", "access_token", "refresh_token",
    "client_secret", "private_key", "authorization", "provider_config",
    "system_instruction", "system_prompt", "secret", "credentials",
})


def public_value(value):
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()
                if key.lower() not in PRIVATE_FIELDS
                and not key.lower().endswith(("_api_key", "_password", "_secret"))}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    return value


class PublicationScope:
    def __init__(self, manifest):
        self.manifest = manifest
        self.node_ids = (frozenset(manifest["permissions"]) | frozenset(manifest.get("plugin_access", {}))
                         | frozenset(manifest.get("runtime_nodes", [])))
        self.roots = frozenset(item["legion_id"] for item in manifest.get("workspaces", []))
        if not self.roots and manifest.get("legion_id"):
            self.roots = frozenset({manifest["legion_id"]})

    def cards(self, services):
        if self.roots and "runtime_nodes" in self.manifest:
            return list({node.id: node for root in self.roots
                         for node in [services.world.get_card(root), *services.world.owned_descendants(root)]}.values())
        return [services.world.get_card(node_id) for node_id in self.manifest["permissions"]]

    def require(self, node_id, services=None):
        if node_id in self.node_ids:
            return
        if services is not None and self.roots and "runtime_nodes" in self.manifest and node_id in {card.id for card in self.cards(services)}:
            return
        if node_id not in self.node_ids:
            raise HTTPException(404, "This node is not part of the published application")

    def validate_references(self, value, services):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key.endswith("_node_id") or key == "executor_id") and item:
                    try:
                        self.require(item, services)
                    except HTTPException as error:
                        raise HTTPException(403, "Referenced node is outside the application") from error
                else:
                    self.validate_references(item, services)
        elif isinstance(value, list):
            for item in value:
                self.validate_references(item, services)
