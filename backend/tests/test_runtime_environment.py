"""Host process policy stays provider-neutral and preserves personal defaults."""
from dataclasses import replace
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.agents.environment import subprocess_environment
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.hosting.worker import create_worker_app, create_worker_services
from backend.tests.test_hosted_worker import config


def test_minimal_environment_excludes_unknown_credentials_and_import_overrides():
    source = {"Path": "process-bin", "SYSTEMROOT": "system", "LANG": "C.UTF-8",
              "ALIYUN_SECRET": "secret", "FUTURE_PROVIDER_TOKEN": "future",
              "PYTHONPATH": "injected-imports", "HTTPS_PROXY": "proxy"}
    assert subprocess_environment(inherit=False, source=source) == {
        "Path": "process-bin", "SYSTEMROOT": "system", "LANG": "C.UTF-8"}
    assert subprocess_environment(inherit=True, source=source) == source
    result = subprocess_environment(inherit=True, source=source)
    result["ALIYUN_SECRET"] = "changed"
    assert source["ALIYUN_SECRET"] == "secret"


def test_provider_uses_operator_policy_and_returns_fresh_personal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_EXTENSION", "keep")
    settings = Settings.for_data_root(tmp_path)
    provider = WorldAgentCapabilityProvider(SimpleNamespace(settings=settings))
    environment = provider.subprocess_environment()
    assert environment["PERSONAL_EXTENSION"] == "keep"
    environment["PERSONAL_EXTENSION"] = "changed"
    assert os.environ["PERSONAL_EXTENSION"] == "keep"
    hosted = WorldAgentCapabilityProvider(SimpleNamespace(
        settings=replace(settings, inherit_runtime_environment=False)))
    assert "PERSONAL_EXTENSION" not in hosted.subprocess_environment()


def test_generic_hosted_worker_selects_minimal_environment_without_cloud_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURE_PROVIDER_TOKEN", "control-only")
    seen = []

    def factory(settings):
        services = create_worker_services(settings)
        seen.append(WorldAgentCapabilityProvider(services).subprocess_environment())
        return services

    with TestClient(create_worker_app(config(tmp_path), services_factory=factory)) as client:
        assert client.get("/health/ready").status_code == 200
    assert len(seen) == 1 and "FUTURE_PROVIDER_TOKEN" not in seen[0]
