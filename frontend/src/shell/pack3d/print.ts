import { CanvasTexture, NoColorSpace, SRGBColorSpace, Color } from 'three';
import type { PackRenderCard, PackRenderOptions } from './types';

const INK = '#19353c';
const FONT = 'Georgia, "Noto Serif SC", "Songti SC", SimSun, serif';
const SANS = '"Segoe UI", "Microsoft YaHei", sans-serif';
type Mode = 'color' | 'roughness' | 'height';
function canvas(w: number, h: number) {
  const element = document.createElement('canvas'); element.width = w; element.height = h;
  return element;
}
function spaced(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, gap: number) {
  const chars = [...text], widths = chars.map(char => ctx.measureText(char).width);
  let left = x - (widths.reduce((a, b) => a + b, 0) + (chars.length - 1) * gap) / 2;
  ctx.textAlign = 'left';
  chars.forEach((char, i) => { ctx.fillText(char, left, y); left += widths[i] + gap; });
  ctx.textAlign = 'center';
}
function wrapped(ctx: CanvasRenderingContext2D, text: string, width: number) {
  const lines: string[] = []; let line = '';
  // Segment at word boundaries for Latin scripts, at characters for CJK.
  const pieces = text.match(/[\u3400-\u9fff]|[^\u3400-\u9fff\s]+\s*|\s+/gu) ?? [];
  for (const piece of pieces) {
    if (ctx.measureText(line + piece).width <= width) { line += piece; continue; }
    if (line.trim()) lines.push(line.trim());
    line = '';
    for (const char of piece) {
      if (ctx.measureText(line + char).width > width) { lines.push(line); line = ''; }
      line += char;
    }
  }
  if (line.trim()) lines.push(line.trim());
  return lines;
}
function paragraph(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, width: number, size: number, max: number, serif = false) {
  let lines: string[] = [];
  const minimum = size * .7;
  ctx.font = `${serif ? '600' : '400'} ${minimum}px ${serif ? FONT : SANS}`;
  const preferredLines = ctx.measureText(text).width <= width ? 1 : max;
  for (let current = size; current >= minimum; current -= 2) {
    ctx.font = `${serif ? '600' : '400'} ${current}px ${serif ? FONT : SANS}`;
    lines = wrapped(ctx, text, width); size = current;
    if (lines.length <= preferredLines) break;
  }
  if (lines.length > max) {
    lines = lines.slice(0, max); let last = lines[max - 1];
    while (ctx.measureText(last + '…').width > width && last.length) last = last.slice(0, -1);
    lines[max - 1] = last + '…';
  }
  ctx.textAlign = 'center';
  lines.forEach((line, i) => ctx.fillText(line, x, y + i * size * 1.24));
  return y + (lines.length - 1) * size * 1.24;
}
function brand(ctx: CanvasRenderingContext2D, x: number, y: number, mode: Mode) {
  ctx.fillStyle = mode === 'color' ? INK : mode === 'height' ? '#888' : '#eee';
  ctx.font = `600 40px ${SANS}`; spaced(ctx, 'OAW', x, y, 7);
  ctx.font = `400 15px ${SANS}`; spaced(ctx, 'OPEN AGENT WORLD', x, y + 36, 3.4);
}
function landscape(ctx: CanvasRenderingContext2D) {
  const ridge = (fill: string, points: number[]) => {
    ctx.fillStyle = fill; ctx.beginPath(); ctx.moveTo(0, 1030);
    for (let i = 0; i < points.length; i += 2) ctx.lineTo(points[i], points[i + 1]);
    ctx.lineTo(768, 1030); ctx.closePath(); ctx.fill();
  };
  ridge('#b3c9d0', [0, 828, 116, 722, 186, 768, 348, 598, 398, 685, 435, 674, 638, 824, 768, 680]);
  ridge('#e1e7de', [197, 769, 348, 598, 307, 704, 338, 681, 355, 743, 403, 747, 444, 822]);
  ridge('#d1cdb4', [0, 925, 193, 821, 265, 897, 528, 747, 555, 794, 768, 809]);
  ridge('#859f9f', [0, 887, 102, 927, 357, 806, 500, 903, 768, 821]);
  ridge('#bac4b2', [0, 982, 246, 906, 376, 944, 647, 874, 768, 906]);
}
function paintFront(ctx: CanvasRenderingContext2D, options: PackRenderOptions, mode: Mode, icon?: HTMLImageElement, artwork?: HTMLImageElement) {
  const { packaging: p } = options;
  const base = new Color(options.color).lerp(new Color('#f4f4e9'), p === 'premium' ? .36 : .22).getStyle();
  ctx.clearRect(0, 0, 768, 1088);
  ctx.fillStyle = mode === 'color' ? base : mode === 'roughness' ? (p === 'paper' ? '#ededed' : '#bbb') : '#808080';
  ctx.fillRect(0, 0, 768, 1088);
  if (mode === 'color' && options.issue) {
    for (let y = 0; y < 1088; y += 64) for (let x = 0; x < 768; x += 64) {
      ctx.fillStyle = (x / 64 + y / 64) % 2 ? '#211b28' : '#af23d0'; ctx.fillRect(x, y, 64, 64);
    }
  } else if (mode === 'color' && artwork) {
    const scale = Math.max(768 / artwork.width, 1088 / artwork.height);
    ctx.drawImage(artwork, (768 - artwork.width * scale) / 2, (1088 - artwork.height * scale) / 2, artwork.width * scale, artwork.height * scale);
    ctx.globalAlpha = .74; ctx.fillStyle = base; ctx.fillRect(0, 0, 768, 1088); ctx.globalAlpha = 1;
  } else if (p === 'collector' && mode === 'color') landscape(ctx);
  const center = p === 'premium' ? 350 : 384;
  if (p !== 'paper' && p !== 'collector' && !options.issue) {
    ctx.strokeStyle = mode === 'color' ? '#f7ffff66' : mode === 'height' ? '#8a8a8a' : '#b0b0b0';
    ctx.lineWidth = 1.3;
    for (let radius = 132; radius < 670; radius += 48) { ctx.beginPath(); ctx.ellipse(center, 507, radius, radius * 1.12, -.18, 0, Math.PI * 2); ctx.stroke(); }
  }
  if (p !== 'paper') brand(ctx, center, p === 'collector' ? 135 : 171, mode);
  const emblemY = p === 'paper' ? 498 : p === 'collector' ? 356 : 432;
  ctx.strokeStyle = mode === 'color' ? '#29484d55' : mode === 'height' ? '#b0b0b0' : '#777';
  ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(center, emblemY, 102, 0, Math.PI * 2); ctx.stroke();
  if (mode === 'color' && icon && !options.issue) ctx.drawImage(icon, center - 71, emblemY - 71, 142, 142);
  else if (mode !== 'color' && icon) {
    ctx.save(); ctx.globalAlpha = .14; ctx.drawImage(icon, center - 71, emblemY - 71, 142, 142); ctx.restore();
  }
  ctx.fillStyle = mode === 'color' ? options.issue ? '#ffffff' : INK : mode === 'height' ? '#878787' : '#ddd';
  if (options.issue && mode === 'color') { ctx.font = `600 40px ${SANS}`; ctx.textAlign = 'center'; ctx.fillText(options.issue.toUpperCase(), center, emblemY + 15); }
  const titleY = p === 'paper' ? 677 : p === 'collector' ? 525 : 616;
  const last = paragraph(ctx, options.name, center, titleY, p === 'premium' ? 550 : 618, 95, 2, true);
  ctx.fillStyle = mode === 'color' ? options.issue ? '#fff' : '#385259' : mode === 'height' ? '#838383' : '#999';
  paragraph(ctx, options.description, center, last + 47, p === 'premium' ? 495 : 540, 29, 2);
  if (p === 'collector' && mode === 'color') { ctx.fillStyle = '#e7ebe5'; ctx.fillRect(0, 938, 768, 150); }
  ctx.fillStyle = mode === 'color' ? options.issue ? '#fff' : INK : mode === 'height' ? '#878787' : '#ddd';
  ctx.textAlign = 'left'; ctx.font = `500 80px ${SANS}`;
  const count = options.count === null ? '—' : String(options.count).padStart(2, '0');
  ctx.fillText(count, 76, p === 'collector' ? 1015 : 955);
  const countWidth = ctx.measureText(count).width;
  ctx.font = `500 22px ${SANS}`; ctx.fillText(options.countLabel, 87 + countWidth, p === 'collector' ? 1014 : 954);
  ctx.font = `500 13px ${SANS}`; ctx.textAlign = 'right';
  ctx.fillText('OPEN AGENT', p === 'premium' ? 611 : 688, p === 'collector' ? 996 : 936);
  ctx.fillText('WORLD', p === 'premium' ? 611 : 688, p === 'collector' ? 1019 : 959);
}

