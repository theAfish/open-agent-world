/** Production host acceptance. The host frontend is built once before install. */
import { spawn } from 'node:child_process';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const frontend = path.join(root, 'frontend');
const build = JSON.parse(await readFile(path.join(root, '.outputs/pack-acceptance/build.json'), 'utf8'));
const dataRoot = path.join(root, '.outputs/pack-acceptance', `host-${Date.now()}`);
await mkdir(dataRoot, { recursive: true });
const port = Number(process.env.OAW_PACK_E2E_PORT ?? 38579);
const url = `http://127.0.0.1:${port}`;
const env = { ...process.env, OPEN_AGENT_WORLD_MODE: 'production', OPEN_AGENT_WORLD_DATA_ROOT: dataRoot,
  OPEN_AGENT_WORLD_AGENT_RUNTIME: 'mock', OAW_E2E_BASE_URL: url, OAW_PACK_ARTIFACT: build.artifact,
  OAW_PACK_E2E_DATA_ROOT: dataRoot };
delete env.OPEN_AGENT_WORLD_PLUGIN_DIRS;
const python = path.join(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
let host;
let logs = '';

async function start() {
  host = spawn(python, ['-m', 'backend.launcher', '--mode', 'production', '--port', String(port), '--strict-port',
    '--frontend', path.join(frontend, 'dist'), '--desktop'], { cwd: root, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  host.stdout.on('data', data => { logs += data; });
  host.stderr.on('data', data => { logs += data; });
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (host.exitCode !== null) throw new Error(`Host exited: ${logs.slice(-8000)}`);
    try { if ((await fetch(`${url}/api/health`)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Host startup timed out: ${logs.slice(-8000)}`);
}
async function stop() {
  if (!host || host.exitCode !== null) return;
  const child = host;
  child.stdin.end();
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill(); reject(new Error('Host shutdown timed out')); }, 60000);
    child.once('exit', () => { clearTimeout(timeout); resolve(); });
  });
}
async function run(phase) {
  console.log(`Pack production acceptance: ${phase}`);
  const child = spawn(process.execPath, ['node_modules/@playwright/test/cli.js', 'test', '-c', 'playwright.pack.config.ts', '--grep', phase],
    { cwd: frontend, env, stdio: 'inherit', windowsHide: true });
  const code = await new Promise(resolve => child.once('exit', resolve));
  if (code !== 0) throw new Error(`Pack acceptance failed: ${phase}`);
}
try {
  await start();
  await run('install local');
  await stop();
  await start();
  await run('restart activates');
  console.log(JSON.stringify({ status: 'passed', ...build, dataRoot, url }));
} finally {
  await stop();
  await writeFile(path.join(dataRoot, 'acceptance-host.log'), logs);
}
