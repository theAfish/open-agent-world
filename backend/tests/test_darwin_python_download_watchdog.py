"""The macOS CPython bootstrap must fail on stalled I/O and stop its child."""
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sandbox.models import SandboxPreparationError
from backend.sandbox.python_runtime import SharedPythonRuntime, _InstallationStalled


class DarwinDownloadWatchdogTests(unittest.TestCase):
    def test_silent_download_is_terminated_with_actionable_error(self):
        class Process:
            pid = 4321
            stopped = False

            def poll(self):
                return -15 if self.stopped else None

            def wait(self, timeout=None):
                if self.stopped:
                    return -15
                time.sleep(0.015)
                raise subprocess.TimeoutExpired("uv python install", timeout)

        with tempfile.TemporaryDirectory() as directory:
            runtime = SharedPythonRuntime(Path(directory))
            process = Process()
            def stop(_pid, _signal):
                process.stopped = True
            with patch("backend.sandbox.python_runtime.subprocess.Popen", return_value=process), \
                 patch("backend.sandbox.python_runtime.os.killpg", side_effect=stop, create=True), \
                 patch("backend.sandbox.python_runtime.DARWIN_PYTHON_DOWNLOAD_IDLE_TIMEOUT", 0.03), \
                 patch.object(runtime, "_progress_mtime", return_value=0):
                with self.assertRaisesRegex(_InstallationStalled, "no observable progress"):
                    runtime._run_darwin_install(["uv", "python", "install"], io.BytesIO(), {}, 30)
            self.assertTrue(process.stopped)

    def test_failed_download_is_recorded_and_reported(self):
        class Process:
            pid = 4321
            stopped = False

            def poll(self):
                return -15 if self.stopped else None

            def wait(self, timeout=None):
                if self.stopped:
                    return -15
                time.sleep(0.015)
                raise subprocess.TimeoutExpired("uv python install", timeout)

        with tempfile.TemporaryDirectory() as directory:
            runtime = SharedPythonRuntime(Path(directory))
            runtime.root.mkdir(parents=True)
            process = Process()

            def stop(_pid, _signal):
                process.stopped = True

            with patch("backend.sandbox.python_runtime.sys.platform", "darwin"), \
                 patch("backend.sandbox.python_runtime.subprocess.Popen", return_value=process), \
                 patch("backend.sandbox.python_runtime.os.killpg", side_effect=stop, create=True), \
                 patch("backend.sandbox.python_runtime.DARWIN_PYTHON_DOWNLOAD_IDLE_TIMEOUT", 0.03), \
                 patch.object(runtime, "_progress_mtime", return_value=0):
                with self.assertRaisesRegex(SandboxPreparationError, "no observable progress"):
                    runtime._run(["uv", "python", "install", "3.12"])
            records = [json.loads(line) for line in (runtime.root / "install.log").read_text().splitlines()]
            self.assertEqual(records[-1]["state"], "failed")
            self.assertIn("no observable progress", records[-1]["error"])
            self.assertTrue(process.stopped)


if __name__ == "__main__":
    unittest.main()
