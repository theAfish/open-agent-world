import type { GasPoint } from './shadowGas';

/** All distances are collection-local world units, not screen pixels. */
export const GAS_MATERIAL = {
  padding: 480, maskResolution: 512, maxResolution: 1280, maxDpr: 1.5,
  amplitude: 175, secondaryAmplitude: 48, width: 165,
  wavelength: 1000, speed: .18, curl: 230, fogOpacity: .34,
} as const;

// CPU distance field is rebuilt only when the local envelope changes. Two chamfer
// sweeps are O(pixels), independent of polygon vertex count; no per-frame DOM capture.
export function distanceField(mask: Uint8Array, w: number, h: number): Float32Array {
  const d = new Float32Array(w * h).fill(w + h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = y * w + x;
    if ((x && mask[i] !== mask[i - 1]) || (y && mask[i] !== mask[i - w])) d[i] = 0;
  }
  const relax = (x: number, y: number, step: number) => {
    const i = y * w + x, nx = x - step, ny = y - step;
    if (nx >= 0 && nx < w) d[i] = Math.min(d[i], d[y * w + nx] + 1);
    if (ny >= 0 && ny < h) {
      d[i] = Math.min(d[i], d[ny * w + x] + 1);
      if (nx >= 0 && nx < w) d[i] = Math.min(d[i], d[ny * w + nx] + Math.SQRT2);
      const ox = x + step;
      if (ox >= 0 && ox < w) d[i] = Math.min(d[i], d[ny * w + ox] + Math.SQRT2);
    }
  };
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) relax(x, y, 1);
  for (let y = h - 1; y >= 0; y--) for (let x = w - 1; x >= 0; x--) relax(x, y, -1);
  for (let i = 0; i < d.length; i++) if (mask[i]) d[i] *= -1;
  return d;
}

const vertex = `attribute vec2 position; varying vec2 uv;
void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}`;
const fragment = `precision highp float;
varying vec2 uv;
uniform sampler2D field;
uniform vec2 extent;
uniform float time, strength, amplitude, secondary, bandWidth, wavelength, speed, curl, fog;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+1.),f.x),f.y);}
float fbm(vec2 p){return .57*noise(p)+.28*noise(p*2.03+7.)+.15*noise(p*4.07-11.);}
float sdf(vec2 p){vec2 q=clamp(p/extent,0.,1.);vec2 rg=texture2D(field,q).rg;return ((rg.r*65280.+rg.g*255.)/65535.-.5)*1200.;}
void main(){
 vec2 p=vec2(uv.x,1.-uv.y)*extent;
 float d=sdf(p);
 // Do not spend noise work on the calm interior or transparent exterior.
 if(d < -25.){gl_FragColor=vec4(vec3(.1647,.1647,.153),.9);return;}
 if(d > 460.){gl_FragColor=vec4(0.);return;}
 float t=time*speed;
 vec2 q=p/wavelength;
 // Traveling low-frequency density displaces the envelope, always outside its safe base.
 float swell=amplitude*(.35+.65*noise(q*.8+vec2(t,-t*.63)))
   +secondary*sin(q.x*2.4+q.y*1.7-t*1.9);
 swell=max(18.,swell)*strength;
 // Advected domain warp: tangent transport plus local rotating flow. Unlike a
 // blurred dash stroke, this folds the density field back through itself.
 vec2 flow=vec2(fbm(q+vec2(-t,t*.31)),fbm(q+vec2(5.+t*.43,-t*.7)))-.5;
 vec2 adv=p+flow*curl;
 vec2 cell=adv/(wavelength*.58);
 vec2 f=fract(cell)-.5;
 float vortex=sin(fbm(floor(cell)+vec2(t*.2,0))*6.28+t*.5);
 float turn=vortex*3.2*exp(-dot(f,f)*8.);
 mat2 rot=mat2(cos(turn),-sin(turn),sin(turn),cos(turn));
 vec2 folded=(floor(cell)+.5+rot*f)*wavelength*.58;
 // Blend vortex cells near their borders to prevent tile seams.
 float cellWeight=(1.-smoothstep(.27,.5,abs(f.x)))*(1.-smoothstep(.27,.5,abs(f.y)));
 adv=mix(adv,folded,cellWeight);
 // Translate the noise domain, not time times a sampled distance-field normal:
 // the latter amplifies tiny gradient errors into streaks after long playback.
 float texture=fbm(adv/(wavelength*.6)+vec2(-t*.22,t*.13));
 float warped=d-swell+(noise(q*1.3+flow)-.5)*curl*.6*strength;
 float body=1.-smoothstep(-16.,85.,warped);
 body=max(body,1.-smoothstep(-8.,8.,d));
 // Thin folded wisps embedded in a broad volumetric band, not an emissive outline.
 float foldDistance=mix(d,sdf(adv),strength);
 float ribbon=sin((foldDistance-swell)/bandWidth*4.+texture*5.);
 ribbon=pow(.5+.5*ribbon,2.8);
 float fringe=exp(-pow((warped-22.)/bandWidth,2.));
 float breakup=smoothstep(.25,.7,texture);
 float smoke=fringe*(.35+breakup*.65+ribbon*.55)*fog*strength;
 smoke*=smoothstep(-18.,20.,d);
 float a=body*.9+smoke*(1.-body*.9);
 vec3 base=vec3(.1647,.1647,.153);
 vec3 mist=mix(vec3(.12,.128,.12),vec3(.32,.33,.31),ribbon*.65+texture*.25);
 vec3 color=(base*body*.9+mist*smoke*(1.-body*.9))/max(a,.0001);
 gl_FragColor=vec4(color,a);
}`;

