"""CI shards must execute the full collection exactly once and propagate failures."""
from collections import Counter
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".github/run-backend-tests.py"


def test_shards_preserve_collection_file_fixtures_and_failure_exit(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    for index in range(5):
        (suite / f"example_{index}_test.py").write_text(
            "import pytest\n"
            "@pytest.fixture(scope='module')\n"
            "def values(): return []\n"
            "@pytest.mark.parametrize('value', [0, 1])\n"
            "def test_order(values, value):\n"
            "    assert values == list(range(value))\n"
            "    values.append(value)\n"
            f"    assert not ({index} == 4 and value == 1)\n",
            encoding="utf-8",
        )

    def run(name, *options):
        report = tmp_path / f"{name}.xml"
        result = subprocess.run(
            [sys.executable, str(RUNNER), str(suite), "-q", "-p", "no:cacheprovider",
             f"--junitxml={report}", *options],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        cases = ET.parse(report).findall(".//testcase")
        assert result.returncode == int(any(case.find("failure") is not None for case in cases)), result.stdout
        return [(case.get("classname"), case.get("name")) for case in cases]

    baseline = run("all")
    assert len(baseline) == 10
    shards = [run(f"shard-{index}", "--shard", f"{index}/3") for index in range(1, 4)]
    assert Counter(case for shard in shards for case in shard) == Counter(baseline)
    for filename, _ in baseline:
        owners = [shard for shard in shards if any(case[0] == filename for case in shard)]
        assert len(owners) == 1
        assert [case for case in owners[0] if case[0] == filename] == [case for case in baseline if case[0] == filename]


@pytest.mark.parametrize("value", ["0/4", "5/4", "bad"])
def test_invalid_shard_fails_before_collection(tmp_path, value):
    suite = tmp_path / "test_never_import.py"
    suite.write_text("raise RuntimeError('collection ran')", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(suite), "--shard", value],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 4
    assert "--shard must be INDEX/COUNT" in result.stderr
    assert "collection ran" not in result.stdout + result.stderr
