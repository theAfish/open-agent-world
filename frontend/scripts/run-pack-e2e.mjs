/** Production host acceptance. The host frontend is built once before install. */
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { startFakeMarketplace } from './fake-marketplace.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const frontend = path.join(root, 'frontend');
const official = process.argv.includes('--store-official');
const declarative = process.argv.includes('--declarative');
const build = official ? {} : JSON.parse(await readFile(path.join(root, `.outputs/${declarative ? 'declarative-acceptance' : 'pack-acceptance'}/build.json`), 'utf8'));
if (!official && !declarative && build.schema_version !== 3) throw new Error('Rebuild the Greeter fixture through scripts/build-external-greeter.py');
const source = official ? 'store-official' : process.argv.includes('--store-real') ? 'store-real' : process.argv.includes('--store-fake') ? 'store-fake' : 'local';
if (official && Object.hasOwn(process.env, 'OPEN_AGENT_WORLD_MARKETPLACE_URL')) {
  throw new Error('Official acceptance forbids OPEN_AGENT_WORLD_MARKETPLACE_URL, including an empty override');
}
if (source === 'store-real' && (process.env.OAW_STORE_REAL_ACCEPTANCE !== '1' || !process.env.OPEN_AGENT_WORLD_MARKETPLACE_URL)) {
  throw new Error('Real Store acceptance requires OAW_STORE_REAL_ACCEPTANCE=1 and OPEN_AGENT_WORLD_MARKETPLACE_URL');
}
const dataRoot = path.join(root, '.outputs/pack-acceptance', `${source}-${Date.now()}`);
await mkdir(dataRoot, { recursive: true });
const port = Number(process.env.OAW_PACK_E2E_PORT ?? 38579);
const url = `http://127.0.0.1:${port}`;
const env = { ...process.env, OPEN_AGENT_WORLD_MODE: 'production', OPEN_AGENT_WORLD_DATA_ROOT: dataRoot,
  OPEN_AGENT_WORLD_AGENT_RUNTIME: 'mock', OAW_E2E_BASE_URL: url, OAW_PACK_ARTIFACT: build.artifact,
  OAW_PACK_E2E_DATA_ROOT: dataRoot, OAW_PACK_SOURCE: source, OAW_PACK_VERSION: official ? process.env.OAW_PACK_VERSION : build.version };
env.OAW_PACK_DECLARATIVE = declarative ? '1' : '0';
delete env.OPEN_AGENT_WORLD_PLUGIN_DIRS;
for (const key of Object.keys(env)) if (/^(?:GH_|GITHUB_|MARKETPLACE_)/.test(key)) delete env[key];
const payload = process.env.OAW_PACK_DESKTOP_PAYLOAD;
if (payload) {
  const inheritedPath = process.env.PATH ?? '';
  for (const key of Object.keys(env)) if (key.toLowerCase() === 'path') delete env[key];
  env.PATH = path.join(payload, 'tools') + path.delimiter + inheritedPath;
}
const python = payload ? path.join(payload, process.platform === 'win32' ? 'python/python.exe' : 'python/bin/python3')
  : path.join(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
let marketplace;
if (source === 'store-fake') {
  marketplace = await startFakeMarketplace(build.artifact, python);
  env.OPEN_AGENT_WORLD_MARKETPLACE_URL = marketplace.url;
}
let productionEndpoint = null;
if (official) {
  const { stdout } = await promisify(execFile)(python, ['-I', '-B', '-c',
    'import sys,json;sys.path.insert(0,sys.argv[1]);from backend.config import Settings;print(json.dumps(Settings.from_environment().marketplace_url))',
    payload ?? root], { env, windowsHide: true });
  productionEndpoint = JSON.parse(stdout.trim());
  if (!productionEndpoint) throw new Error('Official Marketplace endpoint has not been deployed and bound in this build');
  const parsed = new URL(productionEndpoint);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash
      || parsed.pathname !== '/' || ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)) {
    throw new Error('Official acceptance requires the confirmed public HTTPS origin embedded in this build');
  }
}
const expectedSha = official ? process.env.OAW_PACK_EXPECTED_SHA
  : createHash('sha256').update(await readFile(build.artifact)).digest('hex');
env.OAW_PACK_EXPECTED_SHA = expectedSha;
if (official && (!/^[a-f0-9]{64}$/.test(expectedSha ?? '') || !env.OAW_PACK_VERSION)) {
  throw new Error('Official acceptance requires OAW_PACK_VERSION and OAW_PACK_EXPECTED_SHA for a published Schema 3 Greeter');
}
let host;
let logs = '';

async function start() {
  const entry = payload ? ['-I', '-B', path.join(payload, 'launch.py')] : ['-m', 'backend.launcher'];
  host = spawn(python, [...entry, '--mode', 'production', '--port', String(port), '--strict-port',
    '--frontend', process.env.OAW_PACK_FRONTEND ?? path.join(payload ?? root, 'frontend/dist'), '--desktop'], { cwd: payload ?? root, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
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
  await run(source === 'local' ? 'install local' : 'install remote');
  if (!declarative) { await stop(); await start(); await run('restart activates'); }
  if (declarative) { await stop(); await start(); await run('saved note'); }
  if (marketplace || official) {
    await stop();
    if (marketplace) { await marketplace.stop(); marketplace = undefined; }
    if (official) {
      // Block upstream HTTP only in this host process. Keep the embedded default
      // and no Marketplace override, including across an offline cold start.
      env.HTTP_PROXY = env.HTTPS_PROXY = env.ALL_PROXY = 'http://127.0.0.1:9';
      env.NO_PROXY = 'localhost,127.0.0.1,::1';
    }
    await start();
    await run('Store offline');
  }
  const receipt = { status: 'passed', source, productionEndpoint, marketplaceOverride: official ? false : Boolean(env.OPEN_AGENT_WORLD_MARKETPLACE_URL), desktopPayload: payload ?? null, ...build, version: env.OAW_PACK_VERSION, dataRoot, url, sha256: expectedSha, offlineRestart: official || source === 'store-fake' };
  await writeFile(path.join(dataRoot, 'receipt.json'), JSON.stringify(receipt, null, 2));
  console.log(JSON.stringify(receipt));
} finally {
  try {
    await stop();
  } finally {
    if (marketplace) await marketplace.stop();
    await writeFile(path.join(dataRoot, 'acceptance-host.log'), logs);
  }
}
