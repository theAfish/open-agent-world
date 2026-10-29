import type { PackRenderOptions } from './types';

export const INK = '#19353c';
const FONT = 'Georgia, "Noto Serif SC", "Songti SC", SimSun, serif';
export const SANS = '"Segoe UI", "Microsoft YaHei", sans-serif';
type Mode = 'color' | 'roughness' | 'height';
export function canvas(w: number, h: number) {
  const element = document.createElement('canvas'); element.width = w; element.height = h;
  return element;
}
export function spaced(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, gap: number) {
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
export function paragraph(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, width: number, size: number, max: number, serif = false) {
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
export function brand(ctx: CanvasRenderingContext2D, x: number, y: number, mode: Mode) {
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
function faceMetadata(ctx: CanvasRenderingContext2D, options: PackRenderOptions) {
  ctx.save();
  if (options.version) {
    ctx.font = `500 46px ${SANS}`;
    let text = `v${options.version}`;
    while (ctx.measureText(text).width > 250 && text.length > 2) text = text.slice(0, -2) + '…';
    const width = ctx.measureText(text).width + 32;
    const right = options.packaging === 'premium' ? 635 : 700;
    const bottom = options.packaging === 'collector' ? 1030 : 973;
    ctx.fillStyle = '#f7f7efdd'; ctx.strokeStyle = '#29484d40'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.roundRect(right - width, bottom - 66, width, 66, 10); ctx.fill(); ctx.stroke();
    ctx.fillStyle = INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, right - width / 2, bottom - 31);
  }
  const notice = options.notice;
  if (notice) {
    const warning = notice.kind === 'warning';
    ctx.translate(384, warning ? 795 : 815);
    if (warning) ctx.rotate(-.23);
    ctx.shadowColor = notice.kind === 'update' ? '#6ce4b0' : '#182f3d55';
    ctx.shadowBlur = notice.kind === 'update' ? 36 : 12; ctx.shadowOffsetY = 8;
    if (notice.kind === 'update') {
      const glow = ctx.createLinearGradient(-460, 0, 460, 0);
      glow.addColorStop(0, '#245a58'); glow.addColorStop(.5, '#4a8b78'); glow.addColorStop(1, '#245a58');
      ctx.fillStyle = glow;
    } else ctx.fillStyle = warning ? '#efc46d' : notice.kind === 'restart' ? '#41677d' : '#526b70';
    ctx.fillRect(-600, -52, 1200, 104);
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    if (warning) {
      ctx.fillStyle = '#26383a';
      for (const y of [-52, 40]) for (let x = -620; x < 620; x += 48) {
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 24, y); ctx.lineTo(x + 12, y + 12); ctx.lineTo(x - 12, y + 12); ctx.closePath(); ctx.fill();
      }
    } else {
      ctx.fillStyle = '#d2f6ed70'; ctx.fillRect(-600, -52, 1200, 3); ctx.fillRect(-600, 49, 1200, 3);
    }
    ctx.fillStyle = warning ? '#26383a' : '#f2f8f5'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `600 46px ${SANS}`;
    const mark = notice.kind === 'restart' ? '↻ ' : notice.kind === 'update' ? '↑ ' : '';
    ctx.fillText(mark + notice.label.toUpperCase(), 0, 2, 650);
  }
  ctx.restore();
}
function paperColor(color: string, amount: number) {
  const linear = (value: number) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  const srgb = (value: number) => value <= .0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - .055;
  const channels = [244, 244, 233].map((paper, index) => {
    const value = linear(parseInt(color.slice(1 + index * 2, 3 + index * 2), 16) / 255);
    return Math.round(srgb(value + (linear(paper / 255) - value) * amount) * 255);
  });
  return `rgb(${channels.join(',')})`;
}
export function paintFront(ctx: CanvasRenderingContext2D, options: PackRenderOptions, mode: Mode, icon?: HTMLImageElement, artwork?: HTMLImageElement) {
  const { packaging: p } = options;
  const base = paperColor(options.color, p === 'premium' ? .36 : .22);
  ctx.clearRect(0, 0, 768, 1088);
  ctx.fillStyle = mode === 'color' ? base : mode === 'roughness' ? (p === 'paper' ? '#ededed' : '#bbb') : '#808080';
  ctx.fillRect(0, 0, 768, 1088);
  if (mode === 'color' && artwork) {
    const scale = Math.max(768 / artwork.width, 1088 / artwork.height);
    ctx.drawImage(artwork, (768 - artwork.width * scale) / 2, (1088 - artwork.height * scale) / 2, artwork.width * scale, artwork.height * scale);
    ctx.globalAlpha = .74; ctx.fillStyle = base; ctx.fillRect(0, 0, 768, 1088); ctx.globalAlpha = 1;
  } else if (p === 'collector' && mode === 'color') landscape(ctx);
  const center = p === 'premium' ? 350 : 384;
  if (p !== 'paper' && p !== 'collector') {
    ctx.strokeStyle = mode === 'color' ? '#f7ffff66' : mode === 'height' ? '#8a8a8a' : '#b0b0b0';
    ctx.lineWidth = 1.3;
    for (let radius = 132; radius < 670; radius += 48) { ctx.beginPath(); ctx.ellipse(center, 507, radius, radius * 1.12, -.18, 0, Math.PI * 2); ctx.stroke(); }
  }
  if (p !== 'paper') brand(ctx, center, p === 'collector' ? 135 : 171, mode);
  const emblemY = p === 'paper' ? 498 : p === 'collector' ? 356 : 432;
  ctx.strokeStyle = mode === 'color' ? '#29484d55' : mode === 'height' ? '#b0b0b0' : '#777';
  ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(center, emblemY, 102, 0, Math.PI * 2); ctx.stroke();
  if (mode === 'color' && icon) ctx.drawImage(icon, center - 71, emblemY - 71, 142, 142);
  else if (mode !== 'color' && icon) {
    ctx.save(); ctx.globalAlpha = .14; ctx.drawImage(icon, center - 71, emblemY - 71, 142, 142); ctx.restore();
  }
  ctx.fillStyle = mode === 'color' ? INK : mode === 'height' ? '#878787' : '#ddd';
  const titleY = p === 'paper' ? 677 : p === 'collector' ? 525 : 616;
  paragraph(ctx, options.name, center, titleY, p === 'premium' ? 550 : 618, 95, 2, true);
  if (p === 'collector' && mode === 'color') { ctx.fillStyle = '#e7ebe5'; ctx.fillRect(0, 938, 768, 150); }
  ctx.fillStyle = mode === 'color' ? INK : mode === 'height' ? '#878787' : '#ddd';
  ctx.textAlign = 'left'; ctx.font = `500 80px ${SANS}`;
  const count = options.count === null ? '—' : String(options.count).padStart(2, '0');
  ctx.fillText(count, 76, p === 'collector' ? 1015 : 955);
  const countWidth = ctx.measureText(count).width;
  ctx.font = `500 22px ${SANS}`; ctx.fillText(options.countLabel, 87 + countWidth, p === 'collector' ? 1014 : 954);
  if (!options.version) {
    ctx.font = `500 13px ${SANS}`; ctx.textAlign = 'right';
    ctx.fillText('OPEN AGENT', p === 'premium' ? 611 : 688, p === 'collector' ? 996 : 936);
    ctx.fillText('WORLD', p === 'premium' ? 611 : 688, p === 'collector' ? 1019 : 959);
  }
  if (mode === 'color') faceMetadata(ctx, options);
}

export function image(url?: string): Promise<HTMLImageElement | undefined> {
  if (!url) return Promise.resolve(undefined);
  return new Promise(resolve => { const asset = new Image(); asset.crossOrigin = 'anonymous'; asset.onload = () => resolve(asset); asset.onerror = () => resolve(undefined); asset.src = url; });
}
/** Flat bags use the same print artwork as the WebGL materials, without a 3D scene. */
export function paintPackCover(target: HTMLCanvasElement, options: PackRenderOptions) {
  const ctx = target.getContext('2d');
  if (!ctx) return;
  let disposed = false;
  const draw = (icon?: HTMLImageElement, artwork?: HTMLImageElement) => {
    target.width = 768; target.height = 1088;
    ctx.save();
    ctx.beginPath(); ctx.roundRect(20, 8, 728, 1072, 14); ctx.clip();
    ctx.translate(20, 8); ctx.scale(728 / 768, 1072 / 1088);
    paintFront(ctx, options, 'color', icon, artwork);
    if (options.packaging === 'paper') {
      ctx.fillStyle = options.color; ctx.strokeStyle = '#29484d33'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(768, 0); ctx.lineTo(768, 230);
      ctx.quadraticCurveTo(384, 390, 0, 230); ctx.closePath(); ctx.fill(); ctx.stroke();
      brand(ctx, 384, 108, 'color');
    }
    if (options.packaging === 'premium') {
      const foil = ctx.createLinearGradient(698, 0, 742, 900);
      for (const [stop, color] of [[0, '#d5dce0'], [.25, '#e7d0e6'], [.5, '#d5ece5'], [.75, '#ede2bf'], [1, '#d3d7ed']] as const) foil.addColorStop(stop, color);
      ctx.fillStyle = foil; ctx.fillRect(698, 0, 44, 1088);
    }
    ctx.restore();
    ctx.fillStyle = options.color; ctx.fillRect(20, 8, 728, 22); ctx.fillRect(20, 1058, 728, 22);
    ctx.strokeStyle = '#29484d44'; ctx.lineWidth = 2;
    for (let x = 28; x < 744; x += 10) {
      ctx.beginPath(); ctx.moveTo(x, 8); ctx.lineTo(x, 30); ctx.moveTo(x, 1058); ctx.lineTo(x, 1080); ctx.stroke();
    }
    ctx.beginPath(); ctx.roundRect(20, 8, 728, 1072, 14); ctx.stroke();
  };
  draw();
  void Promise.all([image(options.icon), image(options.artwork), document.fonts?.ready]).then(([icon, artwork]) => {
    if (!disposed) draw(icon, artwork);
  });
  return () => { disposed = true; };
}
