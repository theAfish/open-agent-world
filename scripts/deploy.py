"""One-command publication deployment; uses the project's installed Python environment."""
from pathlib import Path
import os
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / "backend/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise SystemExit("Run scripts/setup.ps1 (Windows) or bash scripts/setup.sh first.")
    try:
        raise SystemExit(subprocess.call([str(python), "-m", "backend.deploy", *sys.argv[1:]], cwd=root))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
