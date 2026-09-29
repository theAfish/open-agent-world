import { CHUNK_SIZE, getViewportChunkBounds, getViewportChunkKeys } from '../state/chunks';
import type { FlowViewportState } from '../types/world';
import { CONTOUR_LEVELS, TERRAIN_RESOLUTION } from './terrain';
import type { TerrainRequest, TerrainResponse } from './terrain.worker';
import { TerrainTextureCache, TERRAIN_GPU_TILE_LIMIT, TERRAIN_GPU_TEXTURE_BUDGET } from './terrainTextureCache';
import { gridFragmentShader, gridVertexShader, terrainFragmentShader, terrainVertexShader } from './terrainShaders';

const MAX_BACKING_PIXELS = 16 * 1024 * 1024;
type RendererStatus = 'ready' | 'context-lost' | 'unavailable';
interface Program { program: WebGLProgram; uniforms: Map<string, WebGLUniformLocation>; }

function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): Program {
  const shaders: WebGLShader[] = [];
  const handle = gl.createProgram();
  if (!handle) throw new Error('Terrain program allocation failed');
  try {
    for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]] as const) {
      const shader = gl.createShader(type);
      if (!shader) throw new Error('Terrain shader allocation failed');
      shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? 'Terrain shader compilation failed');
      gl.attachShader(handle, shader);
    }
    gl.linkProgram(handle);
    if (!gl.getProgramParameter(handle, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(handle) ?? 'Terrain program linking failed');
    const uniforms = new Map<string, WebGLUniformLocation>();
    for (let i = 0; i < gl.getProgramParameter(handle, gl.ACTIVE_UNIFORMS); i++) {
      const info = gl.getActiveUniform(handle, i)!;
      const location = gl.getUniformLocation(handle, info.name);
      if (location) uniforms.set(info.name, location);
    }
    return { program: handle, uniforms };
  } catch (error) { gl.deleteProgram(handle); throw error; }
  finally { shaders.forEach(shader => gl.deleteShader(shader)); }
}

/** Parse theme tokens once on theme changes, never during camera movement. */
function themeColors(canvas: HTMLCanvasElement) {
  const style = getComputedStyle(canvas);
  const color = (name: string) => parseTerrainColor(style.getPropertyValue(name).trim());
  return { canvas: color('--canvas'), stroke: color('--contour'), fill: color('--contour-fill'), grid: color('--grid-dot') };
}

export function parseTerrainColor(value: string): Float32Array {
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length === 3 || hex.length === 4) hex = [...hex].map(c => c + c).join('');
    return new Float32Array([0, 2, 4, 6].map((offset) => offset < hex.length ? parseInt(hex.slice(offset, offset + 2), 16) / 255 : 1));
  }
  const rgb = value.match(/^rgba?\((.+)\)$/);
  if (!rgb) throw new Error(`Unsupported terrain theme color: ${value}`);
  const parts = rgb[1].split(/[\s,/]+/).filter(Boolean);
  return new Float32Array([0, 1, 2, 3].map(i => parts[i] === undefined ? 1
    : parseFloat(parts[i]) / (parts[i].endsWith('%') ? 100 : i === 3 ? 1 : 255)));
}

/** Imperative renderer: React owns mounting only; the camera never renders React. */
export class TerrainRendererWebGL {
  private gl: WebGL2RenderingContext;
  private terrain!: Program;
  private grid!: Program;
  private vao: WebGLVertexArrayObject | null = null;
  private cache: TerrainTextureCache;
  private worker: Worker;
  private view: FlowViewportState = { x: 0, y: 0, zoom: 1, width: 0, height: 0 };
  private seed: number | null = null;
  private serial = 0;
  private signature = '';
  private wanted = new Set<string>();
  private visible = new Set<string>();
  private frame = 0;
  private lost = false;
  private disposed = false;
  private failed = false;
  private colors: ReturnType<typeof themeColors>;
  private draws = 0;
  private requests = 0;
  private restores = 0;
  private maxDimension = 0;
  private effectiveDpr = 1;
  private lastStatus?: RendererStatus;

