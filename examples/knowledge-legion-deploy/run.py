"""Publish the "Knowledge research" Legion as a locked, served deployment.

Unlike ``examples/deployed-workspace`` this is not a canned, key-free demo: the
Librarian reads a real knowledge base, and turning a document into structured
JSON genuinely calls a model. What this script *does* automate is the one extra
setup step this one plugin needs that the rest of the project does not: the
plugin and its declared dependencies in
``backend/.venv``. It installs that automatically, then builds the release the same
way the Publish application panel does — deploy the preset, upload and convert one
sample document, seed one hand-written projection so Review and the graph have
something to show without a model call, publish, and serve.
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRESET = "knowledge.base.research"
PASSWORD = "oaw-knowledge-2026"
ACTIVE_JOBS = {"QUEUED", "RUNNING", "PENDING", "RETRYING"}

DOCUMENT = """# Sintering of Si3N4 Ceramics

## Method
A Si3N4 powder compact was pressureless-sintered at 1750 C for 2 hours under
flowing nitrogen, using Y2O3 and Al2O3 as sintering aids.

## Results
| Sample | Additive     | Density (g/cm3) |
|--------|--------------|------------------|
| A1     | Y2O3 + Al2O3 | 3.21             |
| A2     | Y2O3 only    | 3.05             |

Sample A1 reached 96% of the theoretical density and showed the finest grain
structure, attributed to the combined liquid-phase sintering effect of the two
additives.
"""
SCHEMA = {
    "type": "object", "required": ["entities"],
    "properties": {
        "entities": {"type": "array", "items": {"type": "object"}},
        "relations": {"type": "array", "items": {"type": "object"}},
    },
}
# A hand-written stand-in for what "Project to JSON" would produce, so Review and
# the graph are not empty on first load even with no model connection configured.
PROJECTION = {
    "entities": [
        {"type": "material", "name": "Si3N4", "form": "powder compact"},
        {"type": "process", "name": "Pressureless sintering", "temperature_c": 1750,
         "atmosphere": "nitrogen"},
        {"type": "sample", "name": "A1", "density_g_cm3": 3.21, "additive": "Y2O3+Al2O3"},
    ],
    "relations": [
        {"source": "Si3N4", "target": "Pressureless sintering", "type": "processed_by"},
        {"source": "A1", "target": "Pressureless sintering", "type": "produced_by"},
    ],
}

INSTALL_HINT = ("See plugins/knowledge_base/README.md#install-the-engines to install "
               "mat-know-base, pymupdf4llm and openpyxl by hand.")


def ensure_knowledge_dependencies(python: Path) -> None:
    """The knowledge base card needs mat-know-base, pymupdf4llm and openpyxl in this
    venv. Normal setup installs all bundled plugins and their dependencies. Skip the
    (slower) sync once they are already importable, so a second run is instant.
    """
    probe = subprocess.run([str(python), "-c", "import mkb, pymupdf4llm, openpyxl"], cwd=ROOT,
                           capture_output=True)
    if probe.returncode == 0:
        return
    print("Installing the knowledge base plugin's engines (mat-know-base, pymupdf4llm, openpyxl)...",
          file=sys.stderr)
    if shutil.which("uv") is None:
        raise SystemExit(f"uv is required to install them automatically. {INSTALL_HINT}")
    result = subprocess.run([str(python), str(ROOT / "scripts/install-plugins.py"),
                             str(ROOT / "plugins/knowledge_base")], cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"Automatic plugin install failed. {INSTALL_HINT}")
    probe = subprocess.run([str(python), "-c", "import mkb, pymupdf4llm, openpyxl"], cwd=ROOT,
                           capture_output=True)
    if probe.returncode != 0:
        raise SystemExit(f"mat-know-base is still not importable after installing. {INSTALL_HINT}")


def prepare(directory: Path) -> Path:
    source, runtime = directory / "engineering", directory / "runtime"
    if (runtime / "deployment.json").is_file():
        return runtime
    if source.exists() or runtime.exists():
        raise ValueError("Incomplete example data exists. Choose a new --data-root; existing data will not be overwritten.")
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.deploy import create_deployment
    from backend.main import create_app

    settings = Settings.for_data_root(source)
    # Exercise the real engineering API in-process; no management listener is exposed.
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        def request(method, path, body=None):
            response = client.request(method, path, json=body if body is not None else {})
            response.raise_for_status()
            return response.json()

        node_ids = request("POST", f"/api/legions/presets/{PRESET}/instances")["node_ids"]
        knowledge_id = node_ids["knowledge"]

        def resource(action, *, confirm=False, **arguments):
            return request("POST", f"/api/nodes/{knowledge_id}/resource/{action}",
                           {"arguments": arguments, "confirm": confirm})

        ingested = resource("ingest", filename="sintering.md", media_type="text/markdown",
                            content_base64=base64.b64encode(DOCUMENT.encode()).decode())
        processed = resource("process", source_ids=[ingested["source"]["id"]])
        job_id = processed["jobs"][0]["job"]["id"]
        deadline = time.monotonic() + 60
        while True:
            job = resource("jobs", job_id=job_id)["job"]
            if job["status"] not in ACTIVE_JOBS:
                break
            if time.monotonic() > deadline:
                raise ValueError("The sample document did not finish converting in time.")
            time.sleep(0.5)
        record_id = resource("sources")["sources"][0]["record_id"]

        schema = resource("schemas", operation="create", name="Process graph", domain="materials",
                          definition=SCHEMA, system_prompt="Extract entities and relations from a materials document.",
                          description="Samples and the processes applied to them")["schema"]
        projection = resource("save_projection", schema_id=schema["id"], record_id=record_id,
                              data=PROJECTION, notes="Seeded sample, not model-produced")["projection"]
        draft = resource("draft", operation="create", projection_ids=[projection["id"]])["draft"]
        resource("review", operation="submit", draft_id=draft["id"], expected_revision=1)
        resource("review", operation="approve", draft_id=draft["id"], expected_revision=1, confirm=True)

        release = request("POST", "/api/deployments",
                          {"legion_id": node_ids["group"], "name": "Knowledge research · deployed"})
    # The engineering profile must be closed before the verified deployment copy.
    return create_deployment(source, runtime, release["id"], password=PASSWORD)


def main():
    python = ROOT / "backend/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise SystemExit("Run scripts/setup.ps1 (Windows) or bash scripts/setup.sh first.")
    ensure_knowledge_dependencies(python)
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=ROOT / ".open-agent-world/examples/knowledge-legion-deploy")
    parser.add_argument("--port", type=int, default=38476)
    parser.add_argument("--no-open", action="store_true", help="Do not open the default browser")
    parser.add_argument("--prepare-only", action="store_true", help="Create the deployed profile without starting a server")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Run npm --prefix frontend run build first.")
    runtime = prepare(args.data_root.expanduser().resolve())
    print(f"Deployment data: {runtime}\nAccess password (unless rotated): {PASSWORD}", flush=True)
    if args.prepare_only:
        return 0
    from backend.deploy import main as deploy_main
    sys.argv = ["deploy", "--serve", str(runtime), "--host", "127.0.0.1", "--port", str(args.port)]
    if not args.no_open:
        sys.argv.append("--open")
    deploy_main()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
