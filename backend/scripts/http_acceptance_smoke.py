"""Exercise the running application through its public HTTP API.

Start the backend with the mock Agent runtime before invoking this module.  The
script creates an isolated four-card world, validates direct capabilities and a
real Sandbox read/write mount, revokes both permission paths, and removes the
temporary cards it created.
"""

from __future__ import annotations

import base64
import json
import time
from uuid import uuid4

import httpx


BASE_URL = "http://127.0.0.1:8000/api"
PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlY5Z0"
    "AAAAASUVORK5CYII="
)


def require(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text}"
        )
    return response


def main() -> None:
    suffix = uuid4().hex[:10]
    created_cards: list[str] = []
    results: dict[str, str] = {}

    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        require(client.get("/health"), 200)
        try:
            agent = require(
                client.post(
                    "/nodes",
                    json={
                        "type": "agent",
                        "name": f"Acceptance Agent {suffix}",
                        "position": {"x": 0, "y": 0},
                    },
                ),
                201,
            ).json()
            created_cards.append(agent["id"])
            text = require(
                client.post(
                    "/nodes",
                    json={
                        "type": "text",
                        "name": f"Acceptance Notes {suffix}",
                        "position": {"x": 360, "y": 0},
                        "config": {"filename": "notes.txt"},
                        "content": "alpha",
                    },
                ),
                201,
            ).json()
            created_cards.append(text["id"])
            image = require(
                client.post(
                    "/nodes",
                    json={
                        "type": "image",
                        "name": f"Acceptance Image {suffix}",
                        "position": {"x": 360, "y": 280},
                        "config": {"filename": "pixel.png"},
                        "media_type": "image/png",
                        "data_base64": PNG_1X1,
                    },
                ),
                201,
            ).json()
            created_cards.append(image["id"])
            sandbox = require(
                client.post(
                    "/nodes",
                    json={
                        "type": "sandbox",
                        "name": f"Acceptance Lab {suffix}",
                        "position": {"x": 760, "y": 120},
                    },
                ),
                201,
            ).json()
            created_cards.append(sandbox["id"])
            results["four_card_world"] = "passed"

            agent_text_edge = require(
                client.post(
                    "/edges",
                    json={
                        "source": agent["id"],
                        "target": text["id"],
                        "relationship": "read_edit",
                    },
                ),
                201,
            ).json()
            require(
                client.post(
                    "/edges",
                    json={
                        "source": agent["id"],
                        "target": image["id"],
                        "relationship": "view",
                    },
                ),
                201,
            )
            require(
                client.post(
                    "/edges",
                    json={
                        "source": agent["id"],
                        "target": sandbox["id"],
                        "relationship": "execute",
                    },
                ),
                201,
            )
            text_mount_edge = require(
                client.post(
                    "/edges",
                    json={
                        "source": text["id"],
                        "target": sandbox["id"],
                        "relationship": "mount_read_write",
                    },
                ),
                201,
            ).json()
            require(
                client.post(
                    "/edges",
                    json={
                        "source": image["id"],
                        "target": sandbox["id"],
                        "relationship": "mount_read_only",
                    },
                ),
                201,
            )

            document = require(
                client.get(f"/agents/{agent['id']}/resources/{text['id']}/text"),
                200,
            ).json()
            require(
                client.put(
                    f"/agents/{agent['id']}/resources/{text['id']}/text",
                    json={
                        "content": "direct-capability",
                        "expected_revision": document["revision"],
                    },
                ),
                200,
            )
            viewed_image = require(
                client.get(f"/agents/{agent['id']}/resources/{image['id']}/image"),
                200,
            )
            if base64.b64encode(viewed_image.content).decode("ascii") != PNG_1X1:
                raise RuntimeError("direct image capability returned different bytes")
            results["direct_capabilities"] = "passed"

            require(
                client.post(
                    f"/agents/{agent['id']}/run",
                    json={"prompt": "List the scoped tools currently available."},
                ),
                202,
            )
            for _ in range(50):
                status = require(client.get(f"/agents/{agent['id']}"), 200).json()[
                    "status"
                ]
                if status == "idle":
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError("mock Agent run did not return to idle")
            results["agent_runtime"] = "passed"

            require(client.post(f"/sandboxes/{sandbox['id']}/start"), 200)
            mounted_read = require(
                client.post(
                    f"/sandboxes/{sandbox['id']}/execute",
                    json={"command": r"type resources\notes.txt"},
                ),
                200,
            ).json()
            if mounted_read["exit_code"] != 0 or "direct-capability" not in mounted_read[
                "stdout"
            ]:
                raise RuntimeError(f"Sandbox did not read its mounted resource: {mounted_read}")
            mounted_write = require(
                client.post(
                    f"/sandboxes/{sandbox['id']}/execute",
                    json={"command": r"echo sandbox-update>resources\notes.txt"},
                ),
                200,
            ).json()
            if mounted_write["exit_code"] != 0:
                raise RuntimeError(f"Sandbox did not update its writable mount: {mounted_write}")
            refreshed = require(client.get(f"/resources/{text['id']}/text"), 200).json()
            if "sandbox-update" not in refreshed["content"]:
                raise RuntimeError("managed Text resource did not reflect the Sandbox write")
            results["sandbox_read_write"] = "passed"

            require(client.delete(f"/edges/{text_mount_edge['id']}"), 200)
            require(client.post(f"/sandboxes/{sandbox['id']}/start"), 200)
            revoked_mount = require(
                client.post(
                    f"/sandboxes/{sandbox['id']}/execute",
                    json={"command": r"type resources\notes.txt"},
                ),
                200,
            ).json()
            if revoked_mount["exit_code"] == 0 or "sandbox-update" in revoked_mount[
                "stdout"
            ]:
                raise RuntimeError("deleted mount edge did not revoke Sandbox access")

            require(client.delete(f"/edges/{agent_text_edge['id']}"), 200)
            denied = client.get(f"/agents/{agent['id']}/resources/{text['id']}/text")
            require(denied, 403)
            results["permission_revocation"] = "passed"

            snapshot = require(client.get("/world"), 200).json()
            ids = {node["id"] for node in snapshot["nodes"]}
            if not set(created_cards).issubset(ids):
                raise RuntimeError("world snapshot omitted an acceptance card")
            results["world_snapshot"] = "passed"
        finally:
            for card_id in reversed(created_cards):
                response = client.delete(f"/nodes/{card_id}")
                if response.status_code not in {200, 404}:
                    results["cleanup"] = f"failed ({card_id}: {response.status_code})"

    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
