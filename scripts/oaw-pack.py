"""Run the existing Pack CLI from any external project directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_agent_world.pack import main

if __name__ == '__main__':
    main()
