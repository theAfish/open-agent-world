import { readFile } from 'node:fs/promises';

const systemEntry = /[/\\]@xyflow[/\\]system[/\\]dist[/\\]esm[/\\]index\.(?:m?js)$/;
const initializer = 'const d3ZoomInstance = zoom()';

function enableTouch(source) {
  if (!source.includes(initializer)) throw new Error('React Flow camera initializer changed; review dynamic touch support.');
  return source.replace(initializer, `${initializer}.touchable(() => true)`);
}

/** d3 only detects touch when the camera mounts. Register its native handlers
 * on every device so enabling Edge/Chrome device emulation later also works.
 * Keep the camera and all live card editors mounted when input devices change. */
export function reactFlowTouchInput() {
  return {
    name: 'oaw-react-flow-touch',
    enforce: 'pre',
    config: () => ({ optimizeDeps: { esbuildOptions: { plugins: [{
      name: 'oaw-react-flow-touch',
      setup(build) {
        build.onLoad({ filter: systemEntry }, async ({ path }) => ({
          contents: enableTouch(await readFile(path, 'utf8')), loader: 'js',
        }));
      },
    }] } } }),
    transform(source, id) {
      if (systemEntry.test(id)) return { code: enableTouch(source), map: null };
    },
  };
}
