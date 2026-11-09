"""Archive one local AtomSculptor Run using OAW's shared native exporter."""
import argparse
from pathlib import Path
import sys

# Keep direct invocation with a standard-library-only Python supported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_agent_world.run_export import (activity, execution_path, experiment_metadata, export_run, objects, render, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--label", default="difficult problem")
    parser.add_argument("--run-id")
    parser.add_argument("--manual-source", type=Path)
    parser.add_argument("--task-id", help="Stable task ID shared by baseline and candidate")
    parser.add_argument("--repeat", type=int, help="Matching repetition number for paired comparison")
    parser.add_argument("--variant", help="Baseline/candidate implementation label")
    parser.add_argument("--acceptance", choices=("passed", "failed", "unknown"), default="unknown",
                        help="Explicit external task verdict, independent of Run status")
    options = parser.parse_args()
    print(export_run(options.database, options.output_root, run_id=options.run_id,
                     label=options.label, manual_source=options.manual_source, task_id=options.task_id,
                     repeat=options.repeat, variant=options.variant, acceptance=options.acceptance))


if __name__ == "__main__":
    main()
