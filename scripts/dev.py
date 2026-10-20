"""Portable local launcher: Python scripts/dev.py [--agent-runtime core.mock]."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from setup import backend_python, prerequisites, profile_name


def available_port(preferred: int) -> int:
    if not 1 <= preferred <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    for port in range(preferred, min(preferred + 100, 65536)):
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
    if process.stdin:
        try:
            process.stdin.write(b"shutdown\n")
            process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=40)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name == "nt":
        # Vite owns build workers; terminate the whole owned process tree.
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Keep cleaning up the other child and preserve the startup error.
            print(f"Unable to stop owned process {process.pid}; check process permissions.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-runtime", default="google.adk")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--profile", type=profile_name, default="default")
    parser.add_argument("--plugin-path", action="append", type=Path, default=[])
    parser.add_argument("--startup-timeout-seconds", type=int, default=0,
                        help="Zero waits until ready or process exit, including long migrations")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.startup_timeout_seconds < 0:
        parser.error("Startup timeout must be nonnegative")
    python = backend_python(root)
    if not python.is_file():
        parser.error(f"Run setup.sh --profile {args.profile} --skip-build or setup.ps1 -Profile {args.profile} -SkipBuild first")
    commands = prerequisites(install=False, warn=False)
    check = [str(python), str(root / "scripts/check-source-environment.py"), "--profile", args.profile]
    for path in args.plugin_path:
        check += ["--plugin-path", str((root / path).resolve())]
    subprocess.run(check, cwd=root, check=True)
    subprocess.run([commands["node"], str(root / "frontend/scripts/prepare-dev.mjs")], cwd=root, check=True)
    backend_port = available_port(args.backend_port)
    frontend_port = available_port(args.frontend_port)
    if frontend_port == backend_port:
        frontend_port = available_port(frontend_port + 1)
    env = dict(os.environ)
    env["OPEN_AGENT_WORLD_AGENT_RUNTIME"] = args.agent_runtime
    env["OPEN_AGENT_WORLD_MODE"] = "development"
    env["OAW_DEV_BACKEND_HTTP_URL"] = f"http://127.0.0.1:{backend_port}"
    env["OAW_DEV_BACKEND_WS_URL"] = f"ws://127.0.0.1:{backend_port}"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    processes = []
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous_termination = signal.signal(signal.SIGTERM, interrupted)
    try:
        backend = subprocess.Popen([str(python), "-m", "backend.launcher", "--mode", "development",
                                    "--profile", args.profile, "--port", str(backend_port), "--strict-port", "--desktop"], cwd=root, env=env, stdin=subprocess.PIPE, **options)
        processes.append(backend)
        started = time.monotonic()
        notice = started
        # Loopback readiness must not depend on system HTTP proxy settings.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            if backend.poll() is not None:
                raise RuntimeError("Backend exited during startup; see its error above")
            try:
                with opener.open(env["OAW_DEV_BACKEND_HTTP_URL"] + "/health/ready", timeout=1):
                    break
            except (urllib.error.URLError, TimeoutError):
                now = time.monotonic()
                if args.startup_timeout_seconds and now - started >= args.startup_timeout_seconds:
                    raise RuntimeError("Backend startup timed out; omit --startup-timeout-seconds for long migrations")
                if now >= notice:
                    print(f"Waiting for backend startup ({now - started:.0f}s elapsed). Ctrl+C cancels.", flush=True)
                    notice = now + 5
                time.sleep(0.2)
        # Shared predev preparation already completed before backend startup.
        frontend = subprocess.Popen([commands["node"], str(root / "frontend/node_modules/vite/bin/vite.js"),
                                     "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                                    cwd=root / "frontend", env=env, **options)
        processes.append(frontend)
        print(f"Open Agent World: http://127.0.0.1:{frontend_port}", flush=True)
        print(f"Development profile: {args.profile}; press F3 for debugging and reset controls.", flush=True)
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.3)
        for name, process in (("Backend", backend), ("Frontend", frontend)):
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited unexpectedly (exit code {process.returncode}); see its output above")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            for process in reversed(processes):
                stop(process)
        finally:
            signal.signal(signal.SIGTERM, previous_termination)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
