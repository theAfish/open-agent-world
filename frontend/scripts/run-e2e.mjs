import { spawn, spawnSync } from "node:child_process";
import { readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const projectRoot = path.resolve(frontendRoot, "..");
const backendUrl = "http://127.0.0.1:8017/api/world";
const frontendUrl = "http://127.0.0.1:5177";
// Test worlds are disposable and must never share the application's state root.
const dataRoot = path.resolve(projectRoot, ".tmp", "playwright", "data");
const resultFile = path.join(dataRoot, "result.json");
const backendPython = path.join(projectRoot, "backend", ".venv", ...(process.platform === "win32"
  ? ["Scripts", "python.exe"] : ["bin", "python"]));
const children = [];
const ministerSuite = process.argv.includes("--minister");
const args = process.argv.slice(2);
const requestedTimeout = Number(process.env.OAW_E2E_TIMEOUT_MS);
const runnerTimeout = Number.isFinite(requestedTimeout) && requestedTimeout > 0 ? requestedTimeout : 120_000;
const testArgs = [];
let viteConfig;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--minister" || args[i] === "--builtin-ui") continue;
  if (args[i] === "--vite-config") {
    const value = args[++i];
    if (!value || value.startsWith("--")) throw new Error("--vite-config requires a configuration file path");
    viteConfig = path.resolve(value);
  } else testArgs.push(args[i]);
}
if (ministerSuite) testArgs.unshift("e2e/minister.spec.ts");

function prepareCodexFixture() {
  // Codex is optional in the application. Browser suites that exercise it
  // explicitly build and install the real archive into their disposable world.
  const needsCodex = ministerSuite || !testArgs.some(arg => arg.endsWith('.spec.ts'))
    || testArgs.some(arg => /(?:plugin-frontend|minister|card-library|default-card-face)\.spec\.ts$/.test(arg));
  if (!needsCodex) return;
  const source = path.join(projectRoot, 'packs/codex');
  const wheels = path.join(dataRoot, 'codex-wheels');
  const commands = [
    ['uv', ['build', '--wheel', '--out-dir', wheels, source]],
    [process.execPath, ['frontend/scripts/source-packs.mjs', '--frontend-path', path.join(source, 'frontend'), '--prepare', '--build']],
    [backendPython, ['-c', `
from pathlib import Path
import sys
from backend.packs.build import build_project
from backend.packs.installation import PackInstallationManager
from backend.plugins.builtin import create_builtin_registry
source, root = map(Path, sys.argv[1:])
artifact = build_project(source, root / 'codex.oawpack',
    backend_wheel=next((root / 'codex-wheels').glob('*.whl')), frontend_dir=source / 'frontend/dist')
PackInstallationManager(root, create_builtin_registry()).install(artifact.read_bytes())
`, source, dataRoot]],
  ];
  for (const [command, arguments_] of commands) {
    const result = spawnSync(command, arguments_, { cwd: projectRoot, stdio: 'inherit', windowsHide: true });
    if (result.error || result.status !== 0) throw result.error ?? new Error('Codex browser fixture preparation failed');
  }
}

async function resetDataRoot() {
  const relative = path.relative(projectRoot, dataRoot);
  if (relative !== path.join(".tmp", "playwright", "data")) {
    throw new Error(`Refusing to clear unexpected E2E data root: ${dataRoot}`);
  }
  await rm(dataRoot, { recursive: true, force: true });
}

function start(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    detached: process.platform !== "win32",
    windowsHide: true,
    stdio: "inherit",
  });
  children.push(child);
  return child;
}

async function waitFor(url, label) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The process is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${label} did not become ready at ${url}`);
}

function stopTree(child) {
  if (!child.pid || child.exitCode !== null) return;
  try {
    if (process.platform === "win32") {
      // A timed-out Playwright parent can leave its workers/browser alive.
      // Target only this runner's still-running, directly spawned child PID.
      const stopped = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true, stdio: "ignore", timeout: 10_000,
      });
      if (stopped.status !== 0) child.kill("SIGKILL");
    }
    else process.kill(-child.pid, "SIGTERM");
  } catch {
    // It exited between the state check and signal.
  }
  child.unref();
}

async function waitForResult(runner) {
  const deadline = Date.now() + runnerTimeout;
  while (Date.now() < deadline) {
    if (runner.exitCode !== null) return runner.exitCode ?? 1;
    try {
      const result = JSON.parse(await readFile(resultFile, "utf8"));
      if (result.status === "passed") return 0;
      if (typeof result.status === "string") return 1;
    } catch {
      // The reporter has not completed yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Playwright did not report a result within ${runnerTimeout / 1000} seconds`);
}

let exitCode = 1;
try {
  await resetDataRoot();
  prepareCodexFixture();
  start(
    backendPython,
    ["-m", "uvicorn", ministerSuite ? "backend.tests.minister_app:app" : args.includes("--builtin-ui") ? "backend.tests.builtin_ui_app:app" : "backend.main:app", "--host", "127.0.0.1", "--port", "8017", "--no-proxy-headers"],
    {
      cwd: projectRoot,
      env: {
        ...process.env,
        OPEN_AGENT_WORLD_AGENT_RUNTIME: "mock",
        OPEN_AGENT_WORLD_DATA_ROOT: dataRoot,
      },
    },
  );
  start(
    process.execPath,
    ["node_modules/vite/bin/vite.js", ...(viteConfig ? ["--config", viteConfig] : []), "--host", "127.0.0.1", "--port", "5177"],
    {
      cwd: frontendRoot,
      env: {
        ...process.env,
        OAW_DEV_BACKEND_HTTP_URL: "http://127.0.0.1:8017",
        OAW_DEV_BACKEND_WS_URL: "ws://127.0.0.1:8017",
      },
    },
  );
  await Promise.all([
    waitFor(backendUrl, "isolated backend"),
    waitFor(frontendUrl, "isolated frontend"),
  ]);

  await rm(resultFile, { force: true });
  const runner = start(
    process.execPath,
    ["node_modules/@playwright/test/cli.js", "test", ...testArgs],
    {
      cwd: frontendRoot,
      env: { ...process.env, OAW_E2E_DATA_ROOT: dataRoot, OAW_E2E_RESULT_FILE: resultFile, OAW_MINISTER_E2E: ministerSuite ? "1" : "0" },
    },
  );
  exitCode = await waitForResult(runner);
} finally {
  children.slice().reverse().forEach(stopTree);
}

process.exitCode = exitCode;
