import type { CardFinish } from './cardFinish';
import { materialFragment, materialVertex } from './cardMaterialShader';

export const MATERIAL_LIGHT_EVENT = 'card-material-light';
export type MaterialLight = { x: number; y: number };
type Channels = readonly [metalness: number, roughness: number, transmission: number, emission: number];
export const CARD_MATERIALS: Record<Exclude<CardFinish, 'normal'>, { art: Channels; glass: Channels; ior: number }> = {
  foil: { art: [.94, .3, 0, 0], glass: [.12, .13, .72, 0], ior: 1.46 },
  rainbow: { art: [.86, .26, 0, .7], glass: [.16, .12, .68, .15], ior: 1.52 },
  starlight: { art: [.3, .32, 0, 1], glass: [.08, .1, .82, .2], ior: 1.5 },
  laser: { art: [.92, .24, 0, 0], glass: [.2, .1, .64, 0], ior: 1.54 },
};
export interface MaterialFrame {
  restrained?: boolean;
  roughness?: number;
  finish: Exclude<CardFinish, 'normal'>;
  width: number;
  height: number;
  x: number;
  y: number;
  artEnd: number;
  glass: [number, number, number, number];
  /** Scale and offset of the canonical print. Chrome crops it at a fixed physical scale. */
  print: [number, number, number, number];
}

/** One offscreen GPU context for the whole application, never one WebGL context per card.
 * Visible cards receive a 2D snapshot; rendering happens only on resize/entry/pointer input. */
function createRenderer() {
  const source = document.createElement('canvas');
  const gl = source.getContext('webgl', { alpha: true, antialias: false, depth: false, premultipliedAlpha: false });
  if (!gl) return null;
  const compile = (type: number, code: string) => {
    const shader = gl.createShader(type);
    if (!shader) throw new Error('Cannot allocate card material shader');
    gl.shaderSource(shader, code);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const error = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(error || 'Cannot compile card material shader');
    }
    return shader;
  };
  const program = gl.createProgram();
  if (!program) return null;
  const shaders: WebGLShader[] = [];
  try {
    shaders.push(compile(gl.VERTEX_SHADER, materialVertex));
    shaders.push(compile(gl.FRAGMENT_SHADER, materialFragment));
    shaders.forEach(shader => gl.attachShader(program, shader));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Cannot link card material shader');
  } catch {
    shaders.forEach(shader => gl.deleteShader(shader));
    gl.deleteProgram(program);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return null;
  }
  shaders.forEach(shader => gl.deleteShader(shader));
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
  gl.useProgram(program);
  const position = gl.getAttribLocation(program, 'position');
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const uniforms = Object.fromEntries(['resolution','tilt','finish','artEnd','glassRect','artMaterial','glassMaterial','ior','printTransform','restrained']
    .map(name => [name, gl.getUniformLocation(program, name)]));
  // Rest poses are reused; bounded both by entry count and per-entry raster dimensions.
  const cache = new Map<string, HTMLCanvasElement>();
  let lost = false;
  source.addEventListener('webglcontextlost', event => { event.preventDefault(); lost = true; cache.clear(); });
  return {
    draw(target: HTMLCanvasElement, frame: MaterialFrame) {
      if (lost || gl.isContextLost()) return false;
      const ctx = target.getContext('2d');
      if (!ctx) return false;
      const { width, height, finish, x, y, artEnd, glass, print, restrained = false, roughness } = frame;
      const key = x === 0 && y === 0 ? JSON.stringify([finish,width,height,artEnd,glass,print,restrained,roughness]) : '';
      let raster = key ? cache.get(key) : undefined;
      if (!raster) {
        source.width = width; source.height = height;
        gl.viewport(0,0,width,height);
        gl.uniform2f(uniforms.resolution,width,height);
        gl.uniform2f(uniforms.tilt,x,y);
        gl.uniform1f(uniforms.finish, ['foil','rainbow','starlight','laser'].indexOf(finish)+1);
        gl.uniform1f(uniforms.artEnd,artEnd);
        gl.uniform4f(uniforms.glassRect,...glass);
        gl.uniform4f(uniforms.printTransform,...print);
        const material = CARD_MATERIALS[finish].art;
        gl.uniform4f(uniforms.artMaterial, material[0], roughness ?? material[1], material[2], material[3]);
        gl.uniform1f(uniforms.restrained, restrained ? 1 : 0);
        gl.uniform4f(uniforms.glassMaterial,...CARD_MATERIALS[finish].glass);
        gl.uniform1f(uniforms.ior,CARD_MATERIALS[finish].ior);
        gl.drawArrays(gl.TRIANGLES,0,6);
        if (key) {
          raster = document.createElement('canvas');
          raster.width = width; raster.height = height;
          raster.getContext('2d')?.drawImage(source,0,0);
          if (cache.size >= 20) cache.delete(cache.keys().next().value!);
          cache.set(key,raster);
        }
      }
      if (target.width !== width) target.width = width;
      if (target.height !== height) target.height = height;
      ctx.clearRect(0,0,width,height);
      ctx.drawImage(raster || source,0,0);
      return true;
    },
    dispose() {
      cache.clear(); gl.deleteBuffer(buffer); gl.deleteProgram(program);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
let renderer: ReturnType<typeof createRenderer> | undefined;
export function drawCardMaterial(target: HTMLCanvasElement, frame: MaterialFrame) {
  try {
    if (renderer === undefined) renderer = createRenderer();
    return renderer?.draw(target, frame) ?? false;
  } catch { return false; } // CSS plates remain visible on restricted/unsupported GPUs.
}
if (import.meta.hot) import.meta.hot.dispose(() => { renderer?.dispose(); renderer = undefined; });
