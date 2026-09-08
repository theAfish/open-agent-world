"""Explicitly approved broker outage utility, separate from read-only WFP audit.

Starts ONLY the trusted production broker. IPC controls only the child handle
owned by this process: start, graceful stop, abnormal stop, status, or finish.
No caller executable, PID, profile, path, environment, or rule is accepted.
An existing broker is never interrupted unless its exact authenticated PID was
approved separately via the startup-only --replace-pid argument and UAC.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.sandbox.windows_network_broker import _PipeSecurity, probe_network_broker, run_broker
from backend.tests.windows_acceptance_pipe import serve


def broker_pid():
    import _winapi
    from multiprocessing.connection import PipeConnection
    security = _PipeSecurity()
    try:
        _winapi.WaitNamedPipe(security.name, 500)
        handle = _winapi.CreateFile(security.name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
            0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED | 0x100000 | 0x10000, 0)
    except OSError as exc:
        if getattr(exc, "winerror", None) in {2, 3}:
            return None
        raise
    with PipeConnection(handle) as connection:
        security.verify_server(handle)
        pid = wintypes.ULONG()
        security.check(security.kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)), "Read broker PID")
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        connection.send_bytes(b'{"version":1,"operation":"probe"}')
        if not connection.poll(5):
            raise TimeoutError("Broker identity probe timed out")
        result = json.loads(connection.recv_bytes(4096))
        connection.send_bytes(b"ack")
        if result != {"version": 1, "ok": True, "operation": "probe"}:
            raise RuntimeError("Broker identity probe failed")
        return pid.value


def replace_approved_broker(pid):
    security = _PipeSecurity()
    if not security.elevated or broker_pid() != pid:
        raise PermissionError("Approved broker PID no longer matches the authenticated pipe server")
    kernel = security.kernel
    kernel.TerminateProcess.argtypes, kernel.TerminateProcess.restype = [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
    security.check(handle, "Open explicitly approved broker")
    try:
        # Recheck with the process handle pinned, protecting against PID reuse.
        if broker_pid() != pid or security.process_identity(handle) != (security.owner, True, False):
            raise PermissionError("Broker identity changed before approved interruption")
        security.check(kernel.TerminateProcess(handle, 73), "Stop explicitly approved broker")
        if kernel.WaitForSingleObject(handle, 5000) != 0:
            raise TimeoutError("Approved broker did not exit")
    finally:
        kernel.CloseHandle(handle)


class BrokerControl:
    def __init__(self):
        self.child = None
        self.finished = False

    def start(self):
        if self.child is not None and self.child.poll() is None:
            return
        if broker_pid() is not None:
            raise RuntimeError("Another broker exists; refusing to adopt or stop it")
        # Base Python avoids a venv launcher intermediary: this exact process
        # handle is the broker, not a shell or an arbitrary descendant PID.
        self.child = subprocess.Popen([sys._base_executable, "-I", str(Path(__file__).resolve()), "--child"],
            cwd=Path(__file__).resolve().parents[2], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise RuntimeError("Test-owned broker exited before readiness")
            try:
                if broker_pid() == self.child.pid:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise TimeoutError("Test-owned broker did not become ready")

    def dispatch(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {"version", "operation"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or not isinstance(payload["operation"], str)
            or payload["operation"] not in {"start", "graceful", "abnormal", "status", "finish"}):
            raise ValueError("Only fixed test-owned broker lifecycle operations are permitted")
        operation = payload["operation"]
        if operation in {"start", "finish"}:
            self.start()
            self.finished = operation == "finish"
        elif operation in {"graceful", "abnormal"}:
            if self.child is None or self.child.poll() is not None:
                raise RuntimeError("No live test-owned broker to stop")
            if broker_pid() != self.child.pid:
                raise RuntimeError("Authenticated broker is not our child")
            if operation == "graceful":
                self.child.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.child.kill()
            self.child.wait(timeout=8)
            if broker_pid() is not None:
                raise RuntimeError("A broker still serves after the test-owned process exited")
        result = {"operation": operation, "pid": self.child.pid if self.child else None,
                  "exit_code": self.child.poll() if self.child else None}
        print(json.dumps(result), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session")
    parser.add_argument("--replace-pid", type=int)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        if args.session or args.replace_pid:
            parser.error("Child takes no control inputs")
        def graceful(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, graceful)
        try:
            run_broker()
        except KeyboardInterrupt:
            print("Production broker returned through its cleanup path", flush=True)
        return
    if not args.session or not _PipeSecurity().elevated:
        parser.error("A session and explicit UAC elevation are required")
    if args.replace_pid:
        replace_approved_broker(args.replace_pid)
    controller = BrokerControl()
    try:
        controller.start()
        serve(args.session, "BrokerTest", controller.dispatch, stopped=lambda: controller.finished)
    finally:
        # Restore service even if acceptance aborts. Persistent rules are never
        # released by this utility. The restored broker remains running.
        controller.start()


if __name__ == "__main__":
    main()
