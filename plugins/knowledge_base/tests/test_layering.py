"""The layering guard: the pipeline must not reach back into the host.

If this test fails, something in the core started importing OAW — which would make
the standalone service, the CLI and the MCP server unrunnable outside the backend
venv. Registration is the only part allowed to know about the host, and it lives in
``plugin.py`` and ``lifecycle.py``, which this test deliberately does not import.
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

CORE = ["errors", "context", "operations", "projection", "client", "markdown",
        "graph_store", "pipelines", "actions"]

PROBE = f"""
import sys
for name in {CORE!r}:
    __import__("oaw_knowledge_base." + name)
leaked = sorted(name for name in sys.modules
                if name == "open_agent_world" or name.startswith("open_agent_world.")
                or name == "backend" or name.startswith("backend."))
print(";".join(leaked))
"""


def test_core_imports_without_oaw():
    # A subprocess, because another test in this session may already have imported
    # the host for its own reasons; only a clean interpreter proves the point.
    result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                            cwd=str(SRC), timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"the core pulled in the host: {result.stdout}"
