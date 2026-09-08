"""Authenticated, bounded transport for explicitly started Windows test utilities.

Not imported by production services. No pickle, execution, or caller paths.
"""
import json
import re
import time

from backend.sandbox.windows_network_broker import _PipeSecurity
from backend.sandbox.models import SandboxSecurityError

MAX_REPLY = 32768


def security_for(session, purpose):
    if not re.fullmatch(r"[a-f0-9]{32}", session) or purpose not in {"Audit", "BrokerTest"}:
        raise ValueError("Invalid acceptance session or purpose")
    security = _PipeSecurity()
    security.name += "." + purpose + "." + session
    return security


def request(session, purpose, payload):
    import _winapi
    from multiprocessing.connection import PipeConnection
    security = security_for(session, purpose)
    raw = json.dumps(payload).encode()
    if len(raw) > 512:
        raise ValueError("Request exceeds 512 bytes")
    deadline = time.monotonic() + 8
    while True:
        try:
            _winapi.WaitNamedPipe(security.name, 100)
            handle = _winapi.CreateFile(security.name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED | 0x100000 | 0x10000, 0)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {121, 231} or time.monotonic() >= deadline:
                raise RuntimeError("Approved acceptance helper is unavailable") from exc
    with PipeConnection(handle) as connection:
        security.verify_server(handle)
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        connection.send_bytes(raw)
        if not connection.poll(12):
            raise TimeoutError("Acceptance helper response timed out")
        response = json.loads(connection.recv_bytes(MAX_REPLY))
        connection.send_bytes(b"ack")
        if not isinstance(response, dict) or response.get("version") != 1:
            raise ValueError("Invalid acceptance helper response")
        if "error" in response:
            raise RuntimeError(response["error"])
        return response


def serve(session, purpose, dispatch, *, stopped=lambda: False):
    import _winapi
    from multiprocessing.connection import PipeConnection
    security = security_for(session, purpose)
    if not security.elevated:
        raise PermissionError("Start this reviewed test utility through explicit UAC approval")
    handle = security.create_server_pipe()
    deadline = time.monotonic() + 3600
    print(json.dumps({"ready": purpose, "session": session, "elevated": True}), flush=True)
    try:
        for _ in range(512):
            if stopped() or time.monotonic() > deadline:
                break
            try:
                pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 232:
                    security.kernel.DisconnectNamedPipe(handle)
                    continue
                raise
            try:
                while _winapi.WaitForSingleObject(pending.event, 200) == _winapi.WAIT_TIMEOUT:
                    if stopped() or time.monotonic() > deadline:
                        pending.cancel()
                        return
                _, error = pending.GetOverlappedResult(True)
                if error:
                    security.kernel.DisconnectNamedPipe(handle)
                    continue
            finally:
                del pending
            connection = PipeConnection(handle)
            try:
                if connection.poll(3):
                    payload = connection.recv_bytes(512)
                    security.verify_client(handle)
                    try:
                        result = dispatch(json.loads(payload))
                        response = {"version": 1, **result}
                    except (ValueError, OSError, RuntimeError, SandboxSecurityError) as exc:
                        response = {"version": 1, "error": str(exc)[:400]}
                    raw = json.dumps(response, separators=(",", ":")).encode()
                    if len(raw) > MAX_REPLY:
                        raw = b'{"version":1,"error":"Audit result exceeds bound"}'
                    connection.send_bytes(raw)
                    if connection.poll(3):
                        if connection.recv_bytes(8) != b"ack":
                            raise ValueError("Invalid acknowledgement")
            except (OSError, EOFError, ValueError, SandboxSecurityError):
                pass
            finally:
                connection._handle = None
                security.kernel.DisconnectNamedPipe(handle)
    finally:
        security.kernel.CloseHandle(handle)
