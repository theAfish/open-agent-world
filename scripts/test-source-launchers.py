"""Offline source-entrypoint smoke tests; stdlib + Node only, no application setup."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parent


class SourceLaunchers(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oaw launchers ")
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / "checkout with spaces").resolve()
        (self.root / "scripts").mkdir(parents=True)
        for name in ("dev.py", "dev.sh", "start.py", "start.sh", "setup.sh"):
            shutil.copyfile(SCRIPTS / name, self.root / "scripts" / name)

    def run_command(self, command, **kwargs):
        return subprocess.run(command, cwd=self.temporary.name, text=True, capture_output=True, timeout=20, **kwargs)

    @unittest.skipIf(os.name == "nt", "POSIX shell wrappers")
    def test_shell_wrappers_preserve_spaced_paths_arguments_and_exit_status(self):
        for script in ("dev.sh", "start.sh"):
            result = self.run_command(["bash", str(self.root / "scripts" / script)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("environment is missing", result.stderr)
        python = self.root / "backend/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7)\n')
        python.chmod(0o755)
        for script in ("dev.sh", "start.sh"):
            result = self.run_command(["bash", str(self.root / "scripts" / script), "--profile", "space value", ""])
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(json.loads(result.stdout), [str(self.root / "scripts" / script.replace('.sh', '.py')), "--profile", "space value", ""])
        binary = self.root / "bin"
        binary.mkdir()
        shutil.copyfile(python, binary / "uv")
        (binary / "uv").chmod(0o755)
        result = self.run_command(["bash", str(self.root / "scripts/setup.sh"), "--profile", "fixture", "--skip-build"],
                                  env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]})
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["run", "--no-project", "--python", ">=3.11",
                         str(self.root / "scripts/setup.py"), "--profile", "fixture", "--skip-build"])

    @unittest.skipIf(os.name == "nt", "POSIX exec and signal identity")
    def test_production_start_uses_existing_build_and_executes_in_place(self):
        python = self.root / "backend/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        (self.root / "backend/__init__.py").touch()
        (self.root / "frontend/dist").mkdir(parents=True)
        (self.root / "frontend/dist/index.html").write_text("fixture")
        (self.root / "backend/launcher.py").write_text('import json,os,sys\n'
            'print(json.dumps({"pid":os.getpid(),"cwd":os.getcwd(),"argv":sys.argv[1:],"mode":os.environ["OPEN_AGENT_WORLD_MODE"]}),flush=True)\n'
            'sys.exit(23)\n')
        process = subprocess.Popen(["bash", str(self.root / "scripts/start.sh"), "--profile", "space value"],
                                   cwd=self.temporary.name, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 23, stderr)
        record = json.loads(stdout)
        self.assertEqual(record["pid"], process.pid)
        self.assertEqual(record["cwd"], str(self.root))
        self.assertEqual(record["mode"], "production")
        self.assertEqual(record["argv"], ["--frontend", str(self.root / "frontend/dist"), "--open", "--profile", "space value"])

    def prepare_dev_fixture(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node required")
        (self.root / "scripts/setup.py").write_text('from pathlib import Path\nimport sys\n'
            'def backend_python(root): return Path(sys.executable)\n'
            f'def prerequisites(**kwargs): return {{"node":{node!r}}}\n'
            'def profile_name(value): return value\n')
        audit = self.root / "audit.jsonl"
        self.audit = audit
        (self.root / "scripts/check-source-environment.py").write_text('import json,sys\nfrom pathlib import Path\n'
            f'with Path({str(audit)!r}).open("a") as f: f.write(json.dumps({{"kind":"backend-check","argv":sys.argv[1:]}})+"\\n")\n')
        (self.root / "backend").mkdir()
        (self.root / "backend/__init__.py").touch()
        (self.root / "backend/launcher.py").write_text('''import http.server,json,os,sys,threading
from pathlib import Path
audit = Path(__file__).resolve().parents[1] / 'audit.jsonl'
def record(kind):
    with audit.open('a') as f: f.write(json.dumps({'kind':kind,'pid':os.getpid()})+'\\n')
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers()
    def log_message(self, *args): pass
server = http.server.HTTPServer(('127.0.0.1', int(sys.argv[sys.argv.index('--port')+1])), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
record('backend')
sys.stdin.readline()
server.shutdown()
record('shutdown')
''')
        prepare = self.root / "frontend/scripts/prepare-dev.mjs"
        prepare.parent.mkdir(parents=True)
        prepare.write_text("import {appendFileSync} from 'node:fs';\n"
            f"appendFileSync({json.dumps(str(audit))}, JSON.stringify({{kind:'prepare'}})+'\\n');\n"
            "if(process.env.OAW_TEST_PREFLIGHT_FAIL)process.exit(3);\n")
        vite = self.root / "frontend/node_modules/vite/bin/vite.js"
        vite.parent.mkdir(parents=True)
        vite.write_text("const {appendFileSync}=require('node:fs');\n"
            f"appendFileSync({json.dumps(str(audit))}, JSON.stringify({{kind:'frontend',argv:process.argv.slice(2),pid:process.pid}})+'\\n');\n"
            "if(process.env.OAW_TEST_WAIT)setInterval(()=>{},1000);else process.exit(7);\n")
        return [sys.executable, str(self.root / "scripts/dev.py"), "--profile", "fixture", "--plugin-path", "extra plugin",
                "--backend-port", "18300", "--frontend-port", "18400", "--startup-timeout-seconds", "10"]

    def records(self):
        return [json.loads(line) for line in self.audit.read_text().splitlines()] if self.audit.exists() else []

    def test_portable_dev_prepares_once_propagates_failure_and_cleans_up(self):
        command = self.prepare_dev_fixture()
        for _ in range(2):
            result = self.run_command(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Frontend exited unexpectedly (exit code 7)", result.stderr)
        records = self.records()
        self.assertEqual([row["kind"] for row in records], ["backend-check", "prepare", "backend", "frontend", "shutdown"] * 2)
        for row in records:
            if row["kind"] == "frontend":
                self.assertEqual(row["argv"][:2], ["--host", "127.0.0.1"])
            if row["kind"] == "backend-check":
                self.assertEqual(row["argv"], ["--profile", "fixture", "--plugin-path", str(self.root / "extra plugin")])

    def test_failed_frontend_preflight_does_not_launch_services(self):
        result = self.run_command(self.prepare_dev_fixture(), env={**os.environ, "OAW_TEST_PREFLIGHT_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([row["kind"] for row in self.records()], ["backend-check", "prepare"])

    @unittest.skipIf(os.name == "nt", "POSIX SIGTERM and process groups")
    def test_portable_dev_sigterm_stops_both_owned_services(self):
        process = subprocess.Popen(self.prepare_dev_fixture(), cwd=self.temporary.name,
            env={**os.environ, "OAW_TEST_WAIT": "1"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not any(row["kind"] == "frontend" for row in self.records()):
                if time.monotonic() >= deadline or process.poll() is not None:
                    self.fail("Frontend did not start")
                time.sleep(0.05)
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(self.records()[-1]["kind"], "shutdown")
            for row in self.records():
                if row["kind"] in {"backend", "frontend"}:
                    with self.assertRaises(ProcessLookupError): os.kill(row["pid"], 0)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
