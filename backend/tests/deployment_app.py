"""Isolated real runtime server for the deployment browser acceptance suite."""
from pathlib import Path
from dataclasses import replace
import tempfile
import os

import uvicorn
from fastapi.testclient import TestClient
from oaw_xrd.preset import PRESET_ID
from fastapi.testclient import TestClient
from oaw_xrd.preset import PRESET_ID

from backend.deploy import create_deployment
from backend.main import create_app
from backend.tests.test_deployments import PASSWORD, prepare_multiple_workspaces, settings


def main():
    repository = Path(__file__).resolve().parents[2]
    temporary = repository / ".tmp"
    temporary.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="deployment-browser-", dir=temporary))
    source, runtime = directory / "source", directory / "runtime"
    plugin_directories = (repository / "examples/deployed-workspace/plugins",)
    records = prepare_multiple_workspaces(source, plugin_directories=plugin_directories)
    with TestClient(create_app(replace(settings(source), plugin_directories=plugin_directories)), client=("127.0.0.1", 50000)) as client:
        graph = client.post(f"/api/legions/presets/{PRESET_ID}/instances", json={}).json()
        xrd = next(card for card in graph["nodes"] if card["type"] == "legion")
        release = client.post("/api/deployments", json={"legion_ids": [records["operations"]["id"], records["legion"]["id"], xrd["id"]], "name": "Company portal"})
        assert release.status_code == 201, release.text
        records["release"] = release.json()
    with TestClient(create_app(replace(settings(source), plugin_directories=plugin_directories)), client=("127.0.0.1", 50000)) as client:
        graph = client.post(f"/api/legions/presets/{PRESET_ID}/instances", json={}).json()
        xrd = next(card for card in graph["nodes"] if card["type"] == "legion")
        release = client.post("/api/deployments", json={"legion_ids": [records["operations"]["id"], records["legion"]["id"], xrd["id"]], "name": "Company portal"})
        assert release.status_code == 201, release.text
        records["release"] = release.json()
    create_deployment(source, runtime, records["release"]["id"], password=PASSWORD)
    app = create_app(replace(settings(runtime), plugin_directories=plugin_directories),
                     frontend_directory=Path(os.environ.get("OAW_TEST_FRONTEND_DIR", repository / "frontend/dist")))
    uvicorn.run(app, host="127.0.0.1", port=5183, proxy_headers=False)


if __name__ == "__main__":
    main()
