"""Three real worker processes behind the hosted gateway for browser acceptance."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
import uvicorn

from backend.hosting.accounts import AccountStore
from backend.hosting.cli import initialize, provision
from backend.hosting.configuration import read_gateway_settings
from backend.hosting.gateway import create_gateway
from backend.hosting.templates import export_template
from backend.tests.test_deployments import prepare_source


def main():
    repository = Path(__file__).resolve().parents[2]
    temporary = repository / ".tmp"
    temporary.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hosted-browser-", dir=temporary))
    source = root / "source"
    records = prepare_source(source)
    template = root / "template.json"
    asyncio.run(export_template(source, records["release"]["id"], template))
    config = initialize(root / "hosting", "http://127.0.0.1:5184", True)
    store = AccountStore(config.parent / "accounts.sqlite3")
    profiles = []
    for index, username in enumerate(("alice", "bob", "carol")):
        user = store.create_user(username, username.title(), "test-hosted-password-93841")
        runtime = username != "carol"
        profile = asyncio.run(provision(root / "hosting", user["id"], name=username.title() + " workspace",
            mode="runtime" if runtime else "builder", template=template if runtime else None,
            agent_runtime="core.mock", upstream=f"http://127.0.0.1:{5191 + index}"))
        profiles.append(profile)
    workers = []
    with ExitStack() as stack:
        try:
            for index, profile in enumerate(profiles):
                log = stack.enter_context((root / f"worker-{index}.log").open("wb"))
                worker_config = root / "hosting/instances" / profile.profile_id / "worker.json"
                workers.append(subprocess.Popen([sys.executable, str(repository / "scripts/hosted.py"), "worker",
                    "--config", str(worker_config), "--port", str(5191 + index)], cwd=repository,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0))
            deadline = time.monotonic() + 45
            with httpx.Client(trust_env=False, timeout=1) as client:
                for index, worker in enumerate(workers):
                    while True:
                        if worker.poll() is not None:
                            raise RuntimeError(f"Worker exited; inspect {root / f'worker-{index}.log'}")
                        try:
                            if client.get(f"http://127.0.0.1:{5191 + index}/health/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("Hosted test workers did not become ready")
                        time.sleep(.1)
            frontend = Path(os.environ.get("OAW_TEST_FRONTEND_DIST", repository / "frontend/dist"))
            app = create_gateway(read_gateway_settings(config), frontend)
            uvicorn.run(app, host="127.0.0.1", port=5184, proxy_headers=False, access_log=False)
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.terminate()
            for worker in workers:
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()


if __name__ == "__main__":
    main()
