"""Developer POC workflow; run --help for commands. Never installs dependencies."""
from pathlib import Path
import subprocess
import sys

from setup import ROOT, backend_python


def main():
    python = backend_python()
    if not python.is_file():
        raise SystemExit("Run python scripts/setup.py --profile poc --skip-build first.")
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
    sys.path.insert(0, str(ROOT))
    from backend.poc import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