function image(url?: string): Promise<HTMLImageElement | undefined> {
  if (!url) return Promise.resolve(undefined);
  return new Promise(resolve => { const asset = new Image(); asset.crossOrigin = 'anonymous'; asset.onload = () => resolve(asset); asset.onerror = () => resolve(undefined); asset.src = url; });
}
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
  void Promise.all([image(options.icon), image(options.issue ? undefined : options.artwork), document.fonts?.ready]).then(([icon, art]) => {
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

export function cardPrint(card: PackRenderCard, invalidate: () => void) {
  let disposed = false;
  const element = canvas(256, 352), ctx = element.getContext('2d')!;
  ctx.fillStyle = '#f7f5eb'; ctx.fillRect(0, 0, 256, 352);
  ctx.fillStyle = card.color ?? '#adcad0'; ctx.beginPath(); ctx.roundRect(14, 14, 228, 188, 12); ctx.fill();
  ctx.fillStyle = INK; paragraph(ctx, card.label, 128, 249, 222, 24, 2, true);
  ctx.font = `9px ${SANS}`; spaced(ctx, 'OPEN AGENT WORLD', 128, 329, 1.5);
  const map = texture(element);
  void image(card.icon).then(icon => { if (disposed || !icon) return; ctx.drawImage(icon, 95, 77, 66, 66); map.needsUpdate = true; invalidate(); });
  return { map, dispose() { disposed = true; map.dispose(); } };
}
