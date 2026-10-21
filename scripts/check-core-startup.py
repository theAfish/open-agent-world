"""Real HTTP startup in a Core-only interpreter; no optional distributions."""
from importlib.metadata import entry_points, distributions
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
assert not entry_points().select(group='open_agent_world.plugins'), 'Use a fresh Core-only interpreter'
installed = {dist.metadata['Name'].lower() for dist in distributions()}
assert not installed & {'google-adk', 'litellm', 'pymupdf', 'ase', 'numpy'}, installed
with tempfile.TemporaryDirectory(prefix='oaw-core-only-') as directory:
    root = Path(directory)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {**os.environ, 'OPEN_AGENT_WORLD_CORE_ONLY': '1', 'OPEN_AGENT_WORLD_DATA_ROOT': str(root),
           'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1'}
    with (root / 'server.log').open('w+', encoding='utf-8') as log:
        # A stop file allows graceful shutdown through Windows venv redirectors,
        # whose PID is not necessarily the actual Python server process.
        stop = root / 'stop'
        code = '''import asyncio, sys
from pathlib import Path
import uvicorn
async def main():
    server = uvicorn.Server(uvicorn.Config('backend.main:create_app', factory=True, host='127.0.0.1', port=int(sys.argv[1])))
    task = asyncio.create_task(server.serve())
    while not task.done() and not Path(sys.argv[2]).exists():
        await asyncio.sleep(.1)
    server.should_exit = True
    await task
asyncio.run(main())
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(port), str(stop)], cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 40
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        response = client.get('/api/catalog')
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError('Core startup timeout: ' + log.read())
                    time.sleep(.1)
                catalog = response.json()
                assert {p['id'] for p in catalog['plugins']} == {'open-agent-world.core'}
                assert client.get('/api/packs').status_code == 200
                assert client.get('/api/world?chunks=0:0').status_code == 200
                print(json.dumps({'core_http': 'passed', 'optional_plugins': 0,
                    'core_cards': len(catalog['node_types']), 'installed_distributions': sorted(installed)}))
        finally:
            stop.touch()
            process.wait(timeout=15)
