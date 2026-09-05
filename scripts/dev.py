"""Portable local launcher: Python scripts/dev.py [--agent-runtime core.mock]."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request


def available_port(preferred: int) -> int:
    for port in range(preferred, preferred + 100):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No available port near {preferred}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-runtime", default="google.adk")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=5173)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    python = root / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    vite = root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if not python.is_file() or not vite.is_file() or node is None:
        parser.error("Run scripts/setup.sh (Linux/WSL/macOS) or scripts/setup.ps1 (Windows) first")
    backend_port = available_port(args.backend_port)
    frontend_port = available_port(args.frontend_port)
    if frontend_port == backend_port:
        frontend_port = available_port(frontend_port + 1)
    env = dict(os.environ)
    env["OPEN_AGENT_WORLD_AGENT_RUNTIME"] = args.agent_runtime
    env["OAW_DEV_BACKEND_HTTP_URL"] = f"http://127.0.0.1:{backend_port}"
    env["OAW_DEV_BACKEND_WS_URL"] = f"ws://127.0.0.1:{backend_port}"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    processes = []
    try:
        backend = subprocess.Popen([str(python), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                                    "--port", str(backend_port)], cwd=root, env=env, **options)
        processes.append(backend)
        for _ in range(100):
            if backend.poll() is not None:
                raise RuntimeError("Backend exited during startup; see its error above")
            try:
                with urllib.request.urlopen(env["OAW_DEV_BACKEND_HTTP_URL"] + "/api/catalog", timeout=1):
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Backend did not become ready")
        frontend = subprocess.Popen([node, str(vite), "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                                    cwd=root / "frontend", env=env, **options)
        processes.append(frontend)
        print(f"Open Agent World: http://127.0.0.1:{frontend_port}", flush=True)
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    main()