type Shared = { canvas: HTMLCanvasElement; gl: WebGLRenderingContext; program: WebGLProgram; buffer: WebGLBuffer; users: number };
let shared: Shared | undefined;
function acquire(): Shared | undefined {
  if (shared && !shared.gl.isContextLost()) { shared.users++; return shared; }
  const canvas = document.createElement('canvas');
  canvas.width=canvas.height=GAS_MATERIAL.maxResolution;
  const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: false });
  if (!gl) return;
  const compile = (kind: number, source: string) => {
    const shader = gl.createShader(kind)!;
    gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error(message || 'Gas shader compilation failed');
    }
    return shader;
  };
  try {
    const vs = compile(gl.VERTEX_SHADER, vertex), fs = compile(gl.FRAGMENT_SHADER, fragment);
    const program = gl.createProgram()!;
    gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
    gl.deleteShader(vs); gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || 'Gas shader link failed');
    const buffer = gl.createBuffer()!; gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    shared = { canvas, gl, program, buffer, users: 1 };
    return shared;
  } catch (error) { console.warn('Shadow gas: static fallback', error); gl.getExtension('WEBGL_lose_context')?.loseContext(); }
}

export function createGasRenderer(output: HTMLCanvasElement) {
  const target = output.getContext('2d');
  if (!target) return;
  const renderer = acquire();
  if (!renderer) return;
  const { gl, canvas, program } = renderer;
  const texture = gl.createTexture()!;
  const raster = document.createElement('canvas'), ctx = raster.getContext('2d', { willReadFrequently: true })!;
  let extent = { width: 1, height: 1 }, lastGeometry = '', disposed = false;
  let lastPoints: GasPoint[] | undefined, lastWidth=0, lastHeight=0;
  const uniforms = new Map<string, WebGLUniformLocation | null>();
  const uniform = (name: string) => { if (!uniforms.has(name)) uniforms.set(name, gl.getUniformLocation(program, name)); return uniforms.get(name)!; };
  return {
    update(points: GasPoint[], width: number, height: number) {
      if(points===lastPoints&&width===lastWidth&&height===lastHeight)return;
      lastPoints=points;lastWidth=width;lastHeight=height;
      const signature = `${width},${height}:` + points.map(p => `${p.x},${p.y}`).join(';');
      if (signature === lastGeometry) return;
      lastGeometry = signature;
      const pad = GAS_MATERIAL.padding;
      extent = { width: width + pad * 2, height: height + pad * 2 };
      const scale = GAS_MATERIAL.maskResolution / Math.max(extent.width, extent.height);
      const w = Math.max(2, Math.round(extent.width * scale)), h = Math.max(2, Math.round(extent.height * scale));
      raster.width = w; raster.height = h;
      ctx.setTransform(w / extent.width, 0, 0, h / extent.height, pad * w / extent.width, pad * h / extent.height);
      ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath(); ctx.fill();
      const rgba = ctx.getImageData(0, 0, w, h).data;
      const mask = new Uint8Array(w * h); for (let i = 0; i < mask.length; i++) mask[i] = rgba[i * 4 + 3] > 127 ? 1 : 0;
      const distances = distanceField(mask, w, h), bytes = new Uint8Array(w * h * 4);
      for (let i = 0; i < distances.length; i++) {
        const encoded=Math.round(Math.max(0,Math.min(1,.5+distances[i]/scale/1200))*65535);
        bytes[i*4]=encoded>>8;bytes[i*4+1]=encoded&255;bytes[i*4+3]=255;
      }
      gl.bindTexture(gl.TEXTURE_2D, texture); gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
    },
    draw(seconds: number, strength: number) {
      if (disposed || gl.isContextLost()) return false;
      const scale = Math.min(Math.min(window.devicePixelRatio || 1, GAS_MATERIAL.maxDpr), GAS_MATERIAL.maxResolution / Math.max(extent.width, extent.height));
      const w = Math.max(2, Math.round(extent.width * scale)), h = Math.max(2, Math.round(extent.height * scale));
      if (output.width !== w || output.height !== h) { output.width = w; output.height = h; }
      gl.viewport(0, 0, w, h); gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, renderer.buffer);
      const position = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture); gl.uniform1i(uniform('field'), 0);
      gl.uniform2f(uniform('extent'), extent.width, extent.height);
      for (const [name, value] of Object.entries({ time: seconds, strength, amplitude: GAS_MATERIAL.amplitude, secondary: GAS_MATERIAL.secondaryAmplitude, bandWidth: GAS_MATERIAL.width, wavelength: GAS_MATERIAL.wavelength, speed: GAS_MATERIAL.speed, curl: GAS_MATERIAL.curl, fog: GAS_MATERIAL.fogOpacity })) gl.uniform1f(uniform(name), value);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      // Copy the just-rendered lower-left tile synchronously, before WebGL's
      // drawing buffer can be discarded. Different instances never resize it.
      target.clearRect(0, 0, w, h); target.drawImage(canvas, 0, canvas.height-h, w, h, 0, 0, w, h);
      return true;
    },
    dispose() {
      if (disposed) return; disposed = true; gl.deleteTexture(texture);
      if (--renderer.users === 0) { gl.deleteBuffer(renderer.buffer); gl.deleteProgram(program); gl.getExtension('WEBGL_lose_context')?.loseContext(); if (shared === renderer) shared = undefined; }
      output.width = output.height = 1; raster.width = raster.height = 1;
    },
  };
}
