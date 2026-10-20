import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

export const digest = value => createHash('sha256').update(value).digest('hex');
export const readReceipt = async file => {
  try { return JSON.parse(await readFile(file, 'utf8')); } catch { return null; }
};

// Content, not timestamps, identifies sources and outputs. In particular, a
// restored checkout or an edit of the same length must not reuse old artifacts.
export async function filesFingerprint(files) {
  return digest(JSON.stringify(await Promise.all([...new Set(files)].sort().map(async file => {
    try { return [file, await realpath(file), digest(await readFile(file))]; }
    catch (error) { if (error.code === 'ENOENT') return [file, null]; throw error; }
  }))));
}

export async function treeFiles(root, { exclude = [] } = {}) {
  const ignored = new Set(exclude);
  const walk = async directory => {
    let entries;
    try { entries = await readdir(directory, { withFileTypes: true }); }
    catch (error) { if (error.code === 'ENOENT') return []; throw error; }
    return (await Promise.all(entries.filter(entry => !ignored.has(entry.name)).map(async entry => {
      const filename = path.join(directory, entry.name);
      // Do not recurse through directory links (node_modules in particular).
      return entry.isDirectory() ? walk(filename) : [filename];
    }))).flat();
  };
  return (await walk(root)).sort();
}

export async function treeFingerprint(root, options) {
  return filesFingerprint(await treeFiles(root, options));
}

// Installed package manifests and their resolved locations identify the local
// dependency tree. A reinstall, removed package or retargeted junction invalidates
// the receipt, even if the declared versions have not changed.
export async function installedFingerprint(files) {
  return digest(JSON.stringify(await Promise.all([...new Set(files)].sort().map(async file => {
    try {
      const info = await stat(file, { bigint: true });
      return [file, await realpath(file), String(info.size), String(info.mtimeNs), String(info.ctimeNs)];
    } catch (error) { if (error.code === 'ENOENT') return [file, null]; throw error; }
  }))));
}

export const buildRuntime = () => [process.version, process.platform, process.arch,
  ...['NODE_OPTIONS', 'BROWSERSLIST', 'BROWSERSLIST_ENV', 'SOURCE_DATE_EPOCH'].map(name => process.env[name] ?? ''),
  Object.entries(process.env).filter(([name]) => name.startsWith('VITE_')).sort(([a], [b]) => a.localeCompare(b))];

export async function writeReceipt(file, value) {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value));
    await rename(temporary, file);
  } finally { await rm(temporary, { force: true }); }
}

/** A failed/interrupted build must never leave a reusable success receipt. */
export async function cachedBuild({ receiptFile, inputs, outputs, build, force = false }) {
  const before = await inputs();
  const receipt = await readReceipt(receiptFile);
  if (!force && receipt?.version === 1 && receipt.inputs === before && receipt.outputs === await outputs()) {
    return false;
  }
  await rm(receiptFile, { force: true });
  await build();
  const output = await outputs();
  // An editor may change a source while a build is running. Do not certify the
  // result as current; the watcher/next invocation will rebuild that source.
  if (before === await inputs()) await writeReceipt(receiptFile, { version: 1, inputs: before, outputs: output });
  return true;
}
