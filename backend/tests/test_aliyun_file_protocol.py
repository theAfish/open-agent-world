"""Captured file helpers run with stdlib alone, without a cloud VM or host imports."""
import base64
import json
import shlex
import subprocess
import sys
import zlib

from backend.cloud.aliyun.file_protocol import worker_command


def test_captured_file_worker_uploads_and_downloads_without_host_file_transfer_module(tmp_path):
    command = shlex.split(worker_command())
    assert command[:3] == ['/usr/bin/python3', '-I', '-c']
    sources = json.loads(zlib.decompress(base64.b64decode(command[4])))
    assert 'backend.sandbox.file_policy' in sources
    assert 'backend.file_transfer' not in sources and 'backend.errors' not in sources
    assert 'from .file_policy import' in sources['backend.sandbox.transfers']
    assert 'from .file_policy import' in sources['backend.sandbox.streaming']
    worker = command[3].replace('Path("/workspace")', f'Path({str(tmp_path)!r})')

    def operate(operation, **options):
        # -S excludes installed packages too: an editable host installation must
        # not hide a missing dependency in the actual captured source bundle.
        result = subprocess.run([sys.executable, '-I', '-S', '-c', worker, command[4]],
            input=json.dumps({'operation': operation, 'path': 'result.bin', **options}),
            text=True, capture_output=True, timeout=15, check=True)
        response = json.loads(result.stdout)
        assert 'error' not in response, response
        return response['result']

    content = b'\0binary file data\xff'
    state = operate('upload_begin')
    chunk = operate('upload_chunk', staging=state['staging'], expected=state['signature'],
                    offset=0, data=base64.b64encode(content).decode())
    operate('upload_commit', staging=state['staging'], expected=chunk['signature'], offset=len(content))
    info = operate('download_info')
    assert info['size'] == len(content)
    downloaded = operate('download_chunk', expected=info['signature'], offset=0)
    assert base64.b64decode(downloaded['data'], validate=True) == content
    assert operate('download_chunk', expected=info['signature'], offset=len(content))['size'] == 0
    assert not list(tmp_path.glob('.oaw-upload-*'))
