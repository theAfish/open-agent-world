"""Local trial launcher and HTTP-only card setup; no world database access."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def api(base: str, path: str, payload: dict | None = None):
    request = Request(base.rstrip('/') + '/api' + path,
                      data=json.dumps(payload).encode('utf-8') if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f'OAW {exc.code}: {exc.read().decode("utf-8")}') from exc


def setup(base: str, workspace: Path, model: str):
    catalog = api(base, '/catalog')
    if not any(plugin['id'] == 'openai.codex' for plugin in catalog['plugins']):
        raise RuntimeError('Backend has not loaded openai.codex. Start it with --with-editable ./plugins/codex.')
    existing = {node['id']: node for node in api(base, '/world')['nodes']}
    nodes = [
        {'id': 'codex-demo-agent', 'type': 'openai.codex.agent', 'name': 'Codex', 'position': {'x': 450, 'y': 350},
         'config': {'runtime_provider_id': 'openai.codex', 'model': model,
                    'workspace_path': str(workspace), 'codex_sandbox': 'workspace-write',
                    'inherit_legion_model': False,
                    'description': 'Codex coding agent in ' + str(workspace),
                    'system_instruction': 'You are Codex working inside Open Agent World. Help the user with their project and connected resources.'}},
        {'id': 'codex-demo-conversation', 'type': 'conversation', 'name': 'Talk to Codex', 'position': {'x': 850, 'y': 350}},
        {'id': 'codex-demo-notes', 'type': 'text', 'name': 'Codex notes', 'position': {'x': 450, 'y': 660},
         'content': 'You can ask Codex to read or edit this connected note.'},
    ]
    for node in nodes:
        if node['id'] not in existing:
            api(base, '/nodes', node)
        elif existing[node['id']]['type'] != node['type']:
            raise RuntimeError(f"Existing node {node['id']} has a different type")
    snapshot = api(base, '/world')
    pairs = {(e['source'], e['target'], e['relationship']) for e in snapshot['edges']}
    for target, relationship in [('codex-demo-conversation', 'participate'), ('codex-demo-notes', 'read_edit')]:
        if ('codex-demo-agent', target, relationship) not in pairs:
            api(base, '/edges', {'source': 'codex-demo-agent', 'target': target, 'relationship': relationship})
    summary = api(base, '/conversations/codex-demo-conversation')
    session = summary['sessions'][0]
    if 'codex-demo-agent' not in session['participant_ids']:
        api(base, f"/conversations/codex-demo-conversation/sessions/{session['id']}/participants",
            {'participant_ids': ['codex-demo-agent']})
    actual = next(n for n in snapshot['nodes'] if n['id'] == 'codex-demo-agent')
    print('Codex workspace: ' + actual['config']['workspace_path'], flush=True)
    print('Open Talk to Codex, mention @Codex, and send a task.', flush=True)


def free_port(preferred):
    for port in range(preferred, preferred + 100):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise RuntimeError('No free local port')


def stop_process(process):
    if os.name == 'nt':
        if process.poll() is None:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def demo_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'launcher.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Codex trial is already running. Open its printed URL, or run try.ps1 -Stop first.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('Codex trial is already running.') from exc
        yield


def demo(repo: Path, workspace: Path, model: str):
    if not (repo / 'backend/main.py').is_file():
        raise RuntimeError('--repo must point to an Open Agent World checkout')
    vite = repo / 'frontend/node_modules/vite/bin/vite.js'
    node = shutil.which('node')
    if not node or not vite.is_file():
        raise RuntimeError('Install Node.js and run npm --prefix frontend install first.')
    root = repo / '.open-agent-world/codex-card-demo'
    root.mkdir(parents=True, exist_ok=True)
    stop_file = root / 'stop.request'
    stop_file.unlink(missing_ok=True)
    backend_port, frontend_port = free_port(8088), free_port(5188)
    base = f'http://127.0.0.1:{backend_port}'
    environment = {**os.environ, 'OPEN_AGENT_WORLD_DATA_ROOT': str(root / 'world'),
                   'OPEN_AGENT_WORLD_AGENT_RUNTIME': 'core.mock',
                   'OAW_CODEX_STATE_DIR': str(root / 'sessions'),
                   'OAW_DEV_BACKEND_HTTP_URL': base,
                   'OAW_DEV_BACKEND_WS_URL': f'ws://127.0.0.1:{backend_port}'}
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    children = []
    with (root / 'backend.log').open('w', encoding='utf-8') as backend_log, (root / 'frontend.log').open('w', encoding='utf-8') as frontend_log:
        try:
            backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(backend_port)],
                                       cwd=repo, env=environment, stdout=backend_log, stderr=subprocess.STDOUT, **options)
            children.append(backend)
            deadline = time.monotonic() + 30
            while True:
                if backend.poll() is not None:
                    raise RuntimeError(f'Backend exited. See {root / "backend.log"}')
                try:
                    api(base, '/catalog')
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'Backend startup timed out. See {root / "backend.log"}')
                    time.sleep(.2)
            setup(base, workspace, model)
            frontend = subprocess.Popen([node, str(vite), '--host', '127.0.0.1', '--port', str(frontend_port), '--strictPort'],
                                        cwd=repo / 'frontend', env=environment, stdout=frontend_log, stderr=subprocess.STDOUT, **options)
            children.append(frontend)
            print(f'Open Agent World + Codex: http://127.0.0.1:{frontend_port}/', flush=True)
            print(f'API: {base} | Logs and trial data: {root}', flush=True)
            print('Press Ctrl+C to stop this trial. Cards and sessions are kept.', flush=True)
            while not stop_file.exists() and all(child.poll() is None for child in children):
                time.sleep(.5)
        finally:
            for child in reversed(children):
                stop_process(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'demo'])
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--model', default='default', help='default uses the model in Codex configuration')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error('workspace must be an existing directory')
    try:
        if args.action == 'demo':
            with demo_lock(args.repo.resolve() / '.open-agent-world/codex-card-demo'):
                demo(args.repo.resolve(), workspace, args.model)
        else:
            setup(args.api, workspace, args.model)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, URLError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
