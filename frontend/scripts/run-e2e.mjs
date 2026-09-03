import { spawn } from "node:child_process";
import { readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const projectRoot = path.resolve(frontendRoot, "..");
const backendUrl = "http://127.0.0.1:8017/api/world";
const frontendUrl = "http://127.0.0.1:5177";
const dataRoot = path.resolve(projectRoot, ".open-agent-world", "playwright");
const resultFile = path.join(dataRoot, "result.json");
const children = [];

async function resetDataRoot() {
  const relative = path.relative(projectRoot, dataRoot);
  if (relative !== path.join(".open-agent-world", "playwright")) {
    throw new Error(`Refusing to clear unexpected E2E data root: ${dataRoot}`);
  }
  await rm(dataRoot, { recursive: true, force: true });
}

function start(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    detached: process.platform !== "win32",
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
    if (process.platform === "win32") child.kill("SIGKILL");
    else process.kill(-child.pid, "SIGTERM");
  } catch {
    // It exited between the state check and signal.
  }
  child.unref();
}

async function waitForResult(runner) {
  const deadline = Date.now() + 45_000;
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
  throw new Error("Playwright did not report a result within 45 seconds");
}

let exitCode = 1;
try {
  await resetDataRoot();
  start(
    path.join(projectRoot, "backend", ".venv", "Scripts", "python.exe"),
    ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8017"],
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
    ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5177"],
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
    ["node_modules/@playwright/test/cli.js", "test"],
    {
      cwd: frontendRoot,
      env: { ...process.env, OAW_E2E_RESULT_FILE: resultFile },
    },
  );
  exitCode = await waitForResult(runner);
  stopTree(runner);
} finally {
  children.slice(0, 2).forEach(stopTree);
}

process.exitCode = exitCode;
