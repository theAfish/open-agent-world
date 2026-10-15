"""Run OAW's existing scoped file contract inside the remote VM.

The small standard-library module set is delivered in memory, not installed in
the mutable workspace. No cloud credentials or host paths cross this boundary.
The payload uses stdin so large upload chunks do not exceed argv limits.
"""
import base64
from functools import lru_cache
import json
from pathlib import Path
import shlex
import zlib


_WORKER = '''
import base64, importlib.abc, importlib.util, json, sys, zlib
sources = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname in {"backend", "backend.sandbox"})
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        exec(compile(sources[module.__name__], "<oaw-file-helper>", "exec"), module.__dict__)
sys.meta_path.insert(0, Loader())
from pathlib import Path
from backend.sandbox.files import file_operation
from backend.sandbox.models import SandboxError, ResourceAccess
try:
    line = sys.stdin.readline(32 * 1024 * 1024 + 1)
    if len(line) > 32 * 1024 * 1024:
        raise ValueError("File request exceeds size limit")
    request = json.loads(line)
    result = file_operation(Path("/workspace"), ResourceAccess.READ_WRITE, (), **request)
    print(json.dumps({"result": result}))
except (SandboxError, OSError, ValueError, TypeError) as exc:
    print(json.dumps({"error": {"type": type(exc).__name__, "message": str(exc)}}))
'''


@lru_cache(maxsize=1)
def worker_command() -> str:
    root = Path(__file__).resolve().parents[2] / "sandbox"
    sources = {"backend": "", "backend.sandbox": ""}
    for name in ("models", "materialization", "files", "listing", "mutations", "streaming", "transfers"):
        sources["backend.sandbox." + name] = (root / (name + ".py")).read_text(encoding="utf-8")
    encoded = base64.b64encode(zlib.compress(json.dumps(sources).encode())).decode()
    return shlex.join(("/usr/bin/python3", "-I", "-c", _WORKER, encoded))
