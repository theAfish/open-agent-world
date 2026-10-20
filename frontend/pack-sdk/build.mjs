import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import sdkExports from './sdk-exports.mjs';

const exports = {
  react: ['Children', 'Component', 'Fragment', 'Profiler', 'PureComponent', 'StrictMode', 'Suspense',
    'cloneElement', 'createContext', 'createElement', 'createFactory', 'createRef', 'forwardRef',
    'isValidElement', 'lazy', 'memo', 'startTransition', 'useCallback', 'useContext', 'useDebugValue',
    'useDeferredValue', 'useEffect', 'useId', 'useImperativeHandle', 'useInsertionEffect', 'useLayoutEffect',
    'useMemo', 'useReducer', 'useRef', 'useState', 'useSyncExternalStore', 'useTransition', 'version'],
  reactDom: ['createPortal', 'flushSync'],
  jsx: ['Fragment', 'jsx', 'jsxs'],
  sdk: sdkExports,
};
const shared = { react: 'react', 'react-dom': 'reactDom', 'react/jsx-runtime': 'jsx', '@oaw/plugin-api': 'sdk' };

export function hostRuntimeModule(module) {
  const key = shared[module];
  if (!key) throw new Error(`Unsupported shared host module: ${module}`);
  return `const runtime = globalThis[Symbol.for("oaw.frontend.host.v1")];
    if (!runtime) throw new Error("This Pack requires OAW Frontend API 1");
    const shared = runtime.${key}; export default shared;
    ${exports[key].map(name => `export const ${name} = shared.${name};`).join('\n')}`;
}

/** Build a distributable module and its relative CSS, URL assets and workers.
 * Additional compiler plugins (for example Svelte) run after the shared ABI.
 * nodePaths is only needed by source-workspace builds with shared node_modules.
 */
export async function buildPackFrontend({ entryPoint, outfile, minify = true, plugins = [], nodePaths = [], workers = {} }) {
  if (!/\.m?js$/.test(outfile)) throw new Error('Pack frontend output must be a .js or .mjs file');
  const hostPlugin = { name: 'oaw-host-runtime', setup(builder) {
    builder.onResolve({ filter: /^(react(?:\/.*)?|react-dom(?:\/.*)?|@oaw\/plugin-api)$/ }, ({ path }) => {
      if (!shared[path]) throw new Error(`Unsupported shared host module: ${path}`);
      return { path, namespace: 'oaw-host' };
    });
    builder.onLoad({ filter: /.*/, namespace: 'oaw-host' }, ({ path }) => {
      return { loader: 'js', contents: hostRuntimeModule(path) };
    });
  } };
  const assetsPlugin = { name: 'oaw-relative-assets', setup(builder) {
    // A ?url import is a browser URL relative to this module, never the page.
    // This also supports prebuilt module workers such as pdf.worker.min.mjs.
    builder.onResolve({ filter: /\?url$/ }, async args => {
      const result = await builder.resolve(args.path.slice(0, -4), { resolveDir: args.resolveDir, kind: 'import-statement' });
      if (result.errors.length) return { errors: result.errors };
      return { path: result.path, namespace: 'oaw-url' };
    });
    builder.onLoad({ filter: /.*/, namespace: 'oaw-url' }, args => ({
      contents: `import asset from ${JSON.stringify(args.path + '?oaw-file')}; export default new URL(asset, import.meta.url).href;`, loader: 'js',
    }));
    builder.onResolve({ filter: /\?oaw-file$/ }, args => ({ path: args.path.slice(0, -9), namespace: 'oaw-file' }));
    builder.onLoad({ filter: /.*/, namespace: 'oaw-file' }, async args => ({ contents: await readFile(args.path), loader: 'file' }));
  } };
  const common = { bundle: true, format: 'esm', platform: 'browser',
    target: 'es2022', jsx: 'automatic', minify, metafile: true,
    write: false, nodePaths, assetNames: 'assets/[name]-[hash]',
    loader: { '.png': 'file', '.jpg': 'file', '.jpeg': 'file', '.svg': 'file', '.webp': 'file', '.woff': 'file', '.woff2': 'file', '.wasm': 'file' },
    define: { 'process.env.NODE_ENV': '"production"' },
  };
  const result = await build({ ...common, entryPoints: [entryPoint], outfile, plugins: [hostPlugin, assetsPlugin, ...plugins] });
  const outputFiles = [...result.outputFiles];
  for (const [name, entry] of Object.entries(workers)) {
    if (!/^[a-zA-Z0-9_-]+$/.test(name)) throw new Error(`Invalid Pack worker name: ${name}`);
    const worker = await build({ ...common, entryPoints: [entry], outfile: path.join(path.dirname(outfile), 'workers', `${name}.js`),
      plugins: [{ name: 'oaw-worker-boundary', setup(builder) {
        builder.onResolve({ filter: /^(react(?:\/.*)?|react-dom(?:\/.*)?|@oaw\/plugin-api)$/ }, () => ({ errors: [{ text: 'Pack workers cannot import the window host runtime' }] }));
      } }, assetsPlugin, ...plugins] });
    outputFiles.push(...worker.outputFiles);
  }
  const entry = outputFiles.find(file => file.path === path.resolve(outfile));
  const css = outputFiles.find(file => file.path === path.resolve(outfile).replace(/\.m?js$/, '.css'));
  if (css) {
    const relative = `./${path.relative(path.dirname(entry.path), css.path).replaceAll('\\', '/')}`;
    const loadCss = `\nif (typeof document !== "undefined") {\n`
      + ` const href = new URL(${JSON.stringify(relative)}, import.meta.url).href;\n`
      + ` if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === href)) {\n`
      + `  await new Promise((resolve, reject) => { const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = href; link.onload = resolve; link.onerror = () => { link.remove(); reject(new Error('Unable to load Pack stylesheet')); }; document.head.append(link); });\n }\n}\n`;
    entry.contents = Buffer.concat([entry.contents, Buffer.from(loadCss)]);
  }
  const root = path.resolve(path.dirname(outfile));
  const files = [];
  for (const file of outputFiles) {
    await mkdir(path.dirname(file.path), { recursive: true });
    await writeFile(file.path, file.contents);
    files.push({ path: path.relative(root, file.path).replaceAll('\\', '/'), size_bytes: file.contents.length,
      sha256: createHash('sha256').update(file.contents).digest('hex') });
  }
  // The archive builder can use this list to include every generated asset.
  const manifest = { api_version: 1, entry: path.basename(outfile), files };
  await writeFile(path.join(root, 'frontend-build.json'), JSON.stringify(manifest, null, 2) + '\n');
  return { ...result, outputFiles, packManifest: manifest };
}
