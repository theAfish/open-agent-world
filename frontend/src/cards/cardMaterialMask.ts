/** A small RGB region map in untransformed layout coordinates:
 * red = artwork, green = frame, blue = icon rim, yellow = decorative accents.
 * Black is uncoated paper or protected copy. Alpha is opaque to preserve mask channels. */
export function layoutOrigin(element: HTMLElement) {
  let x = 0, y = 0;
  for (let node: HTMLElement | null = element; node; node = node.offsetParent as HTMLElement | null) {
    x += node.offsetLeft; y += node.offsetTop;
  }
  return { x, y };
}

const ART = '[data-material-region="artwork"], .card-face-art, .card-header, .card-lod-heading, .factory-art-element[data-kind="illustration"]';
const ICON = '[data-material-region="icon"], .card-face-symbol, .card-kind-icon, .card-lod-kind, .factory-art-element[data-kind="icon"]';
const ACCENT = '[data-material-region="accent"]';
const PROTECTED = '[data-material-region="text"], [data-material-region="background"], .card-face-copy, .card-face-detail, .card-face-badge, .card-face-corner, .card-title-group, .card-status, .card-lod-title, .card-lod-status, .card-lod-symbol > strong, .node-preview-content, .card-static-preview, .factory-art-element:not([data-kind="illustration"]):not([data-kind="icon"]), button, input, textarea, select, [contenteditable="true"], hr, [role="separator"]';
export const MATERIAL_LAYOUT_PARTS = [ART, ICON, ACCENT, PROTECTED].join(', ');

export function createMaterialMask(host: HTMLElement, layer: HTMLElement, width: number, height: number) {
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d')!;
  const w = layer.offsetWidth, h = layer.offsetHeight, origin = layoutOrigin(layer);
  ctx.scale(width / w, height / h);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, w, h);
  const select = (selector: string) => [...host.querySelectorAll<HTMLElement>(selector)]
    .filter(element => element.closest('.card-finish-surface') === host && element.offsetWidth && element.offsetHeight);
  const rect = (element: HTMLElement, colour: string, stroke = false, pad = 0) => {
    const pos = layoutOrigin(element);
    const radius = parseFloat(getComputedStyle(element).borderTopLeftRadius) || 0;
    ctx.beginPath();
    ctx.roundRect(pos.x-origin.x-pad, pos.y-origin.y-pad, element.offsetWidth+pad*2, element.offsetHeight+pad*2, radius);
    if (stroke) { ctx.strokeStyle = colour; ctx.lineWidth = 1; ctx.stroke(); }
    else { ctx.fillStyle = colour; ctx.fill(); }
  };
  select(ART).forEach(element => rect(element, '#f00'));
  // An explicitly materialized factory background may be coated, but ordinary paper is not.
  if (host.matches('.factory-artwork') && !host.querySelector(ART)) {
    ctx.fillStyle = '#f00'; ctx.fillRect(0, 0, w, h);
  }
  const radius = parseFloat(getComputedStyle(host).borderTopLeftRadius) || 0;
  ctx.beginPath();
  ctx.roundRect(.75, .75, Math.max(0,w-1.5), Math.max(0,h-1.5), Math.max(0,radius-.75));
  ctx.strokeStyle = '#0f0'; ctx.lineWidth = 1; ctx.stroke();
  select(ICON).forEach(element => {
    if (element.matches('.card-kind-icon, .card-lod-kind')) {
      const pos = layoutOrigin(element);
      const cx = pos.x-origin.x+element.offsetWidth/2, cy = pos.y-origin.y+element.offsetHeight/2;
      const radius = Math.max(element.offsetWidth,element.offsetHeight);
      const halo = ctx.createRadialGradient(cx,cy,radius*.36,cx,cy,radius*.85);
      halo.addColorStop(0,'#000'); halo.addColorStop(.4,'#000c'); halo.addColorStop(1,'#0000');
      ctx.fillStyle = halo; ctx.fillRect(cx-radius,cy-radius,radius*2,radius*2);
    } else { rect(element, '#000', false, 1); rect(element, '#00f', true); }
  });
  select(ACCENT).forEach(element => rect(element, '#ff0'));
  // Let the coating dissolve into the stock across the whole composition,
  // rather than cutting a white rectangular hole around the title.
  select('.card-title-group, .card-lod-title, .card-lod-symbol > strong, .card-face-copy').forEach(element => {
    const pos = layoutOrigin(element), bottom = pos.y-origin.y-2;
    const feather = Math.min(48, Math.max(22, h*.15));
    const gradient = ctx.createLinearGradient(0,bottom-feather,0,bottom);
    gradient.addColorStop(0,'#0000');
    gradient.addColorStop(.35,'#0002');
    gradient.addColorStop(.7,'#000a');
    gradient.addColorStop(1,'#000');
    ctx.fillStyle = gradient;
    ctx.fillRect(0,bottom-feather,w,h-bottom+feather);
  });
  // Exclusion is last and padded, so even antialiased text edges cannot pick up foil.
  select(PROTECTED).forEach(element => rect(element, '#000', false, 2));
  return canvas;
}
