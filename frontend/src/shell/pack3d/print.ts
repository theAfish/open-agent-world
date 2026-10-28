import { CanvasTexture, NoColorSpace, SRGBColorSpace } from 'three';
import type { PackRenderOptions } from './types';
import { brand, canvas, image, INK, paintFront, paragraph, SANS, spaced } from './artwork';

function texture(element: HTMLCanvasElement, color = true) {
  const result = new CanvasTexture(element); result.colorSpace = color ? SRGBColorSpace : NoColorSpace; result.anisotropy = 4;
  return result;
}

export function createPrint(options: PackRenderOptions, invalidate: () => void) {
  let disposed = false;
  const frontCanvas = canvas(768, 1088), roughCanvas = canvas(256, 384), bumpCanvas = canvas(256, 384);
  const front = texture(frontCanvas), roughness = texture(roughCanvas, false), bump = texture(bumpCanvas, false);
  const draw = (icon?: HTMLImageElement, artwork?: HTMLImageElement) => {
    for (const [element, map, mode] of [[frontCanvas, front, 'color'], [roughCanvas, roughness, 'roughness'], [bumpCanvas, bump, 'height']] as const) {
      const ctx = element.getContext('2d')!; ctx.save(); ctx.scale(element.width / 768, element.height / 1088);
      paintFront(ctx, options, mode, icon, artwork); ctx.restore(); map.needsUpdate = true;
    }
  };
  draw();
  void Promise.all([image(options.icon), image(options.artwork), document.fonts?.ready]).then(([icon, art]) => {
    if (disposed) return; draw(icon, art); invalidate();
  });
  const backCanvas = canvas(384, 544), backCtx = backCanvas.getContext('2d')!; backCtx.scale(.5, .5);
  backCtx.fillStyle = options.color; backCtx.fillRect(0, 0, 768, 1088); brand(backCtx, 384, 180, 'color');
  backCtx.fillStyle = INK; paragraph(backCtx, options.name, 384, 351, 575, 48, 2, true);
  paragraph(backCtx, options.description, 384, 486, 530, 25, 4);
  backCtx.strokeStyle = '#3a5b622f'; backCtx.beginPath(); backCtx.moveTo(136, 659); backCtx.lineTo(632, 659); backCtx.stroke();
  backCtx.font = `400 18px ${SANS}`; ['PLAY', 'EXPLORE', 'CREATE', 'TOGETHER'].forEach((text, i) => spaced(backCtx, text, 384, 727 + i * 33, 5));
  paragraph(backCtx, options.edition, 384, 957, 555, 18, 2);
  const back = texture(backCanvas);
  const spineCanvas = canvas(128, 544), spineCtx = spineCanvas.getContext('2d')!;
  spineCtx.fillStyle = options.color; spineCtx.fillRect(0, 0, 128, 544);
  spineCtx.translate(64, 272); spineCtx.rotate(Math.PI / 2); spineCtx.font = `500 15px ${SANS}`; spineCtx.fillStyle = INK; spaced(spineCtx, 'OPEN AGENT WORLD', 0, 5, 3);
  const spine = texture(spineCanvas);
  const flapCanvas = canvas(512, 216), flapCtx = flapCanvas.getContext('2d')!;
  flapCtx.fillStyle = options.color; flapCtx.fillRect(0, 0, 512, 216); flapCtx.scale(512 / 768, 512 / 768); brand(flapCtx, 384, 103, 'color');
  const flap = texture(flapCanvas);
  return { front, back, spine, flap, roughness, bump,
    dispose() { disposed = true; for (const map of [front, back, spine, flap, roughness, bump]) map.dispose(); } };
}
