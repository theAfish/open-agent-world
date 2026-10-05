import type { CardFinish } from './cardFinish';
import { materialFragment, materialVertex } from './cardMaterialShader';
import { drawMaterialFallback } from './cardMaterialFallback';
import { configureMaterial, materialForFinish, resolveEnvironment, MATERIAL_DEBUG_VIEWS,
  type CardMaterial, type MaterialEnvironment, type MaterialDebugView } from './cardMaterial';
import type { MaterialMask } from './cardMaterialMask';
import { normalizeFinishing, type PrintFinishing, type ProductionLayer } from './cardProduction';
export { CARD_MATERIALS } from './cardMaterial';

/** Legacy event name retained; x/y describe the VIEW, never the light position. */
export const MATERIAL_LIGHT_EVENT = 'card-material-light';
export type MaterialLight = { x: number; y: number; active: boolean; immediate?: boolean };
export interface MaterialFrame extends MaterialLight {
  restrained?: boolean;
  roughness?: number;
  finish: CardFinish;
  material?: CardMaterial;
  finishing?: PrintFinishing;
  environment?: Partial<MaterialEnvironment>;
  debugView?: MaterialDebugView;
  backend?: 'auto' | 'fallback';
  width: number;
  height: number;
  aspect?: number;
  mask: MaterialMask;
  processLayer?: ProductionLayer;
}
export interface ResolvedMaterialFrame extends MaterialFrame {
  material: CardMaterial; environment: MaterialEnvironment; debugView: MaterialDebugView; aspect: number;
  finishing: PrintFinishing;
}

