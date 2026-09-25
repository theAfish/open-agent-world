"""Explicitly gated Store acceptance against the existing private Release.

Starts the unmodified Marketplace with a snapshot of its seeded catalog. Only
that process receives the existing GitHub credential. No publishing or uploads.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--marketplace-repo', type=Path, required=True)
    parser.add_argument('--desktop-payload', type=Path)
    args = parser.parse_args()
    if os.environ.get('OAW_STORE_REAL_ACCEPTANCE') != '1':
        parser.error('Set OAW_STORE_REAL_ACCEPTANCE=1 to explicitly enable private Release acceptance.')
    root = Path(__file__).resolve().parents[1]
    repo = args.marketplace_repo.resolve()
    output = root / '.outputs' / 'store-private' / str(time.time_ns())
    output.mkdir(parents=True)
    # SQLite backup includes committed WAL content, without changing the source.
    with sqlite3.connect((repo / 'marketplace.db').as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(output / 'catalog.db') as target:
            source.backup(target)
    consumer = dict(os.environ)
    for key in list(consumer):
        if key.startswith(('GH_', 'GITHUB_', 'MARKETPLACE_')):
            del consumer[key]
    server_env = dict(consumer)
    token = os.environ.get('MARKETPLACE_GITHUB_TOKEN')
    if not token:
        gh = shutil.which('gh')
        if not gh:
            parser.error('Existing Marketplace credential or authenticated GitHub CLI is required.')
        result = subprocess.run([gh, 'auth', 'token'], capture_output=True, text=True, check=False)
        if result.returncode or not result.stdout.strip():
            parser.error('An existing authenticated GitHub CLI credential is required.')
        token = result.stdout.strip()
    server_env.update(MARKETPLACE_GITHUB_TOKEN=token,
        MARKETPLACE_DATABASE_URL='sqlite:///' + (output / 'catalog.db').as_posix(),
        PYTHONPATH=str(repo / 'src'), PYTHONDONTWRITEBYTECODE='1')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    python = repo / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    with (output / 'marketplace.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([str(python), '-m', 'uvicorn', 'oaw_marketplace.app:create_app', '--factory',
            '--host', '127.0.0.1', '--port', str(port)], cwd=output, env=server_env,
            stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError('Marketplace startup failed; inspect the local server log.')
                try:
                    with urlopen(url + '/v1/packs', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError('Marketplace did not become ready.')
            consumer['OPEN_AGENT_WORLD_MARKETPLACE_URL'] = url
            if args.desktop_payload:
                consumer['OAW_PACK_DESKTOP_PAYLOAD'] = str(args.desktop_payload.resolve())
            subprocess.run([shutil.which('node') or 'node', 'frontend/scripts/run-pack-e2e.mjs', '--store-real'],
                cwd=root, env=consumer, check=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=5)
    print(f'Marketplace evidence: {output}')


if __name__ == '__main__':
    main()
