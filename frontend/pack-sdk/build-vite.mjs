import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { build } from 'vite';
import autoprefixer from 'autoprefixer';
import { hostRuntimeModule } from './build.mjs';

/** The same Pack artifact is consumed by source development and archive installs.
 * Libraries are resolved from the Pack's own installed dependency tree.
 */
export async function buildPackFrontendVite({ root = process.cwd(), outDir = 'dist', plugins: additionalPlugins = [], minify = true }) {
  root = path.resolve(root); outDir = path.resolve(root, outDir);
  if (root === outDir || path.dirname(outDir) === outDir) throw new Error('Pack output must be a separate build directory');
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  const manifests = new Map([[root, Promise.resolve(manifest)]]);
  const insideRoot = filename => {
    const relative = path.relative(root, filename);
    return relative === '' || (relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative));
  };
  const importOwner = filename => {
    const parts = path.relative(root, filename).split(path.sep);
    const installation = parts.lastIndexOf('node_modules');
    if (installation < 0) return root;
    const packageName = parts[installation + 1];
    if (!packageName) throw new Error('Pack dependency has no owning package');
    return path.join(root, ...parts.slice(0, installation + (packageName.startsWith('@') ? 3 : 2)));
  };
  const ownerManifest = owner => {
    if (!manifests.has(owner)) manifests.set(owner, readFile(path.join(owner, 'package.json'), 'utf8').then(JSON.parse));
    return manifests.get(owner);
  };
  const prefix = '\0oaw-host:';
  const boundary = (worker = false) => ({ name: 'oaw-pack-boundary', enforce: 'pre',
    async resolveId(source, importer) {
      importer = importer?.replaceAll('\\', '/');
      if (/^(react(?:\/.*)?|react-dom(?:\/.*)?|@oaw\/plugin-api)$/.test(source)) {
        if (worker) throw new Error('Pack workers cannot import host React or SDK modules');
        return prefix + source;
      }
      const filename = importer && !importer.startsWith('\0') ? importer.split('?')[0] : undefined;
      const owned = filename && insideRoot(filename);
      if (owned && (source.startsWith('.') || path.isAbsolute(source))) {
        const candidate = path.resolve(path.dirname(importer.split('?')[0]), source.split('?')[0]);
        if (!insideRoot(candidate)) throw new Error(`Pack source import escapes its frontend project: ${source}`);
      }
      if (owned && !source.startsWith('.') && !path.isAbsolute(source) && !source.startsWith('\0')) {
        // Each installed package owns its imports, including nested/scoped packages.
        // A transitive dependency does not become available to the Pack's own source.
        const owner = importOwner(filename);
        const declared = await ownerManifest(owner);
        const dependencies = new Set([...Object.keys(declared.dependencies ?? {}), ...Object.keys(declared.peerDependencies ?? {}),
          ...Object.keys(declared.optionalDependencies ?? {}), ...(declared.name ? [declared.name] : []), '@oaw/plugin-api',
          // Some published source libraries (for example @threlte/extras) put
          // runtime peers in devDependencies. Honor that explicit declaration
          // only for vendors; resolution still requires the package to already
          // exist in this Pack's installed tree. Pack source stays runtime-only.
          ...Object.keys(owner === root ? {} : declared.devDependencies ?? {})]);
        const name = source.startsWith('@') ? source.split('/').slice(0, 2).join('/') : source.split('/')[0];
        const internal = source.startsWith('#') && Object.keys(declared.imports ?? {}).some(pattern => {
          const wildcard = pattern.indexOf('*');
          return wildcard < 0 ? source === pattern : source.startsWith(pattern.slice(0, wildcard)) && source.endsWith(pattern.slice(wildcard + 1));
        });
        if (!dependencies.has(name) && !internal) throw new Error(`Undeclared Pack frontend dependency: ${name} (imported by ${path.relative(root, filename)})`);
      }
    },
    load(id) { if (id.startsWith(prefix)) return hostRuntimeModule(id.slice(prefix.length)); },
  });
  const plugins = [boundary(), ...additionalPlugins];
  const result = await build({ root, configFile: false, publicDir: false, base: './', plugins,
    define: { 'process.env.NODE_ENV': '"production"' },
    // Artifact JSX always uses the published host ABI, including when this
    // helper is called from a process with NODE_ENV=development.
    esbuild: { jsx: 'automatic', jsxImportSource: 'react', jsxDev: false },
    css: { postcss: { plugins: [autoprefixer({ overrideBrowserslist: ['Chrome >= 109', 'Firefox >= 115', 'Safari >= 16.4'] })] } },
    // Setup may link an exact-version cache into this Pack's node_modules.
    // Keep those imports under the same ownership checks as a fresh npm ci.
    resolve: { conditions: ['browser', 'module'], preserveSymlinks: true },
    worker: { format: 'es', plugins: () => [boundary(true)] },
    build: { outDir, emptyOutDir: true, minify, target: 'es2022', assetsInlineLimit: 0,
      rollupOptions: { input: path.join(root, 'index.tsx'), preserveEntrySignatures: 'strict',
        output: { format: 'es', entryFileNames: 'index.js', chunkFileNames: 'chunks/[name]-[hash].js', assetFileNames: 'assets/[name]-[hash][extname]' } },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(item => item.output);
  const files = [];
  for (const output of outputs) {
    const filename = path.join(outDir, output.fileName);
    let bytes = await readFile(filename);
    if (output.type === 'chunk' && output.viteMetadata?.importedCss.size) {
      const styles = [...output.viteMetadata.importedCss].map(file => './' + path.relative(path.dirname(output.fileName), file).replaceAll('\\', '/'));
      const loader = `\nif(typeof document!=="undefined")await Promise.all(${JSON.stringify(styles)}.map(relative=>{const href=new URL(relative,import.meta.url).href;if([...document.querySelectorAll('link[rel="stylesheet"]')].some(link=>link.href===href))return;return new Promise((resolve,reject)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=resolve;link.onerror=()=>{link.remove();reject(new Error('Unable to load Pack stylesheet'));};document.head.append(link);});}));\n`;
      bytes = Buffer.concat([bytes, Buffer.from(loader)]); await writeFile(filename, bytes);
    }
    files.push({ path: output.fileName, size_bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
  }
  const artifact = { api_version: 1, entry: 'index.js', files };
  await writeFile(path.join(outDir, 'frontend-build.json'), JSON.stringify(artifact, null, 2) + '\n');
  return artifact;
}
