"""Run OAW's CLI from any directory, including an installed desktop payload."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.version_info < (3, 11):
    raise SystemExit("OAW needs Python 3.11 or newer.")

from open_agent_world.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
