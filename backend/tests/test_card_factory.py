"""The visual factory crosses a clean-profile install without executable files."""
import json
import base64
import struct
import zlib

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.packs.archive import inspect_archive
from backend.packs.content import export_archive
from backend.packs.factory_models import FaceDesign, FunctionDesign
from backend.packs.face_design import FaceStudio

HEADERS = {"X-OAW-Pack-Install": "1"}
ARCHIVE_HEADERS = {**HEADERS, "Content-Type": "application/vnd.oaw.pack"}


def node(client, type_, **kwargs):
    response = client.post("/api/nodes", json={"type": type_, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def call(client, device, action, body=None, expected=200):
    response = client.post(f'/api/packs/factory/{device["id"]}/{action}', headers=HEADERS, json=body)
    assert response.status_code == expected, response.text
    return response


def factory(client):
    devices = {kind: node(client, f"oaw.factory.{kind}") for kind in ("pack", "face", "function", "printer", "packer")}
    for kind, target in [("pack", "packer"), ("face", "printer"), ("function", "printer")]:
        result = client.post("/api/edges", json={"source": devices[kind]["id"], "target": devices[target]["id"],
            "relationship": f"oaw.factory.{kind}-input", "direction": "forward"})
        assert result.status_code == 201, result.text
    return devices


def patch(client, card, config):
    response = client.patch(f'/api/nodes/{card["id"]}', json={"config": config})
    assert response.status_code == 200, response.text
    return response.json()


def test_print_export_install_and_run_in_clean_profile(client, tmp_path):
    devices = factory(client)
    printed = call(client, devices["printer"], "print", expected=201).json()
    assert printed["name"] == "我的卡牌"
    assert call(client, printed, "run", {"values": {"name": "OAW"}}).json() == {"result": "你好，OAW！"}
    patch(client, devices["face"], {**devices["face"]["config"], "title": "第二张卡", "tone": "rose", "layout": "columns"})
    second = call(client, devices["printer"], "print", expected=201).json()
    assert second["config"]["face"]["title"] == "第二张卡"
    assert client.get(f'/api/nodes/{printed["id"]}').json()["config"]["face"]["title"] == "我的卡牌"
    packer = devices["packer"]
    for card in (printed, second):
        call(client, packer, "items", {"kind": "node", "id": card["id"]})
    # Duplicate drops are idempotent; source records stay intact.
    config = call(client, packer, "items", {"kind": "node", "id": printed["id"]}).json()["config"]
    assert len(config["items"]) == 2
    patch(client, packer, {**config, "params": {"name": "朋友"}})
    review = call(client, packer, "inspect").json()
    assert review["can_export"], review
    assert len(review["entries"]) == 2
    artifact = call(client, packer, "export").content
    pack = inspect_archive(artifact)
    assert pack.manifest.kind == "content"
    assert len(pack.manifest.content.cards) == 2
    assert not pack.manifest.dependencies.packs
    assert pack.backend_entry is None
    assert call(client, printed, "run", {"values": {}}).json()["result"] == "你好，世界！"

    settings = Settings.for_data_root(tmp_path / "recipient")
    with TestClient(create_app(settings)) as recipient:
        response = recipient.post("/api/packs/install", content=artifact, headers=ARCHIVE_HEADERS)
        assert response.status_code == 201, response.text
    with TestClient(create_app(settings)) as recipient:
        library = recipient.get("/api/card-library").json()
        definitions = library["packs"]["local.mycards"]["definition"]["cards"]
        assert len(definitions) == 2
        opened = recipient.post("/api/card-library/actions", json={"action": "open_pack", "id": "local.mycards", "expected_revision": library["revision"]})
        assert opened.status_code == 200, opened.text
        for type_ in definitions:
            card = node(recipient, type_)
            assert call(recipient, card, "run", {"values": {}}).json()["result"] == "你好，朋友！"
        assert client.get(f'/api/nodes/{printed["id"]}').status_code == 200


def test_mixed_cards_saved_and_live_legions_preserve_portable_state(client, tmp_path):
    devices = factory(client)
    agent = node(client, "agent", name="Writer")
    text = node(client, "text", name="Instructions", content="share intentionally")
    saved = client.post("/api/legions", json={"name": "My Legion", "node_ids": [agent["id"], text["id"]]}).json()
    live = client.post("/api/legions/presets/assistant/instances", json={"position": {"x": 500, "y": 500}}).json()
    group = next(card for card in live["nodes"] if card["type"] == "legion")
    for kind, id_ in [("legion", saved["id"]), ("node", group["id"]), ("node", text["id"])]:
        packer = call(client, devices["packer"], "items", {"kind": kind, "id": id_}).json()
    artifact = inspect_archive(call(client, packer, "export").content)
    assert len(artifact.manifest.content.legions) == 3
    assert b"share intentionally" not in b"".join(artifact.files.values())
    patch(client, packer, {**packer["config"], "include_content": True})
    artifact = inspect_archive(call(client, packer, "export").content)
    assert b"share intentionally" in b"".join(artifact.files.values())
    # A recipient can actually instantiate the singleton and each formation.
    settings = Settings.for_data_root(tmp_path / "mixed-recipient")
    data = call(client, packer, "export").content
    with TestClient(create_app(settings)) as recipient:
        assert recipient.post("/api/packs/install", content=data, headers=ARCHIVE_HEADERS).status_code == 201
    with TestClient(create_app(settings)) as recipient:
        counts = []
        for path in artifact.manifest.content.legions:
            preset = json.loads(artifact.files[path])
            response = recipient.post(f'/api/legions/presets/{preset["id"]}/instances', json={"position": {"x": 0, "y": 0}})
            assert response.status_code == 201, response.text
            counts.append(len(response.json()["nodes"]))
        assert sorted(counts) == [1, 2, 3]


def test_invalid_connections_inputs_and_parameter_types_are_rejected(client):
    printer = node(client, "oaw.factory.printer")
    assert client.post(f'/api/packs/factory/{printer["id"]}/print').status_code == 403
    assert client.post(f'/api/packs/factory/{printer["id"]}/print', headers=HEADERS).status_code == 422
    devices = factory(client)
    duplicate = node(client, "oaw.factory.face")
    client.post("/api/edges", json={"source": duplicate["id"], "target": devices["printer"]["id"], "relationship": "oaw.factory.face-input"})
    assert client.post(f'/api/packs/factory/{devices["printer"]["id"]}/print', headers=HEADERS).status_code == 422
    assert client.post(f'/api/packs/factory/{devices["packer"]["id"]}/items', headers=HEADERS,
        json={"kind": "node", "id": devices["face"]["id"]}).status_code == 422


@pytest.mark.parametrize("operation,expected", [("sum", 7), ("multiply", 12), ("join", "3,4"), ("template", "3 + 4")])
def test_function_modes_and_strict_values(operation, expected):
    design = FunctionDesign(fields=[{"key": "a", "label": "A", "type": "number", "default": 3},
        {"key": "b", "label": "B", "type": "number", "default": 4}], operation=operation, template="{{a}} + {{b}}", separator=",")
    assert design.run({}) == expected
    with pytest.raises(ValueError):
        design.run({"a": "not a number"})
    with pytest.raises(ValueError):
        design.run({"a": True})
    with pytest.raises(ValueError):
        design.run({"missing": "x"})


def test_declarative_content_cannot_inject_code_or_unowned_ids(client):
    devices = factory(client)
    printed = call(client, devices["printer"], "print", expected=201).json()
    call(client, devices["packer"], "items", {"kind": "node", "id": printed["id"]})
    archive = inspect_archive(call(client, devices["packer"], "export").content)
    files = {path: value for path, value in archive.files.items() if path != "checksums.json"}
    path = archive.manifest.content.cards[0]
    recipe = json.loads(files[path])
    recipe["design"]["function"]["operation"] = "eval"
    files[path] = json.dumps(recipe).encode()
    with pytest.raises(ValueError):
        export_archive(files)
    recipe["design"]["function"]["operation"] = "template"
    recipe["id"] = "other.owner.card"
    files[path] = json.dumps(recipe).encode()
    with pytest.raises(ValueError, match="namespace"):
        export_archive(files)


def test_parameter_types_secrets_and_deleted_sources_are_checked(client):
    devices = factory(client)
    printed = call(client, devices["printer"], "print", expected=201).json()
    packer = call(client, devices["packer"], "items", {"kind": "node", "id": printed["id"]}).json()
    patch(client, packer, {**packer["config"], "params": {"name": 42}})
    assert client.post(f'/api/packs/factory/{packer["id"]}/inspect', headers=HEADERS).status_code == 422
    patch(client, packer, {**packer["config"], "params": {"api_key": "private-value"}})
    inspection = call(client, packer, "inspect").json()
    assert not inspection["can_export"]
    assert "private-value" not in json.dumps(inspection)
    assert client.post(f'/api/packs/factory/{packer["id"]}/export', headers=HEADERS).status_code == 422
    patch(client, packer, {**packer["config"], "params": {}})
    client.delete(f'/api/nodes/{printed["id"]}')
    assert client.post(f'/api/packs/factory/{packer["id"]}/export', headers=HEADERS).status_code == 404


def test_workshop_preset_is_ready_and_capture_clears_external_basket_references(client):
    response = client.post('/api/legions/presets/oaw.factory.workshop/instances', json={"position": {"x": 0, "y": 0}})
    assert response.status_code == 201, response.text
    instance = response.json()
    assert len(instance["nodes"]) == 5
    assert len(instance["edges"]) == 3
    printer = next(card for card in instance["nodes"] if card["type"] == "oaw.factory.printer")
    packer = next(card for card in instance["nodes"] if card["type"] == "oaw.factory.packer")
    printed = call(client, printer, 'print', expected=201).json()
    call(client, packer, 'items', {"kind": "node", "id": printed["id"]})
    saved = client.post('/api/legions', json={"name": "My factory", "node_ids": [card["id"] for card in instance["nodes"]]})
    assert saved.status_code == 201, saved.text
    restored = client.post(f'/api/legions/{saved.json()["id"]}/instances', json={"position": {"x": 2000, "y": 0}})
    assert restored.status_code == 201, restored.text
    copied = next(card for card in restored.json()["nodes"] if card["type"] == "oaw.factory.packer")
    assert copied["config"]["items"] == []


def transparent_png():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 6, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(b'\0\xff\x00\x00\xff\x00\x00\x00\x00')) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def custom_studio():
    surface = {"width": 360, "height": 240, "background_png": transparent_png(), "image_shape": True,
        "shapes": [{"id": "oval", "kind": "ellipse", "x": 0, "y": 0, "width": 360, "height": 240},
            {"id": "poly", "kind": "polygon", "x": 10, "y": 20, "width": 100, "height": 100,
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": .5, "y": 1}]}],
        "elements": [{"id": "title", "kind": "title", "x": 80, "y": 16, "width": 180, "height": 40}]}
    return FaceStudio(enabled=['preview', 'workspace'], initial='preview', open='workspace',
        modes={'preview': surface, 'workspace': {**surface, 'width': 740, 'height': 460}}).model_dump()


def test_custom_face_survives_print_capture_export_and_clean_install(client, tmp_path):
    devices = factory(client)
    studio = custom_studio()
    patch(client, devices['face'], {**devices['face']['config'], 'studio': studio})
    printed = call(client, devices['printer'], 'print', expected=201).json()
    assert printed['config']['face']['studio'] == studio
    text = node(client, 'text')
    saved = client.post('/api/legions', json={'name': 'Custom cards', 'node_ids': [printed['id'], text['id']]}).json()
    restored = client.post(f'/api/legions/{saved["id"]}/instances', json={'position': {'x': 1000, 'y': 0}})
    assert restored.status_code == 201, restored.text
    copied = next(card for card in restored.json()['nodes'] if card['type'] == 'oaw.factory.card')
    assert copied['config']['face']['studio'] == studio
    call(client, devices['packer'], 'items', {'kind': 'node', 'id': printed['id']})
    artifact = call(client, devices['packer'], 'export').content
    archive = inspect_archive(artifact)
    recipe = json.loads(archive.files[archive.manifest.content.cards[0]])
    assert recipe['design']['face']['studio'] == studio
    settings = Settings.for_data_root(tmp_path / 'custom-recipient')
    with TestClient(create_app(settings)) as recipient:
        response = recipient.post('/api/packs/install', content=artifact, headers=ARCHIVE_HEADERS)
        assert response.status_code == 201, response.text
    with TestClient(create_app(settings)) as recipient:
        installed = node(recipient, recipe['id'])
        assert installed['config']['face']['studio'] == studio
        assert call(recipient, installed, 'run', {'values': {'name': '形状'}}).json()['result'] == '你好，形状！'
        definition = recipient.app.state.services.plugins.node_type(recipe['id'])
        assert definition.presentation.states == ('preview', 'workspace')
        assert definition.default_size == (360, 240)


def test_legacy_face_serialization_does_not_change_saved_blueprints():
    legacy = FaceDesign().model_dump()
    assert 'studio' not in legacy
    assert FaceDesign.model_validate(legacy).model_dump() == legacy


def test_semantic_recipe_tokens_slots_and_overrides_survive_printing(client):
    devices = factory(client)
    studio = custom_studio()
    surface = studio['modes']['preview']
    surface['design'] = {
        'recipe': 'editorial', 'kit': 'paper', 'appearance': 'light', 'softness': .6,
        'density': 'medium', 'emphasis': 'title', 'alignment': 'left',
        'material': {'type': 'holo', 'intensity': .35, 'mask': 'visual', 'roughness': .3},
        'tokens': {'background': '#f6f5ef', 'gap': 12},
    }
    surface['elements'].append({'id': 'subtitle', 'kind': 'subtitle', 'text': 'A portable card',
        'x': 20, 'y': 70, 'width': 200, 'height': 24, 'placement': 'free', 'sizing': 'hug',
        'pin': 'center', 'overrides': {'font_size': 15}})
    surface['elements'].append({'id': 'illustration', 'kind': 'illustration', 'image_png': transparent_png(),
        'x': 20, 'y': 100, 'width': 120, 'height': 80})
    patch(client, devices['face'], {**devices['face']['config'], 'studio': studio})
    printed = call(client, devices['printer'], 'print', expected=201).json()
    actual = printed['config']['face']['studio']['modes']['preview']
    assert actual['design']['tokens'] == {'background': '#f6f5ef', 'gap': 12}
    assert actual['design']['material']['type'] == 'holo'
    assert actual['elements'][1]['overrides'] == {'font_size': 15}
    assert actual['elements'][1]['placement'] == 'free'
    assert actual['elements'][2]['image_png'] == transparent_png()
    assert FaceStudio.model_validate(printed['config']['face']['studio']).model_dump() == printed['config']['face']['studio']


@pytest.mark.parametrize('change', [
    lambda s: s.update(enabled=[]),
    lambda s: s.update(enabled=['preview', 'preview']),
    lambda s: s.update(initial='node'),
    lambda s: s.update(version=2),
    lambda s: s['modes'].pop('workspace'),
    lambda s: s['modes']['preview'].update(width=4096),
    lambda s: s['modes']['preview'].update(background_png='https://example.com/image.png'),
    lambda s: s['modes']['preview'].update(background_png='data:image/svg+xml;base64,PHN2Zy8+'),
    lambda s: s['modes']['preview'].update(background_png='data:image/png;base64,eA=='),
    lambda s: s['modes']['preview']['elements'][0].update(x=float('nan')),
    lambda s: s['modes']['preview']['elements'][0].update(color='url(https://example.com)'),
    lambda s: s['modes']['preview']['elements'].append(s['modes']['preview']['elements'][0]),
    lambda s: s['modes']['preview']['shapes'][1].update(points=[]),
    lambda s: s['modes']['preview']['shapes'][1]['points'][0].update(x=2),
])
def test_custom_face_rejects_invalid_or_executable_drawing_data(change):
    studio = custom_studio()
    change(studio)
    with pytest.raises(ValueError):
        FaceStudio.model_validate(studio)
