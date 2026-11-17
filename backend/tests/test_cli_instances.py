"""CLI discovery is read-only and registrations follow the server lifetime."""
import json
import os

import pytest

from open_agent_world import instances


def test_registration_lifetime_and_read_only_discovery(tmp_path, monkeypatch):
    directory = tmp_path / "instances"
    monkeypatch.setenv("OPEN_AGENT_WORLD_INSTANCE_DIR", str(directory))
    assert instances.list_instances() == []
    assert not directory.exists()
    with instances.registered_instance(url="http://127.0.0.1:38473", mode="development",
                                       profile="demo", version="0.7.1", data_root=tmp_path / "data") as record:
        assert instances.list_instances() == [record]
        assert record["pid"] == os.getpid()
    assert instances.list_instances() == []
    assert list(directory.iterdir()) == []


def test_discovery_ignores_dead_corrupt_and_non_local_records(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_AGENT_WORLD_INSTANCE_DIR", str(tmp_path))
    monkeypatch.setattr(instances, "_process_exists", lambda pid: pid == 5)
    record = {"id": "live", "pid": 5, "url": "http://127.0.0.1:38473", "mode": "production",
              "profile": None, "version": "0.7.1", "data_root": str(tmp_path / "data")}
    for identity, changes in (("live", {}), ("dead", {"pid": 6}),
                              ("remote", {"url": "http://example.com:38473"}),
                              ("bad-port", {"url": "http://127.0.0.1:no"}),
                              ("bad-id", {"id": "different"}),
                              ("bad-pid", {"pid": True})):
        (tmp_path / f"{identity}.json").write_text(json.dumps({**record, "id": identity, **changes}))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "array.json").write_text("[]")
    assert instances.list_instances() == [record]


def test_registration_cleans_up_after_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_AGENT_WORLD_INSTANCE_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="stopped"):
        with instances.registered_instance(url="http://127.0.0.1:38473", mode="production", profile=None,
                                           version="0.7.1", data_root=tmp_path):
            raise RuntimeError("stopped")
    assert list(tmp_path.iterdir()) == []
