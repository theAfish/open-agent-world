"""The PowerShell entry preserves native arguments and UTF-8 pipeline input."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell entry")
class PowerShellEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT / ".tmp"
        temporary.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="cli shell ", dir=temporary)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "open_agent_world/cli").mkdir(parents=True)
        shutil.copyfile(ROOT / "oaw.ps1", self.root / "oaw.ps1")
        shutil.copyfile(ROOT / "scripts/oaw.py", self.root / "scripts/oaw.py")
        (self.root / "open_agent_world/__init__.py").write_text("", encoding="utf-8")
        (self.root / "open_agent_world/cli/__init__.py").write_text(
            "import json, os, sys\n"
            "def main(argv=None):\n"
            "    sys.stdout.reconfigure(encoding='utf-8')\n"
            "    print(json.dumps({'args': sys.argv[1:], 'stdin': sys.stdin.buffer.read().decode('utf-8'), 'cwd': os.getcwd()}, ensure_ascii=False))\n"
            "    return 23\n", encoding="utf-8")

    def invoke(self, command, *, expected_cwd=None):
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command + "; exit $LASTEXITCODE"],
            cwd=ROOT.parent, input="", text=True, encoding="utf-8", capture_output=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 23, completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["cwd"], str(expected_cwd or ROOT.parent))
        return payload

    def test_installation_root_preserves_native_arguments(self):
        payload = self.root / "payload"
        payload.mkdir()
        for name in ("scripts", "open_agent_world"):
            shutil.move(str(self.root / name), str(payload / name))
        working = self.root / "working directory"
        working.mkdir()
        arguments = ["--name", 'quoted "name"', "C:\\ends\\", 'mix \\"quoted\\" end\\', "a&b", "", "中文"]
        invocation = "Set-Location -LiteralPath " + ps_literal(working) + "; $values = @("
        invocation += ",".join(ps_literal(value) for value in arguments) + "); & "
        payload = self.invoke(invocation + ps_literal(self.root / "oaw.ps1") + " @values", expected_cwd=working)
        self.assertEqual(payload["args"], arguments)
        self.assertEqual(payload["stdin"], "")

    def test_pipeline_input_is_utf8_without_bom(self):
        text = '{"name":"项目笔记"}'
        command = ps_literal(text) + " | & " + ps_literal(self.root / "oaw.ps1") + " create text --input -"
        for prefix in ("", "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "):
            with self.subTest(encoding=prefix or "default"):
                payload = self.invoke(prefix + command)
                self.assertEqual(payload["args"], ["create", "text", "--input", "-"])
                self.assertEqual(payload["stdin"].rstrip("\r\n"), text)

    def test_json_output_can_be_captured_and_converted_in_powershell(self):
        for mode in ("--json", "--plain"):
            invocation = "& " + ps_literal(self.root / "oaw.ps1") + " " + mode + " '项目笔记'"
            for conversion in ("$raw = " + invocation + "; $value = $raw | ConvertFrom-Json",
                               "$value = " + invocation + " | ConvertFrom-Json"):
                with self.subTest(conversion=conversion):
                    payload = self.invoke(conversion + "; $value | ConvertTo-Json -Compress")
                    self.assertEqual(payload["args"], [mode, "项目笔记"])


if __name__ == "__main__":
    unittest.main()
