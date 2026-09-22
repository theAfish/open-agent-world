"""Build and open an isolated, key-free deployed workspace demo."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PASSWORD = "oaw-demo-2026"


def prepare(directory: Path) -> Path:
    source, runtime = directory / "engineering", directory / "runtime"
    if (runtime / "deployment.json").is_file():
        return runtime
    if source.exists() or runtime.exists():
        raise ValueError("Incomplete example data exists. Choose a new --data-root; existing data will not be overwritten.")
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.deploy import create_deployment
    from backend.main import create_app

    settings = replace(Settings.for_data_root(source), agent_runtime="example.deployment-demo",
                       plugin_directories=(HERE / "plugins",))
    # Exercise the real engineering API in-process; no management listener is exposed.
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        def request(method, path, body):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def node(kind, name, **extra):
            return request("POST", "/api/nodes", {"type": kind, "name": name, **extra})

        chat = node("conversation", "体验对话")
        agent = node("agent", "演示助手（预设回复）")
        guide = node("text", "使用指南", content=(HERE / "guide.md").read_text(encoding="utf-8"))
        checklist = node("text", "交付清单", content=(HERE / "checklist.md").read_text(encoding="utf-8"))
        notes = node("example.deployment-notes", "插件笔记")
        request("POST", "/api/edges", {"source": agent["id"], "target": chat["id"], "relationship": "participate"})
        request("POST", f"/api/conversations/{chat['id']}/sessions", {
            "title": "开始体验", "group_title": "部署体验", "participant_ids": [agent["id"]]})
        cards = request("POST", "/api/legion-groups", {
            "name": "部署体验工作区", "node_ids": [c["id"] for c in (chat, agent, guide, checklist, notes)]})
        legion = next(c for c in cards if c["type"] == "legion")
        layout = {"version": 2, "root": {
            "kind": "split", "axis": "horizontal", "ratio": 0.62,
            "first": {"kind": "pane", "view": {"card_id": chat["id"]}},
            "second": {"kind": "tabs", "views": [{"card_id": c["id"]} for c in (guide, checklist, notes)],
                       "active_view": {"card_id": guide["id"]}}}}
        request("PATCH", f"/api/nodes/{legion['id']}", {"config": {"workspace_layout": layout}})
        release = request("POST", "/api/deployments", {"legion_id": legion["id"], "name": "部署模式 · 体验应用"})
    # The engineering profile must be closed before the verified deployment copy.
    return create_deployment(source, runtime, release["id"], password=PASSWORD)


def main():
    python = ROOT / "backend/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise SystemExit("Run scripts/setup.ps1 (Windows) or bash scripts/setup.sh first.")
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / ".open-agent-world/examples/deployed-workspace")
    parser.add_argument("--port", type=int, default=38475)
    parser.add_argument("--no-open", action="store_true", help="Do not open the default browser")
    parser.add_argument("--prepare-only", action="store_true", help="Create the deployed profile without starting a server")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Run npm --prefix frontend run build first.")
    runtime = prepare(args.data_root.expanduser().resolve())
    print(f"Demo data: {runtime}\nDemo password (unless rotated): {PASSWORD}", flush=True)
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
