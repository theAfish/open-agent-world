"""User command installation stays isolated and preserves existing programs."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oaw_cli_install", ROOT / "scripts/install-cli.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class CliInstallTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".tmp"
        directory.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="cli install ", dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.source = self.workspace / "source 中文's"
        self.source.mkdir()
        for name in ("oaw", "oaw.ps1", "oaw.cmd"):
            shutil.copyfile(ROOT / name, self.source / name)
        (self.source / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/oaw.py", self.source / "scripts/oaw.py")
        (self.source / "open_agent_world/cli").mkdir(parents=True)
        (self.source / "open_agent_world/__init__.py").write_text("", encoding="utf-8")
        (self.source / "open_agent_world/cli/__init__.py").write_text(
            "import json,os,sys\n"
            "def main(argv=None):\n"
            "    sys.stdout.reconfigure(encoding='utf-8')\n"
            "    print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),'stdin':sys.stdin.buffer.read().decode('utf-8')},ensure_ascii=False))\n"
            "    return 23\n", encoding="utf-8")
        self.bin = self.workspace / "command bin"

    def test_existing_foreign_command_is_not_replaced_or_partially_installed(self):
        self.bin.mkdir()
        existing = self.bin / "oaw.ps1"
        existing.write_text("", encoding="utf-8")
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.bin, windows=True)
        self.assertEqual(existing.read_text(encoding="utf-8"), "")
        self.assertFalse((self.bin / "oaw.cmd").exists())

    def test_reinstall_and_uninstall_respect_the_current_source_owner(self):
        installer.install(self.source, self.bin, windows=False)
        installer.install(self.source, self.bin, windows=False)
        newer = self.workspace / "new location"
        newer.mkdir()
        installer.install(newer, self.bin, windows=False)
        self.assertEqual(installer.uninstall(self.source, self.bin, windows=False), [])
        self.assertTrue((self.bin / "oaw").exists())
        self.assertEqual(installer.uninstall(newer, self.bin, windows=False), ["oaw"])

    @unittest.skipUnless(os.name == "nt", "PowerShell command discovery")
    def test_bare_oaw_on_path_preserves_unicode_input_and_current_directory(self):
        installer.install(self.source, self.bin, windows=True)
        command = "$env:PATH = " + literal(str(self.bin) + ";") + " + $env:PATH; Set-Location -LiteralPath "
        command += literal(self.workspace) + "; " + literal('{"name":"项目笔记"}')
        command += " | oaw --json '带有\"引号\"的名字' | ConvertFrom-Json | ConvertTo-Json -Compress; exit $LASTEXITCODE"
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                input="", capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(data["args"], ["--json", '带有"引号"的名字'])
        self.assertEqual(Path(data["cwd"]), self.workspace)
        self.assertEqual(data["stdin"].strip(), '{"name":"项目笔记"}')

    def test_posix_user_command_runs_through_a_real_shell(self):
        shell = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path(shutil.which("sh") or "/bin/sh")
        if not shell.is_file():
            self.skipTest("No POSIX shell available")
        installer.install(self.source, self.bin, windows=False)
        # Installed commands keep the selected Python even if source discovery later fails.
        (self.source / "oaw").write_text('#!/bin/sh\nexit 99\n', encoding="utf-8")
        from shlex import quote
        shell_bin = self.bin.as_posix()
        if os.name == "nt":
            shell_bin = "/" + shell_bin[0].lower() + shell_bin[2:]
        command = "PATH=" + quote(shell_bin) + ':"$PATH"; oaw "hello world"'
        result = subprocess.run([str(shell), "-c", command], input="", capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["args"], ["hello world"])

    def test_old_python_is_rejected_before_installing_a_command(self):
        output = io.StringIO()
        with patch.object(installer.sys, "version_info", (3, 10, 12)), contextlib.redirect_stdout(output):
            self.assertEqual(installer.main(["--bin-dir", str(self.bin), "--no-path", "--json"]), 1)
        self.assertIn("Python 3.11", json.loads(output.getvalue())["error"])
        self.assertFalse(self.bin.exists())

    @unittest.skipUnless(os.name == "nt", "Windows user PATH")
    def test_user_path_is_idempotent_and_preserves_expandable_entries(self):
        state = {"value": r"%USERPROFILE%\tools;C:\existing", "kind": 2}
        class Key:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, REG_EXPAND_SZ=2,
            CreateKey=lambda *args: Key(), QueryValueEx=lambda *args: (state["value"], state["kind"]),
            SetValueEx=lambda key, name, reserved, kind, value: state.update(value=value, kind=kind))
        with patch.dict(sys.modules, {"winreg": registry}), patch.object(installer.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1)))):
            self.assertTrue(installer.update_windows_path(self.bin))
            self.assertFalse(installer.update_windows_path(self.bin))
            self.assertEqual(state["kind"], 2)
            self.assertTrue(state["value"].startswith(r"%USERPROFILE%\tools;C:\existing;"))
            self.assertTrue(installer.update_windows_path(self.bin, remove=True))
            self.assertEqual(state["value"], r"%USERPROFILE%\tools;C:\existing")

    def test_sandbox_identity_cannot_register_another_users_path(self):
        with patch.object(installer, "current_windows_user", return_value="CodexSandboxOffline"), patch.dict(os.environ, {"USERNAME": "taoyu"}):
            with self.assertRaises(installer.InstallError):
                installer.check_windows_user()


if __name__ == "__main__":
    unittest.main()