/** One application-wide GPU context. Visible cards receive 2D snapshots on demand. */
function createRenderer() {
  const source=document.createElement('canvas');
  const gl=source.getContext('webgl',{alpha:true,antialias:false,depth:false,premultipliedAlpha:false});
  if (!gl) return null;
  const compile=(type: number,code: string) => {
    const shader=gl.createShader(type);
    if (!shader) throw new Error('Cannot allocate material shader');
    gl.shaderSource(shader,code); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader,gl.COMPILE_STATUS)) {
      const error=gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error(error||'Shader compilation failed');
    }
    return shader;
  };
  const program=gl.createProgram();
  if (!program) return null;
  const shaders: WebGLShader[]=[];
  try {
    shaders.push(compile(gl.VERTEX_SHADER,materialVertex));
    shaders.push(compile(gl.FRAGMENT_SHADER,materialFragment));
    shaders.forEach(shader => gl.attachShader(program,shader)); gl.linkProgram(program);
    if (!gl.getProgramParameter(program,gl.LINK_STATUS)) throw new Error('Material link failed');
  } catch (error) {
    console.warn('Card material GPU unavailable; using CPU reference.',error);
    shaders.forEach(shader => gl.deleteShader(shader)); gl.deleteProgram(program);
    gl.getExtension('WEBGL_lose_context')?.loseContext(); return null;
  }
  shaders.forEach(shader => gl.deleteShader(shader));
  const buffer=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
  gl.useProgram(program);
  const position=gl.getAttribLocation(program,'position');
  gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
  const uniforms=Object.fromEntries(['resolution','aspect','viewPose','lightDirection','illumination','laminate','response',
    'pattern','patternScale','clearcoat','regionWeights','restrained','debugView','regionMask','protectionMask','finishing','foilTint','finishTarget','finishWidth']
    .map(name => [name,gl.getUniformLocation(program,name)]));
  const textures=[0,1].map(unit => {
    const texture=gl.createTexture(); gl.activeTexture(gl.TEXTURE0+unit); gl.bindTexture(gl.TEXTURE_2D,texture);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE); return texture;
  });
  let uploadedMask: MaterialMask | undefined, lost=false;
  source.addEventListener('webglcontextlost',event => { event.preventDefault(); lost=true; uploadedMask=undefined; });
  source.addEventListener('webglcontextrestored',() => { renderer?.dispose(); renderer=undefined; });
  return {
    draw(target: HTMLCanvasElement, frame: ResolvedMaterialFrame) {
      if (lost || gl.isContextLost()) return false;
      const ctx=target.getContext('2d'); if (!ctx) return false;
      const { width,height,mask,material:m,environment:e }=frame;
      if (source.width!==width) source.width=width;
      if (source.height!==height) source.height=height;
      gl.viewport(0,0,width,height);
      gl.uniform2f(uniforms.resolution,width,height); gl.uniform1f(uniforms.aspect,frame.aspect);
      gl.uniform2f(uniforms.viewPose,frame.x,frame.y);
      gl.uniform3f(uniforms.lightDirection,...e.light); gl.uniform2f(uniforms.illumination,e.intensity,e.ambient);
      gl.uniform3f(uniforms.laminate,m.laminate.opacity,m.laminate.roughness,m.laminate.metalness);
      gl.uniform4f(uniforms.response,m.response.specular,m.response.iridescence,m.response.diffraction,m.response.sparkle);
      gl.uniform4f(uniforms.pattern,m.pattern.brush,m.pattern.domains,m.pattern.flow,m.pattern.grooves);
      gl.uniform1f(uniforms.patternScale,m.pattern.scale); gl.uniform1f(uniforms.clearcoat,m.clearcoat.strength);
      const f=frame.finishing;
      gl.uniform4f(uniforms.finishing,f.spotUV,f.foil,f.emboss*(frame.processLayer?.relief==='recessed'?-1:1),f.edgeFoil);
      gl.uniform3f(uniforms.foilTint,...processFoilTint(frame));
      gl.uniform1f(uniforms.finishTarget,f.target==='artwork'?1:0);
      gl.uniform1f(uniforms.finishWidth,frame.processLayer ? .05+frame.processLayer.roughness*.3 : .09);
      gl.uniform3f(uniforms.regionWeights,m.mask.artwork,m.mask.frame,m.mask.accent);
      gl.uniform1f(uniforms.restrained,frame.restrained?1:0);
      gl.uniform1i(uniforms.debugView,MATERIAL_DEBUG_VIEWS.indexOf(frame.debugView));
      if (mask!==uploadedMask) {
        [mask.regions,mask.protection].forEach((canvas,unit) => {
          gl.activeTexture(gl.TEXTURE0+unit); gl.bindTexture(gl.TEXTURE_2D,textures[unit]);
          gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,canvas);
        }); uploadedMask=mask;
      }
      gl.uniform1i(uniforms.regionMask,0); gl.uniform1i(uniforms.protectionMask,1);
      gl.drawArrays(gl.TRIANGLES,0,6); ctx.drawImage(source,0,0); return true;
    },
    dispose() {
      textures.forEach(texture => gl.deleteTexture(texture)); gl.deleteBuffer(buffer); gl.deleteProgram(program);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
let renderer: ReturnType<typeof createRenderer> | undefined;
export function processFoilTint(frame: Pick<ResolvedMaterialFrame, 'processLayer' | 'finishing'>): [number, number, number] {
  const colour = frame.processLayer?.color;
  return colour && /^#[0-9a-f]{6}$/i.test(colour)
    ? [1, 3, 5].map(offset => parseInt(colour.slice(offset, offset + 2), 16) / 255) as [number, number, number]
    : frame.finishing.foilTone === 'gold' ? [.84, .67, .35] : [.88, .91, .92];
}
export function drawCardMaterial(target: HTMLCanvasElement, frame: MaterialFrame): 'webgl' | 'fallback' | false {
  const ctx=target.getContext('2d'); if (!ctx) return false;
  if (target.width!==frame.width) target.width=frame.width;
  if (target.height!==frame.height) target.height=frame.height;
  ctx.clearRect(0,0,frame.width,frame.height);
  const bound=(v: number) => Number.isFinite(v)?Math.max(-1,Math.min(1,v)):0;
  const resolved: ResolvedMaterialFrame={ ...frame,x:bound(frame.x),y:bound(frame.y),
    finishing:normalizeFinishing(frame.finishing),
    material:configureMaterial(frame.material??materialForFinish(frame.finish),
      frame.roughness===undefined?{}:{laminate:{roughness:frame.roughness}}),
    environment:resolveEnvironment(frame.environment),debugView:frame.debugView??'composite',
    aspect:frame.aspect??frame.height/Math.max(1,frame.width) };
  if (frame.processLayer?.kind === 'ink') {
    if (resolved.debugView === 'laminate') return 'fallback';
    if (resolved.debugView === 'regions' || resolved.debugView === 'coverage' || resolved.debugView === 'protection') {
      ctx.drawImage(resolved.debugView === 'protection' ? frame.mask.protection : frame.mask.regions, 0, 0); return 'fallback';
    }
    const pixels = frame.mask.regions.getContext('2d')!.getImageData(0, 0, frame.width, frame.height);
    const colour = processFoilTint(resolved);
    for (let i = 0; i < pixels.data.length; i += 4) {
      const coverage = pixels.data[i];
      for (let c = 0; c < 3; c++) pixels.data[i + c] = colour[c] * 255;
      pixels.data[i + 3] = coverage * frame.processLayer.strength;
    }
    ctx.putImageData(pixels, 0, 0); return 'fallback';
  }
  try {
    if (frame.backend!=='fallback') {
      if (renderer===undefined) renderer=createRenderer();
      if (renderer?.draw(target,resolved)) return 'webgl';
    }
  } catch { /* Preserve the top print and recover through the CPU reference. */ }
  drawMaterialFallback(target,resolved); return 'fallback';
}
if (import.meta.hot) import.meta.hot.dispose(() => { renderer?.dispose(); renderer=undefined; });
