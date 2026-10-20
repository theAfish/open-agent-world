"""Native POSIX wrapper checks; also executable with stdlib Python in WSL/macOS."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Native POSIX wrapper tests")
class SourceShellTests(unittest.TestCase):
    def test_entrypoints_preserve_arguments_cwd_and_exit_status(self):
        base = ROOT / ".tmp"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="source shell ", dir=base) as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            tools = root / "tools"
            tools.mkdir()
            python = root / "backend/.venv/bin/python"
            python.parent.mkdir(parents=True)
            shim = ('#!/usr/bin/env python3\nimport json, os, sys\n'
                    'from pathlib import Path\n'
                    'Path(os.environ["OAW_TEST_ARGS"]).write_text(json.dumps(sys.argv[1:]))\n'
                    'sys.exit(23)\n')
            for executable in (tools / "uv", python):
                executable.write_text(shim)
                executable.chmod(0o755)
            env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                   "OAW_TEST_ARGS": str(root / "args.json")}
            for name in ("setup", "dev", "start"):
                shutil.copyfile(ROOT / "scripts" / (name + ".sh"), scripts / (name + ".sh"))
                subprocess.run(["bash", "-n", str(scripts / (name + ".sh"))], check=True)
                arguments = ["--profile", "fixture", "--plugin-path", "external plugin with spaces"]
                result = subprocess.run(["bash", str(scripts / (name + ".sh")), *arguments], cwd=root.parent, env=env)
                self.assertEqual(result.returncode, 23)
                recorded = json.loads((root / "args.json").read_text())
                expected = [str(scripts / (name + ".py")), *arguments]
                if name == "setup":
                    expected = ["run", "--no-project", "--python", ">=3.11", *expected]
                self.assertEqual(recorded, expected)


if __name__ == "__main__":
    unittest.main()
