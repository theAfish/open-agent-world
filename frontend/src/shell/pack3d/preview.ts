import type { PackRenderOptions } from './types';

export const packModelKey = (o: PackRenderOptions) => JSON.stringify([
  o.id, o.name, o.description, o.edition, o.packaging, o.color, o.count, o.countLabel, o.icon, o.artwork, o.issue, o.cards,
]);
export const packViewKey = (o: PackRenderOptions) => JSON.stringify([
  o.opened, o.revealing, o.finishVisible, o.reducedMotion,
  o.view.yaw, o.view.pitch, o.view.light, o.view.surface, o.view.opening,
]);
export const packPreviewKey = (o: PackRenderOptions) => `${packModelKey(o)}:${packViewKey(o)}`;

// 2D snapshots survive a view's WebGL resources. Bound count and decoded size.
const previews = new Map<string, HTMLCanvasElement>();
const MAX_BYTES = 12 * 1024 * 1024;
let bytes = 0;
export function rememberPackPreview(key: string, source: HTMLCanvasElement) {
  const size = source.width * source.height * 4;
  if (size > MAX_BYTES) return;
  const previous = previews.get(key);
  if (previous) { bytes -= previous.width * previous.height * 4; previews.delete(key); }
  const copy = document.createElement('canvas');
  copy.width = source.width; copy.height = source.height;
  const context = copy.getContext('2d');
  if (!context) return;
  context.drawImage(source, 0, 0);
  previews.set(key, copy); bytes += size;
  while (bytes > MAX_BYTES || previews.size > 24) {
    const oldest = previews.keys().next().value!;
    const image = previews.get(oldest)!;
    bytes -= image.width * image.height * 4; previews.delete(oldest);
    image.width = image.height = 0;
  }
}
export function restorePackPreview(key: string, target: HTMLCanvasElement) {
  const image = previews.get(key), context = image && target.getContext('2d');
  if (!image || !context) return false;
  target.width = image.width; target.height = image.height;
  context.drawImage(image, 0, 0);
  previews.delete(key); previews.set(key, image);
  return true;
}
