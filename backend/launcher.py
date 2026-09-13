"""Own the local server lifetime, including clean development resets."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import webbrowser

import uvicorn

from backend.config import Settings
from backend.main import create_app


def bind_local_port(preferred: int, attempts: int = 100) -> socket.socket:
    for port in range(preferred, min(preferred + attempts, 65536)):
        listener = socket.socket()
        try:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            listener.setblocking(False)
            return listener
        except OSError:
            listener.close()
    raise RuntimeError(f"No free loopback port near {preferred}")


def configure_logs(settings: Settings):
    # Keep active log writes outside a relocatable store: storage migration copies
    # and verifies that store before services start, while logging its progress.
    directory = settings.data_root.with_name(settings.data_root.name + ".logs") if settings.storage_config_path else settings.data_root / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "launcher.log"
    handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stderr)], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    return log_path


async def serve(settings, listener, *, frontend=None, desktop=False, open_browser=False):
    parent_closed = threading.Event()
    if desktop:
        def watch_parent():
            # EOF also handles a crashed desktop parent, without a detached backend.
            sys.stdin.readline()
            parent_closed.set()
        threading.Thread(target=watch_parent, daemon=True).start()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    port = listener.getsockname()[1]
    opened = False
    while True:
        development = None
        if settings.application_mode == "development":
            from backend.development import DevelopmentControl
            development = DevelopmentControl(settings)
        app = create_app(settings, development=development, frontend_directory=frontend)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_config=None,
                                               proxy_headers=False, timeout_graceful_shutdown=30))
        async def monitor():
            nonlocal opened
            announced = False
            while not server.should_exit:
                if server.started and not announced:
                    print(json.dumps({"event": "ready", "url": url, "mode": settings.application_mode}), flush=True)
                    announced = True
                    if open_browser and not opened:
                        webbrowser.open(url)
                        opened = True
                if parent_closed.is_set() or development and development.pending:
                    server.should_exit = True
                    return
                await asyncio.sleep(.2)
        watcher = asyncio.create_task(monitor())
        connection = listener
        try:
            await server.serve(sockets=[connection])
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            connection.close()
        if not development or not development.pending or parent_closed.is_set():
            if not server.started and not parent_closed.is_set():
                raise RuntimeError("Backend startup failed. See launcher.log.")
            return
        if not app.state.clean_shutdown:
            raise RuntimeError("Backend shutdown failed; the development profile was not reset. See launcher.log.")
        from backend.development import reset_profile
        backup = reset_profile(settings, development.pending)
        logging.getLogger(__name__).info("Development reset completed; recovery backup: %s", backup)
        # Windows IOCP cannot safely re-register a duplicated, previously closed
        # listener. Rebind a new socket after the old server has fully drained.
        listener = bind_local_port(port, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("production", "development", "preview"), default="production")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--port", type=int, default=38473)
    parser.add_argument("--strict-port", action="store_true")
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true", help="Disable execution environments for UI-only acceptance tests")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    settings = replace(Settings.from_environment(), application_mode=args.mode)
    if args.no_sandbox:
        settings = replace(settings, sandbox_runtime=None)
    if args.mode == "development":
        from backend.development import prepare_profile
        root = prepare_profile(args.profile)
        settings = replace(settings, data_root=root, database_path=root / "database/world.sqlite3", storage_config_path=None)
    elif args.mode == "preview":
        from backend.development import profile_path
        root = profile_path(args.profile).parents[1] / "preview" / args.profile
        settings = replace(settings, data_root=root, database_path=root / "database/world.sqlite3", storage_config_path=None)
    if args.frontend and not (args.frontend / "index.html").is_file():
        parser.error("Frontend build is missing. Run npm --prefix frontend run build first.")
    # prepare_profile must run before logging creates files inside a new dev root.
    log_path = configure_logs(settings)
    print(json.dumps({"event": "starting", "log": str(log_path), "mode": args.mode}), flush=True)
    listener = bind_local_port(args.port, 1 if args.strict_port else 100)
    try:
        asyncio.run(serve(settings, listener, frontend=args.frontend, desktop=args.desktop, open_browser=args.open))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
