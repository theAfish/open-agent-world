"""Credential-gated HTTP/mount probe; deliberately contains no OAW runtime/data."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Probe:
    def __init__(self, mount: Path, token: str, run_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("OAW_PROBE_TOKEN must be a random URL-safe token of at least 32 characters")
        self.run_id = str(uuid.UUID(run_id))
        self.mount = mount
        self.token = token
        self.boot_id = str(uuid.uuid4())
        self.filename = f".oaw-agentcore-probe-{self.run_id}.json"
        self.lock = threading.Lock()

    def mounted(self) -> bool:
        return self.mount.is_absolute() and not self.mount.is_symlink() and os.path.ismount(self.mount)

    def marker(self, *, create: bool) -> dict:
        # Fail closed: an ordinary container directory is not persistence evidence.
        if not self.mounted():
            raise OSError("Expected mount is absent")
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise OSError("This probe requires Linux no-follow directory operations")
        with self.lock:
            directory = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                created = False
                if create:
                    try:
                        fd = os.open(self.filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory)
                    except FileExistsError:
                        pass  # Reconcile a lost reply by reading, never overwrite evidence.
                    else:
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump({"run_id": self.run_id, "created_by_boot": self.boot_id}, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.fsync(directory)
                        created = True
                fd = os.open(self.filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                        raise OSError("Invalid marker file")
                    marker = json.load(stream)
                if (not isinstance(marker, dict) or set(marker) != {"run_id", "created_by_boot"}
                        or marker["run_id"] != self.run_id):
                    raise ValueError("Marker does not match this probe")
                uuid.UUID(marker["created_by_boot"])
                return {"created": created, "marker": marker, "boot_id": self.boot_id,
                        "from_previous_boot": marker["created_by_boot"] != self.boot_id}
            finally:
                os.close(directory)

    def handler(self):
        probe = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OAWProbe"
            sys_version = ""

            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, _format, *args):
                pass  # No request paths, headers, tokens, or user-controlled text in logs.

            def reply(self, status, value):
                data = json.dumps(value, ensure_ascii=True).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.dispatch(False)

            def do_POST(self):
                self.dispatch(True)

            def dispatch(self, create):
                if self.path == "/health/live" and not create:
                    return self.reply(200, {"alive": True})
                if self.path == "/health/ready" and not create:
                    mounted = probe.mounted()
                    return self.reply(200 if mounted else 503, {"mounted": mounted})
                tokens = self.headers.get_all("X-OAW-Probe-Token", [])
                if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode(), probe.token.encode()):
                    return self.reply(401, {"error": "unauthorized"})
                sessions = self.headers.get_all("X-OAW-Session", [])
                if len(sessions) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sessions[0]):
                    return self.reply(400, {"error": "session_header_required"})
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
                    return self.reply(400, {"error": "request_body_not_supported"})
                if self.path == "/probe/status" and not create:
                    return self.reply(200, {"boot_id": probe.boot_id, "run_id": probe.run_id,
                                            "mounted": probe.mounted(), "uid": os.getuid()})
                if self.path != "/probe/marker":
                    return self.reply(404, {"error": "not_found"})
                try:
                    value = probe.marker(create=create)
                except FileNotFoundError:
                    return self.reply(404, {"error": "marker_missing"})
                except (OSError, ValueError, TypeError, AttributeError):
                    return self.reply(503, {"error": "mount_or_marker_invalid"})
                return self.reply(200, value)

        return Handler


def main():
    probe = Probe(Path(os.environ.get("OAW_PROBE_MOUNT", "/mnt/agenticfs/probe")),
                  os.environ["OAW_PROBE_TOKEN"], os.environ["OAW_PROBE_RUN_ID"])
    server = ThreadingHTTPServer(("0.0.0.0", 8080), probe.handler())
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
