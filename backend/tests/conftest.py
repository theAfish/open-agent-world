from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture(autouse=True)
def desktop_test_client_peer(monkeypatch):
    """API tests model the local desktop; security tests set an explicit peer."""
    original_init = TestClient.__init__

    def initialize(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", initialize)


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "managed"


@pytest.fixture
def client(data_root: Path) -> Iterator[TestClient]:
    application = create_app(Settings.for_data_root(data_root))
    with TestClient(application) as test_client:
        yield test_client


def create_node(client: TestClient, card_type: str, **overrides: object) -> dict:
    payload: dict[str, object] = {"type": card_type}
    payload.update(overrides)
    response = client.post("/api/nodes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
