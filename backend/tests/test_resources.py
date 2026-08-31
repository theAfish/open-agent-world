from __future__ import annotations

import base64
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.errors import UnsafePathError
from backend.tests.conftest import create_node


def test_text_replace_patch_history_and_revision_conflict(client: TestClient) -> None:
    text = create_node(
        client,
        "text",
        config={"filename": "experiment.txt"},
        content="alpha beta",
    )
    assert text["resource"]["revision"] == 1
    replaced = client.put(
        f"/api/resources/{text['id']}/text",
        json={"content": "alpha gamma", "expected_revision": 1},
    )
    assert replaced.status_code == 200
    assert replaced.json()["revision"] == 2

    stale = client.put(
        f"/api/resources/{text['id']}/text",
        json={"content": "stale", "expected_revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"

    patched = client.patch(
        f"/api/resources/{text['id']}/text",
        json={"expected_revision": 2, "edits": [{"start": 6, "end": 11, "text": "delta"}]},
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "alpha delta"
    assert patched.json()["revision"] == 3

    history = client.get(f"/api/resources/{text['id']}/history").json()
    assert [entry["operation"] for entry in history] == ["patch", "replace", "create"]
    assert history[0]["new_sha256"] != history[0]["old_sha256"]


def test_managed_paths_reject_traversal_and_unsafe_filenames(client: TestClient) -> None:
    response = client.post(
        "/api/nodes",
        json={
            "type": "text",
            "config": {"filename": "..\\secret.txt"},
            "content": "should never be written",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_path"
    assert client.get("/api/nodes").json() == []

    resources = client.app.state.services.resources
    with pytest.raises(UnsafePathError):
        resources.resolve_relative_path("assets/text/../../outside.txt")
    with pytest.raises(UnsafePathError):
        resources.resolve_relative_path("C:/Users/example/secret.txt")


def test_image_is_copied_into_managed_storage_and_deleted_with_card(
    client: TestClient, data_root: Path
) -> None:
    # The manager only needs the PNG signature and IHDR dimensions for metadata;
    # payload bytes remain opaque and are served exactly as imported.
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 3, 2) + b"payload"
    image = create_node(
        client,
        "image",
        config={"filename": "sample.png"},
        media_type="image/png",
        data_base64=base64.b64encode(png).decode("ascii"),
    )
    assert image["resource"]["width"] == 3
    assert image["resource"]["height"] == 2
    record = client.get(f"/api/resources/{image['id']}").json()
    managed_path = data_root / Path(*record["relative_path"].split("/"))
    assert managed_path.is_file()
    assert managed_path.read_bytes() == png
    content = client.get(f"/api/resources/{image['id']}/content")
    assert content.status_code == 200
    assert content.content == png

    assert client.delete(f"/api/nodes/{image['id']}").status_code == 200
    assert not managed_path.exists()


def test_image_can_be_imported_into_an_existing_empty_card(client: TestClient) -> None:
    image = create_node(client, "image", config={"filename": "placeholder.png"})
    assert image["resource"] is None
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 7, 5) + b"pixels"
    imported = client.post(
        f"/api/resources/{image['id']}/image",
        json={
            "filename": "loaded.png",
            "media_type": "image/png",
            "data_base64": base64.b64encode(png).decode("ascii"),
        },
    )
    assert imported.status_code == 201
    assert imported.json()["width"] == 7
    assert client.get(f"/api/nodes/{image['id']}").json()["resource"]["filename"] == "loaded.png"
    duplicate = client.post(
        f"/api/resources/{image['id']}/image",
        json={
            "filename": "again.png",
            "media_type": "image/png",
            "data_base64": base64.b64encode(png).decode("ascii"),
        },
    )
    assert duplicate.status_code == 409


def test_standard_lossy_webp_is_accepted(client: TestClient) -> None:
    image = create_node(client, "image", config={"filename": "placeholder.webp"})
    webp = (
        b"RIFF" + b"\x00" * 4 + b"WEBPVP8 " + b"\x00" * 4
        + b"\x00" * 3 + b"\x9d\x01\x2a" + struct.pack("<HH", 7, 5)
    )
    imported = client.post(
        f"/api/resources/{image['id']}/image",
        json={
            "filename": "loaded.webp",
            "media_type": "image/webp",
            "data_base64": base64.b64encode(webp).decode("ascii"),
        },
    )
    assert imported.status_code == 201
    assert imported.json()["width"] == 7
    assert imported.json()["height"] == 5