  constructor(private canvas: HTMLCanvasElement, private status: (status: RendererStatus, reason?: string) => void) {
    const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false,
      premultipliedAlpha: true, preserveDrawingBuffer: false, failIfMajorPerformanceCaveat: true });
    if (!gl) throw new Error('WebGL2 is unavailable');
    this.gl = gl;
    this.cache = new TerrainTextureCache(gl);
    this.colors = themeColors(canvas);
    this.initialize();
    // Reading diagnostics has no DOM mutations or per-frame React updates.
    Object.defineProperty(canvas, 'terrainStats', { configurable: true, get: () => this.report() });
    try { this.worker = new Worker(new URL('./terrain.worker.ts', import.meta.url), { type: 'module' }); }
    catch (error) { this.releasePrograms(); throw error; }
    this.worker.onmessage = (event: MessageEvent<TerrainResponse>) => {
      const response = event.data;
      if (this.disposed || this.failed || response.id !== this.serial) return;
      const key = `${response.tile.chunkX}:${response.tile.chunkY}`;
      if (!this.wanted.has(key)) return;
      try {
        if (!this.cache.add(response.tile, !this.lost)) return;
        if (this.visible.has(key)) this.schedule();
      } catch (error) {
        // Loss can precede its DOM event. Keep the CPU tile for restoration.
        if (!this.gl.isContextLost()) this.fail(String(error));
      }
    };
    this.worker.onerror = () => this.fail('Terrain worker failed');
    canvas.addEventListener('webglcontextlost', this.onLost);
    canvas.addEventListener('webglcontextrestored', this.onRestored);
  }

  private initialize() {
    const gl = this.gl;
    try {
      this.terrain = program(gl, terrainVertexShader, terrainFragmentShader);
      this.grid = program(gl, gridVertexShader, gridFragmentShader);
      this.vao = gl.createVertexArray();
      if (!this.vao) throw new Error('Terrain vertex array allocation failed');
      gl.bindVertexArray(this.vao);
      gl.disable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      const dimensions = gl.getParameter(gl.MAX_VIEWPORT_DIMS) as Int32Array;
      this.maxDimension = Math.min(gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), dimensions[0], dimensions[1]);
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    } catch (error) { this.releasePrograms(); throw error; }
  }

  setSeed(seed: number | null) {
    if (seed === this.seed) return;
    this.seed = seed; this.serial++; this.signature = '';
    this.cache.clear(); this.wanted.clear(); this.visible.clear();
    this.updateCoverage(); this.schedule();
  }

  setViewport(view: FlowViewportState) {
    this.view = view;
    this.updateCoverage();
    this.schedule();
  }

  refreshTheme() {
    try { this.colors = themeColors(this.canvas); this.schedule(); }
    catch (error) { this.fail(String(error)); }
  }
  refreshSize() { this.schedule(); }

  private updateCoverage() {
    const view = this.view;
    if (this.failed || this.seed === null || !view.width || !view.height) return;
    const b = getViewportChunkBounds(view, 0);
    const signature = `${b.minX}:${b.maxX}:${b.minY}:${b.maxY}`;
    if (signature === this.signature) return;
    this.signature = signature;
    // Visible tiles have priority; prefetch can shrink to respect the hard cap.
    const visible = getViewportChunkKeys(view, 0);
    if (visible.length > TERRAIN_GPU_TILE_LIMIT) { this.fail('Viewport exceeds terrain texture budget'); return; }
    this.visible = new Set(visible);
    const keys = [...visible, ...getViewportChunkKeys(view).filter(key => !this.visible.has(key))].slice(0, TERRAIN_GPU_TILE_LIMIT);
    this.wanted = new Set(keys);
    this.cache.protect(keys);
    const missing = keys.filter(key => !this.cache.entries.has(key));
    // Replacing the worker queue also cancels stale replies from old coverage.
    this.requests += missing.length;
    this.worker.postMessage({ id: ++this.serial, keys: missing,
      seed: this.seed, resolution: TERRAIN_RESOLUTION } satisfies TerrainRequest);
  }

  private schedule() {
    if (this.frame || this.lost || this.disposed || this.failed) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      try { this.draw(); } catch (error) { this.fail(String(error)); }
    });
  }

  private draw() {
    const { width, height, x, y, zoom } = this.view;
    if (!width || !height) return;
    const gl = this.gl;
    const dpr = Math.min(window.devicePixelRatio || 1, Math.sqrt(MAX_BACKING_PIXELS / (width * height)),
      this.maxDimension / width, this.maxDimension / height);
    const pixelsX = Math.max(1, Math.round(width * dpr)), pixelsY = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== pixelsX || this.canvas.height !== pixelsY) {
      this.canvas.width = pixelsX; this.canvas.height = pixelsY;
      gl.viewport(0, 0, pixelsX, pixelsY);
    }
    this.effectiveDpr = pixelsX / width;
    gl.bindVertexArray(this.vao);
    gl.disable(gl.BLEND);
    gl.useProgram(this.grid.program);
    const g = (name: string) => this.grid.uniforms.get(name)!;
    gl.uniform2f(g('uPixels'), pixelsX, pixelsY); gl.uniform2f(g('uViewport'), width, height);
    gl.uniform4fv(g('uCanvas'), this.colors.canvas); gl.uniform4fv(g('uGrid'), this.colors.grid);
    const lod = Math.max(0, Math.log2(18 / (24 * zoom)));
    const gap = 24 * 2 ** Math.floor(lod) * zoom;
    // Modulo in JS double precision keeps the grid stable at distant world coordinates.
    gl.uniform2f(g('uOffset'), x % (gap * 2), y % (gap * 2));
    gl.uniform1f(g('uGap'), gap); gl.uniform1f(g('uMinor'), 1 - (lod % 1));
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.terrain.program);
    const t = (name: string) => this.terrain.uniforms.get(name)!;
    gl.uniform2f(t('uViewport'), width, height); gl.uniform1f(t('uDpr'), this.effectiveDpr);
    gl.uniform4fv(t('uStroke'), this.colors.stroke); gl.uniform4fv(t('uFill'), this.colors.fill);
    gl.uniform3f(t('uLevels'), CONTOUR_LEVELS[0], CONTOUR_LEVELS[1] - CONTOUR_LEVELS[0], CONTOUR_LEVELS.length);
    gl.uniform1i(t('uField'), 0); gl.activeTexture(gl.TEXTURE0);
    // Rebase to a nearby chunk, avoiding large world coordinates in float uniforms.
    const originX = Math.floor(-x / zoom / CHUNK_SIZE) * CHUNK_SIZE;
    const originY = Math.floor(-y / zoom / CHUNK_SIZE) * CHUNK_SIZE;
    gl.uniform3f(t('uCamera'), x + originX * zoom, y + originY * zoom, zoom);
    for (const key of this.visible) {
      const entry = this.cache.entries.get(key);
      if (!entry?.texture) continue;
      const tile = entry.tile;
      gl.uniform2f(t('uTileOrigin'), tile.chunkX * CHUNK_SIZE - originX, tile.chunkY * CHUNK_SIZE - originY);
      gl.uniform1f(t('uResolution'), tile.resolution);
      gl.bindTexture(gl.TEXTURE_2D, entry.texture);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    this.draws++;
    this.notify('ready');
  }

  private report() {
    return {
      terrainRenderer: 'webgl2', tiles: this.cache.entries.size, textureBytes: this.cache.bytes,
      textureBudget: TERRAIN_GPU_TEXTURE_BUDGET, tileLimit: TERRAIN_GPU_TILE_LIMIT,
      texturePeakBytes: this.cache.peakBytes, uploads: this.cache.uploads, evictions: this.cache.evictions,
      requestedTiles: this.requests, pendingTiles: [...this.wanted].filter(key => !this.cache.entries.has(key)).length,
      visibleTiles: this.visible.size, coveredTiles: [...this.visible].filter(key => this.cache.entries.has(key)).length,
      draws: this.draws, restores: this.restores, contextLost: this.lost, effectiveDpr: this.effectiveDpr,
      backingBytes: this.canvas.width * this.canvas.height * 4, seed: this.seed,
    };
  }

  private notify(status: RendererStatus, reason?: string) {
    if (status === this.lastStatus) return;
    this.lastStatus = status; this.status(status, reason);
  }

  private onLost = (event: Event) => {
    event.preventDefault(); this.lost = true;
    cancelAnimationFrame(this.frame); this.frame = 0;
    this.cache.contextLost(); this.notify('context-lost');
  };

  private onRestored = () => {
    if (this.disposed || this.failed) return;
    try {
      this.initialize(); this.cache.restore(this.gl);
      this.lost = false; this.restores++;
      this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      this.schedule();
    } catch (error) { this.fail(String(error)); }
  };

  private fail(reason: string) {
    if (this.failed || this.disposed) return;
    this.failed = true; cancelAnimationFrame(this.frame); this.frame = 0;
    this.worker.terminate(); this.cache.clear(); this.releasePrograms();
    this.canvas.width = this.canvas.height = 1;
    this.notify('unavailable', reason);
  }

  private releasePrograms() {
    if (this.terrain) this.gl.deleteProgram(this.terrain.program);
    if (this.grid) this.gl.deleteProgram(this.grid.program);
    if (this.vao) this.gl.deleteVertexArray(this.vao);
    this.vao = null;
  }

  dispose() {
    this.disposed = true; cancelAnimationFrame(this.frame);
    this.worker.terminate(); this.cache.clear(); this.releasePrograms();
    this.canvas.width = this.canvas.height = 1;
    this.canvas.removeEventListener('webglcontextlost', this.onLost);
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored);
  }
}
