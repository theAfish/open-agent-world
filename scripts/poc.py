"""Developer POC workflow. Only the explicit setup command installs dependencies."""
from pathlib import Path
import subprocess
import sys

from setup import ROOT, backend_python


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        # A documented recipe over existing setup; normal startup never installs.
        import argparse
        parser = argparse.ArgumentParser(description="Prepare the shared three-workspace POC once")
        parser.add_argument("--profile", default="poc")
        parser.add_argument("--pack", action="append", type=Path, default=[],
                            help="Supply an exact-version POC .oawpack instead of downloading (repeatable)")
        args = parser.parse_args(sys.argv[2:])
        from setup import main as setup
        paths = ("knowledge_base", "xrd", "sqlite", "matcreator", "structure_viewer")
        result = setup(["--profile", args.profile,
                        *[arg for name in paths for arg in ("--plugin-path", "plugins/" + name)],
                        "--runtime", "adk", "--runtime", "litellm", "--skip-build"])
        if result:
            return result
        result = subprocess.call([str(backend_python()), str(Path(__file__).resolve()),
            "deps", "--install", "--profile", args.profile,
            *[arg for pack in args.pack for arg in ("--pack", str(pack.resolve()))]], cwd=ROOT)
        if result:
            return result
        return subprocess.call([str(backend_python()), str(Path(__file__).resolve()), "init", "--profile", args.profile,
                               "--template", str(ROOT / "examples/poc/legion.json")], cwd=ROOT)
    python = backend_python()
    if not python.is_file():
        raise SystemExit("Run python scripts/poc.py setup first.")
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
    sys.path.insert(0, str(ROOT))
    from backend.poc import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
