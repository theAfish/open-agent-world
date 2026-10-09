import type { ProductionMask } from './cardProduction';
import { createMaterialMask, type MaterialMask } from './cardMaterialMask';
import { paintFactoryInkMask } from './factoryInkMask';

/** PNG greyscale includes alpha, so transparent white never becomes an opaque plate. */
export function productionMaskValue(r: number, g: number, b: number, a: number, channel: ProductionMask['channel'], invert: boolean) {
  const coverage = channel === 'alpha' ? a / 255 : (r * .2126 + g * .7152 + b * .0722) / 255 * a / 255;
  return invert ? 1 - coverage : coverage;
}

/** Every pass owns a coverage plate. Explicit masks intentionally have no text knockout. */
export function createProductionLayerMask(host: HTMLElement, layer: HTMLElement, width: number, height: number, definition: ProductionMask, png?: HTMLImageElement): MaterialMask {
  const make = () => { const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height; return canvas; };
  const regions = make(), protection = make(), coverage = make();
  const context = coverage.getContext('2d')!;
  const w = Math.max(1, layer.offsetWidth), h = Math.max(1, layer.offsetHeight);
  context.scale(width / w, height / h); context.fillStyle = '#fff'; context.strokeStyle = '#fff';
  if (definition.source === 'all') context.fillRect(0, 0, w, h);
  else if (definition.source === 'png') {
    if (png?.complete && png.naturalWidth) {
      const fit = definition.fit ?? 'contain';
      const scale = (fit === 'cover' ? Math.max : Math.min)(w / png.naturalWidth, h / png.naturalHeight);
      const imageWidth = fit === 'stretch' ? w : png.naturalWidth * scale, imageHeight = fit === 'stretch' ? h : png.naturalHeight * scale;
      context.drawImage(png, (w - imageWidth) / 2, (h - imageHeight) / 2, imageWidth, imageHeight);
    }
  } else if (definition.source === 'preset') {
    const margin = Math.min(w, h) * .055, line = Math.max(1, w * .008);
    context.lineWidth = line;
    if (definition.preset === 'border') {
      context.beginPath(); context.roundRect(margin, margin, w - margin * 2, h - margin * 2, Math.min(w, h) * .035); context.stroke();
      context.lineWidth = line * .4;
      context.beginPath(); context.roundRect(margin + line * 3, margin + line * 3, w - margin * 2 - line * 6, h - margin * 2 - line * 6, Math.min(w, h) * .025); context.stroke();
    } else if (definition.preset === 'corners') {
      for (const [x, y, sx, sy] of [[margin, margin, 1, 1], [w - margin, margin, -1, 1], [margin, h - margin, 1, -1], [w - margin, h - margin, -1, -1]]) {
        context.beginPath(); context.moveTo(x, y + sy * margin * 2.4); context.lineTo(x, y); context.lineTo(x + sx * margin * 2.4, y); context.stroke();
        context.beginPath(); context.arc(x + sx * margin * .7, y + sy * margin * .7, margin * .12, 0, Math.PI * 2); context.fill();
      }
    } else if (definition.preset === 'diagonal') {
      context.lineWidth = Math.max(1, w * .035);
      for (let x = -h; x < w + h; x += w * .14) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x + h, h); context.stroke(); }
    } else {
      const spacing = w * .065;
      for (let y = spacing / 2, row = 0; y < h; y += spacing, row++) for (let x = spacing / 2 + row % 2 * spacing / 2; x < w; x += spacing) {
        context.beginPath(); context.arc(x, y, Math.max(1, w * .009), 0, Math.PI * 2); context.fill();
      }
    }
  } else if (definition.source === 'accents' || definition.source === 'frame') {
    const legacy = createMaterialMask(host, layer, width, height);
    const source = legacy.regions.getContext('2d')!.getImageData(0, 0, width, height);
    const channel = definition.source === 'frame' ? 1 : 2;
    for (let i = 0; i < source.data.length; i += 4) {
      source.data[i + 3] = source.data[i + channel];
      source.data[i] = source.data[i + 1] = source.data[i + 2] = 255;
    }
    context.putImageData(source, 0, 0);
  } else paintFactoryInkMask(host, layer, context, definition);

  const regionContext = regions.getContext('2d')!, pixels = context.getImageData(0, 0, width, height);
  const channel = definition.source === 'png' ? definition.channel : 'alpha';
  for (let i = 0; i < pixels.data.length; i += 4) {
    const value = productionMaskValue(pixels.data[i], pixels.data[i + 1], pixels.data[i + 2], pixels.data[i + 3], channel, definition.invert) * 255;
    pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value; pixels.data[i + 3] = 255;
  }
  regionContext.putImageData(pixels, 0, 0);
  const protect = protection.getContext('2d')!; protect.fillStyle = '#000'; protect.fillRect(0, 0, width, height);
  return { regions, protection };
}
