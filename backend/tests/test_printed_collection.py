"""Printing persists a reusable passive type without broadening other cards."""
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.services import ApplicationServices
from backend.tests.test_card_factory import call, factory, node, patch


def test_printed_types_are_collected_reused_and_survive_restart(tmp_path):
    settings = Settings.for_data_root(tmp_path / "profile")
    with TestClient(create_app(settings)) as client:
        devices = factory(client)
        first = call(client, devices["printer"], "print", expected=201).json()
        again = call(client, devices["printer"], "print", expected=201).json()
        assert first["type"].startswith("local.printed.")
        assert first["id"] != again["id"] and first["type"] == again["type"]
        library = client.get("/api/card-library").json()
        assert library["collection"][first["type"]]["unlocked"]
        placed = client.post("/api/card-library/nodes", json={"type": first["type"]})
        assert placed.status_code == 201, placed.text
        patch(client, devices["face"], {**devices["face"]["config"], "title": "A new design"})
        changed = call(client, devices["printer"], "print", expected=201).json()
        assert changed["type"] != first["type"]

    with TestClient(create_app(settings)) as client:
        restored = client.get(f'/api/nodes/{first["id"]}').json()
        assert restored["type"] == first["type"]
        assert restored["config"] == first["config"]
        assert client.get("/api/card-library").json()["collection"][first["type"]]["unlocked"]
        assert node(client, first["type"])["config"] == first["config"]
        assert node(client, changed["type"])["config"]["face"]["title"] == "A new design"


def test_printed_association_is_specific_to_its_design(client):
    devices = factory(client)
    plain = call(client, devices["printer"], "print", expected=201).json()
    patch(client, devices["function"], {**devices["function"]["config"],
        "associations": [{"id": "notes", "label": "Related notes", "target_type": "text"}]})
    linked = call(client, devices["printer"], "print", expected=201).json()
    target = node(client, "text")
    catalog = client.get("/api/catalog").json()
    relationship = next(item for item in catalog["relationships"] if item["source_types"] == [linked["type"]])
    edge = {"source": linked["id"], "target": target["id"], "relationship": relationship["id"], "direction": "forward"}
    response = client.post("/api/edges", json=edge)
    assert response.status_code == 201, response.text
    assert not relationship["data_read"]
    assert client.post("/api/edges", json={**edge, "source": plain["id"]}).status_code == 422
    wrong_target = node(client, "agent")
    assert client.post("/api/edges", json={**edge, "target": wrong_target["id"]}).status_code == 422


def test_printing_does_not_reenable_a_disabled_local_pack(client):
    devices = factory(client)
    printed = call(client, devices["printer"], "print", expected=201).json()
    owner = printed["type"].removesuffix(".card")
    assert client.delete(f'/api/nodes/{printed["id"]}').status_code == 200
    library = client.get("/api/card-library").json()
    response = client.post("/api/card-library/actions", json={"action": "set_plugin_enabled",
        "id": owner, "enabled": False, "expected_revision": library["revision"]})
    assert response.status_code == 200, response.text
    response = client.post(f'/api/packs/factory/{devices["printer"]["id"]}/print', headers={"X-OAW-Pack-Install": "1"})
    assert response.status_code == 422, response.text
    assert owner not in client.get("/api/catalog").json()["enabled_plugin_ids"]


def test_print_retries_activation_after_successful_install(client, monkeypatch):
    devices = factory(client)
    original = ApplicationServices.activate_passive_packs
    interrupted = False

    async def activate(services):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise ValueError("Interrupted before Library activation")
        await original(services)

    monkeypatch.setattr(ApplicationServices, "activate_passive_packs", activate)
    response = call(client, devices["printer"], "print", expected=422)
    assert "Interrupted before Library activation" in response.text
    printed = call(client, devices["printer"], "print", expected=201).json()
    assert client.get("/api/card-library").json()["collection"][printed["type"]]["unlocked"]
    installed = client.get("/api/packs").json()["versions"]
    assert len([item for item in installed if item["id"].startswith("local.printed.")]) == 1
