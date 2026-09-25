import { build } from 'esbuild';

const exports = {
  react: ['Children', 'Component', 'Fragment', 'Profiler', 'PureComponent', 'StrictMode', 'Suspense',
    'cloneElement', 'createContext', 'createElement', 'createFactory', 'createRef', 'forwardRef',
    'isValidElement', 'lazy', 'memo', 'startTransition', 'useCallback', 'useContext', 'useDebugValue',
    'useDeferredValue', 'useEffect', 'useId', 'useImperativeHandle', 'useInsertionEffect', 'useLayoutEffect',
    'useMemo', 'useReducer', 'useRef', 'useState', 'useSyncExternalStore', 'useTransition', 'version'],
  reactDom: ['createPortal', 'flushSync'],
  jsx: ['Fragment', 'jsx', 'jsxs'],
  sdk: ['SchemaFields', 't', 'useLocale', 'useNestedFlowGestures', 'useFileViewer', 'WorkspaceSection', 'useWorkspaceSections'],
};
const shared = { react: 'react', 'react-dom': 'reactDom', 'react/jsx-runtime': 'jsx', '@oaw/plugin-api': 'sdk' };

/** One self-contained ES module; React/DOM/SDK are references to host objects. */
export async function buildPackFrontend({ entryPoint, outfile, minify = true }) {
  return build({ entryPoints: [entryPoint], outfile, bundle: true, format: 'esm', platform: 'browser',
    target: 'es2022', jsx: 'automatic', minify, metafile: true,
    define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [{ name: 'oaw-host-runtime', setup(builder) {
      builder.onResolve({ filter: /^(react(?:\/.*)?|react-dom(?:\/.*)?|@oaw\/plugin-api)$/ }, ({ path }) => {
        if (!shared[path]) throw new Error(`Unsupported shared host module: ${path}`);
        return { path, namespace: 'oaw-host' };
      });
      builder.onLoad({ filter: /.*/, namespace: 'oaw-host' }, ({ path }) => {
        const key = shared[path];
        return { loader: 'js', contents: `const runtime = globalThis[Symbol.for("oaw.frontend.host.v1")];
          if (!runtime) throw new Error("This Pack requires OAW Frontend API 1");
          const shared = runtime.${key}; export default shared;
          ${exports[key].map(name => `export const ${name} = shared.${name};`).join('\n')}` };
      });
    } }],
  });
}
