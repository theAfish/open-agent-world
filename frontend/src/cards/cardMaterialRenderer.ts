import type { CardFinish } from './cardFinish';
import { materialFragment, materialVertex } from './cardMaterialShader';
import { drawMaterialFallback } from './cardMaterialFallback';
import { CARD_MATERIALS, MATERIAL_REGIONS } from './cardMaterial';
export { CARD_MATERIALS } from './cardMaterial';

export const MATERIAL_LIGHT_EVENT = 'card-material-light';
export type MaterialLight = { x: number; y: number; active: boolean; immediate?: boolean };
export interface MaterialFrame extends MaterialLight {
  restrained?: boolean;
  roughness?: number;
  finish: Exclude<CardFinish, 'normal'>;
  width: number;
  height: number;
  mask: HTMLCanvasElement;
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
  const uniforms = Object.fromEntries(['resolution','macroResponse','mesoscopic','tilt','channels','detailChannels','structure','regions','roughness','restrained','regionMask']
    .map(name => [name, gl.getUniformLocation(program, name)]));
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  let uploadedMask: HTMLCanvasElement | undefined;
  let lost = false;
  source.addEventListener('webglcontextlost', event => { event.preventDefault(); lost = true; uploadedMask = undefined; });
  source.addEventListener('webglcontextrestored', () => { renderer?.dispose(); renderer = undefined; });
  return {
    draw(target: HTMLCanvasElement, frame: MaterialFrame) {
      if (lost || gl.isContextLost()) return false;
      const ctx = target.getContext('2d');
      if (!ctx) return false;
      const { width, height, finish, x, y, mask, restrained = false, roughness } = frame;
      if (source.width !== width) source.width = width;
      if (source.height !== height) source.height = height;
      gl.viewport(0,0,width,height);
      gl.uniform2f(uniforms.tilt,x,y);
      gl.uniform2f(uniforms.resolution,width,height);
      const material = CARD_MATERIALS[finish];
      gl.uniform4f(uniforms.channels,material.specular,material.iridescence,material.diffraction,material.edgeFoil);
      gl.uniform3f(uniforms.detailChannels,material.spotGloss,material.sparkle,material.emissive);
      gl.uniform4f(uniforms.structure,...material.structure);
      gl.uniform3f(uniforms.macroResponse,...material.macro);
      gl.uniform1f(uniforms.mesoscopic,material.mesoscopic);
      gl.uniform4f(uniforms.regions,MATERIAL_REGIONS.artwork,MATERIAL_REGIONS.frame,MATERIAL_REGIONS.icon,MATERIAL_REGIONS.accents);
      gl.uniform1f(uniforms.roughness,Math.max(.05,Math.min(1,roughness ?? material.roughness)));
      gl.uniform1f(uniforms.restrained,restrained ? 1 : 0);
      if (mask !== uploadedMask) {
        gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,mask);
        uploadedMask = mask;
      }
      gl.uniform1i(uniforms.regionMask,0);
      gl.drawArrays(gl.TRIANGLES,0,6);
      ctx.drawImage(source,0,0);
      return true;
    },
    dispose() {
      gl.deleteTexture(texture); gl.deleteBuffer(buffer); gl.deleteProgram(program);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
let renderer: ReturnType<typeof createRenderer> | undefined;
export function drawCardMaterial(target: HTMLCanvasElement, frame: MaterialFrame): 'webgl' | 'fallback' | false {
  const ctx = target.getContext('2d');
  if (!ctx) return false;
  if (target.width !== frame.width) target.width = frame.width;
  if (target.height !== frame.height) target.height = frame.height;
  ctx.clearRect(0,0,frame.width,frame.height);
  // A stationary laminate still reflects the studio. Rest is a cached neutral
  // view, not transparent stock; no work is scheduled once the view settles.
  try {
    if (renderer === undefined) renderer = createRenderer();
    if (renderer?.draw(target,frame)) return 'webgl';
  } catch { /* Keep the printed face intact if the GPU becomes unavailable. */ }
  drawMaterialFallback(target,frame);
  return 'fallback';
}
if (import.meta.hot) import.meta.hot.dispose(() => { renderer?.dispose(); renderer = undefined; });
