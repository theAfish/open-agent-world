"""Create an isolated, persistent demo world through ordinary host APIs."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path(".open-agent-world/matcreator-demo"))
    args = parser.parse_args()
    settings = replace(Settings.for_data_root(args.data_root.resolve()), agent_runtime="core.mock")
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        def create(kind, name, x, y):
            response = client.post("/api/nodes", json={"type": kind, "name": name, "position": {"x": x, "y": y}})
            response.raise_for_status()
            return response.json()
        agent = create("agent", "Materials Agent", 0, 0)
        graph = create("matcreator.kdg", "Materials Know-Do Graph", 500, 0)
        toolset = create("matcreator.core", "Materials Core", 500, 850)
        sandbox = create("sandbox", "Local materials workspace", -500, 500)
        artifacts = create("core.artifact-collection", "Materials results", 0, 500)
        environment = create("environment", "Local ASE Environment", -500, 850)
        env_doc = client.get(f"/api/nodes/{environment['id']}/document").json()
        client.post(f"/api/nodes/{environment['id']}/actions/replace", json={"expected_revision": env_doc['revision'],
            "arguments": {"variables": {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}}}).raise_for_status()
        for target, relationship in [(graph, "matcreator.kdg.learn"), (toolset, "matcreator.core.use"), (sandbox, "execute"), (environment, "environment.use"), (artifacts, "artifact.publish")]:
            response = client.post("/api/edges", json={"source": agent["id"], "target": target["id"], "relationship": relationship})
            response.raise_for_status()
        print("Demo created at", args.data_root.resolve())
        print("Configure an Agent model and a Sandbox runtime with Python + ASE before Run.")
        print("Task: use Local structure demo to create copper.cif, copper.extxyz and copper.json (repeat 2), then publish them as an Artifact.")
        print("Assimilate Materials Core, then repeat the task with repeat 3 using knowledge_search / knowledge_inspect.")

if __name__ == "__main__":
    main()
