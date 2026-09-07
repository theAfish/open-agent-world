from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_services
from backend.api.plugin_assets import router
from backend.plugins.builtin import create_builtin_registry
from open_agent_world.plugin_api import PluginAsset, PluginDefinition, PluginDescriptor


def plugin(identifier, configure):
    return PluginDefinition(PluginDescriptor(id=identifier, version="1.0", plugin_api_version="1.9"), configure)


def test_asset_names_are_local_and_endpoint_only_serves_registered_bytes():
    registry = create_builtin_registry()
    for owner, content in [("example.one", b"<svg/>"), ("example.two", b"<svg><path/></svg>")]:
        registry.install(plugin(owner, lambda r, content=content: r.register_asset(PluginAsset("logo", content, "image/svg+xml"))))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(plugins=registry)
    with TestClient(app) as client:
        first = client.get("/api/plugins/example.one/assets/logo")
        assert first.content == b"<svg/>"
        assert first.headers["content-type"] == "image/svg+xml"
        assert "sandbox" in first.headers["content-security-policy"]
        assert client.get("/api/plugins/example.two/assets/logo").content != first.content
        for path in ["missing/assets/logo", "example.one/assets/missing", "example.one/assets/%2e%2e%2fsecret"]:
            assert client.get(f"/api/plugins/{path}").status_code == 404


@pytest.mark.parametrize("changes", [
    {"icon_asset": "unregistered"}, {"frontend": {"unknown": "view"}}, {"frontend": {"body": "../view"}},
])
def test_invalid_view_or_asset_reference_does_not_partially_install(changes):
    registry = create_builtin_registry()
    def configure(registration):
        registration.register_asset(PluginAsset("logo", b"<svg/>", "image/svg+xml"))
        registration.register_node_type(replace(registry.node_type("text"), id="example.card", **changes))
    with pytest.raises(ValueError):
        registry.install(plugin("example.invalid", configure))
    assert not registry.has_plugin("example.invalid")
    with pytest.raises(KeyError):
        registry.asset("example.invalid", "logo")


def test_catalog_carries_owned_resource_and_frontend_references():
    registry = create_builtin_registry()
    def configure(registration):
        registration.register_asset(PluginAsset("logo", b"<svg/>", "image/svg+xml"))
        registration.register_node_type(replace(registry.node_type("text"), id="example.card", icon_asset="logo", frontend={"body": "editor"}))
    registry.install(plugin("example.views", configure))
    card = next(c for c in registry.catalog().node_types if c.id == "example.card")
    assert card.icon_url == "/api/plugins/example.views/assets/logo"
    assert card.frontend == {"body": "editor"}
