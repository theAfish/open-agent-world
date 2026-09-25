import type { TerrainChunkGeometry } from './terrain';
import { terrainRasterSpec } from './terrainRaster';

export interface TerrainRasterRequest {
  id: number;
  chunk: TerrainChunkGeometry;
  zoom: number;
  dpr: number;
  stroke: string;
  fill: string;
}

export interface TerrainRasterResponse {
  id: number;
  bitmap?: ImageBitmap;
  error?: string;
}

self.onmessage = (event: MessageEvent<TerrainRasterRequest>) => {
  const { id, chunk, zoom, dpr, stroke, fill } = event.data;
  try {
    const spec = terrainRasterSpec(zoom, dpr);
    const canvas = new OffscreenCanvas(spec.pixels, spec.pixels);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('OffscreenCanvas 2D unavailable');
    context.setTransform(spec.scale, 0, 0, spec.scale, spec.padding, spec.padding);
    context.fillStyle = fill;
    for (const path of chunk.fillPaths) if (path) context.fill(new Path2D(path), 'evenodd');
    context.strokeStyle = stroke;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.lineWidth = 1.15 / zoom;
    context.globalAlpha = 0.72;
    if (chunk.minorPath) context.stroke(new Path2D(chunk.minorPath));
    context.lineWidth = 1.65 / zoom;
    context.globalAlpha = 1;
    if (chunk.majorPath) context.stroke(new Path2D(chunk.majorPath));
    const bitmap = canvas.transferToImageBitmap();
    // Release the worker's blank replacement backing before starting another job.
    canvas.width = 1;
    canvas.height = 1;
    self.postMessage({ id, bitmap } satisfies TerrainRasterResponse, { transfer: [bitmap] });
  } catch (error) {
    self.postMessage({ id, error: String(error) } satisfies TerrainRasterResponse);
  }
};
