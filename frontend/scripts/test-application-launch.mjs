/** End-to-end acceptance of isolated dev resets and the built application's startup. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = fileURLToPath(new URL("../../", import.meta.url));
const python = path.join(root, "backend/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const profileName = `acceptance-${Date.now()}`;
const children = [];
const output = path.join(root, ".tmp/application-acceptance");
await mkdir(output, { recursive: true });

async function port() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const exited = child => child.exitCode !== null || child.signalCode !== null;
async function until(check, label, timeout = 40_000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch (error) { last = error; }
    await delay(200);
  }
  throw new Error(`Timed out: ${label}`, { cause: last });
}
function start(command, args, env = {}) {
  const child = spawn(command, args, { cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, ...env } });
  child.log = "";
  for (const stream of [child.stdout, child.stderr]) stream.on("data", bytes => { child.log += bytes; });
  children.push(child);
  return child;
}
async function backend(mode, name = profileName) {
  const number = await port();
  const args = ["-m", "backend.launcher", "--mode", mode, "--profile", name, "--port", `${number}`, "--strict-port", "--desktop", "--no-sandbox"];
  if (mode !== "development") args.push("--frontend", path.join(root, "frontend/dist"));
  const child = start(python, args, { OPEN_AGENT_WORLD_AGENT_RUNTIME: "mock" });
  const url = `http://127.0.0.1:${number}`;
  await until(async () => {
    if (exited(child)) throw new Error(child.log);
    return (await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(1000) })).ok;
  }, `${mode} backend`);
  return { child, url };
}
async function json(url, value) {
  const response = await fetch(url, { signal: AbortSignal.timeout(3000), ...(value ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) } : {}) });
  assert(response.ok, `${response.status}: ${await response.clone().text()}`);
  return response.json();
}
async function stop(child) {
  if (exited(child)) return;
  child.stdin.end("shutdown\n");
  if (child.spawnfile === process.execPath) child.kill();
  await until(() => exited(child), "owned child exit", 45_000);
}

let browser;
try {
  const dev = await backend("development");
  const frontendPort = await port();
  const vite = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", `${frontendPort}`, "--strictPort"], {
    cwd: path.join(root, "frontend"), windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, OAW_DEV_BACKEND_HTTP_URL: dev.url, OAW_DEV_BACKEND_WS_URL: dev.url.replace("http:", "ws:") },
  });
  vite.log = "";
  for (const stream of [vite.stdout, vite.stderr]) stream.on("data", bytes => { vite.log += bytes; });
  children.push(vite);
  const frontendUrl = `http://127.0.0.1:${frontendPort}`;
  await until(async () => (await fetch(frontendUrl)).ok, "Vite");
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome", headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) });
  const page = await browser.newPage({ locale: "en-US", viewport: { width: 1360, height: 960 } });
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(String(error)));
  await page.goto(frontendUrl);
  await page.getByText("DEV · F3", { exact: true }).waitFor();
  await page.keyboard.press("F3");
  await page.getByRole("dialog", { name: "Development tools" }).waitFor();
  await page.screenshot({ path: path.join(output, "development-panel.png") });
  await page.keyboard.press("F3");
  assert.equal(await page.getByRole("dialog", { name: "Development tools" }).count(), 0);
  let library = await json(`${dev.url}/api/card-library`);
  const pack = Object.keys(library.packs).find(id => library.packs[id].definition.cards.includes("text"));
  library = await json(`${dev.url}/api/card-library/actions`, { action: "open_pack", id: pack, expected_revision: library.revision });
  await json(`${dev.url}/api/nodes`, { type: "text", name: "Reset acceptance", content: "disposable" });
  const before = await json(`${dev.url}/api/application`);
  await page.keyboard.press("F3");
  await page.getByRole("button", { name: "Test first launch", exact: true }).click();
  await page.getByRole("button", { name: "Review reset", exact: true }).click();
  await page.getByText("Clear all 1 cards in the world.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Confirm and restart", exact: true }).click();
  await until(async () => (await json(`${dev.url}/api/application`)).generation !== before.generation, "reset generation");
  await page.waitForFunction(() => !document.querySelector(".development-review"));
  await page.getByText("DEV · F3", { exact: true }).waitFor();
  assert.equal((await json(`${dev.url}/api/world`)).nodes.length, 0);
  library = await json(`${dev.url}/api/card-library`);
  assert.equal(Object.keys(library.collection).length, 0);
  assert(library.decks.every(deck => deck.entries.length === 0));
  assert(Object.values(library.packs).every(pack => !pack.opened));
  assert.equal(library.migration_pending, false);
  await page.screenshot({ path: path.join(output, "first-launch.png") });
  await page.close();
  await stop(vite);
  await stop(dev.child);
  const restarted = await backend("development");
  const persisted = await json(`${restarted.url}/api/application`);
  assert.equal(persisted.profile_id, before.profile_id);
  assert.notEqual(persisted.generation, before.generation);
  await stop(restarted.child);
  const production = await backend("preview");
  const formalPage = await browser.newPage({ locale: "en-US", viewport: { width: 1360, height: 960 } });
  await formalPage.goto(production.url);
  await formalPage.locator(".world-shell").waitFor();
  await formalPage.keyboard.press("F3");
  assert.equal(await formalPage.getByText("DEV · F3", { exact: true }).count(), 0);
  assert.equal(await formalPage.getByRole("dialog", { name: "Development tools" }).count(), 0);
  assert.equal((await fetch(`${production.url}/api/debug`)).status, 404);
  await formalPage.screenshot({ path: path.join(output, "production.png") });
  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
  await formalPage.close();
  await stop(production.child);
  console.log("PASS: F3, selective reset, first-launch state, changed-port persistence, production assets and absent debug API.");
} finally {
  if (browser) await browser.close();
  for (const [index, child] of children.entries()) {
    await writeFile(path.join(output, `process-${index}.log`), child.log ?? "");
    if (!exited(child)) {
      try { await stop(child); } catch { child.kill(); }
    }
  }
}
