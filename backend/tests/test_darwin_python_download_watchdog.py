"""macOS uses the same package deadlines and failure receipts as Windows."""
import json
import subprocess
from unittest.mock import patch

import pytest
from backend.sandbox.python_runtime import SharedPythonRuntime, PACKAGE_INSTALL_TIMEOUT
from backend.sandbox.models import SandboxPreparationError


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_package_timeout_preserves_output_and_failure_receipt(tmp_path, platform):
    runtime = SharedPythonRuntime(tmp_path)
    runtime.root.mkdir(parents=True)
    def stalled(argv, **options):
        assert options['timeout'] == PACKAGE_INSTALL_TIMEOUT
        options['stdout'].write(b'download-in-progress\n')
        options['stdout'].flush()
        raise subprocess.TimeoutExpired(argv, options['timeout'])
    with patch('backend.sandbox.python_runtime.sys.platform', platform), patch('backend.sandbox.python_runtime.subprocess.run', side_effect=stalled):
        with pytest.raises(SandboxPreparationError, match='download-in-progress'):
            runtime._run(['uv', 'pip', 'install', 'example'])
    records = [json.loads(line) for line in (runtime.root / 'install.log').read_text().splitlines()]
    assert records[-1]['state'] == 'failed'
    assert 'download-in-progress' in records[-1]['output']
