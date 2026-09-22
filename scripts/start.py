"""Run the built application from a source checkout, without Vite or Node at runtime."""
from pathlib import Path
import os
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / "backend/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file() or not (root / "frontend/dist/index.html").is_file():
        raise SystemExit("Run setup, then npm --prefix frontend run build before starting the source checkout.")
    env = dict(os.environ)
    env["OPEN_AGENT_WORLD_MODE"] = "production"
    command = [str(python), "-m", "backend.launcher", "--frontend", str(root / "frontend/dist"),
               "--open", *sys.argv[1:]]
    if os.name != "nt":
        # Keep the launcher as the foreground process so signals reach the server.
        os.chdir(root)
        os.execve(str(python), command, env)
    try:
        result = subprocess.run(command, cwd=root, env=env)
        raise SystemExit(result.returncode)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
