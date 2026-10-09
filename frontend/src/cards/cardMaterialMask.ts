import { paintFactoryInkMask } from './factoryInkMask';

/** Immutable pair of RGB masks. R/G/B = artwork/frame/accent; protection R = exclusion.
 * Separate opaque textures avoid losing region values to canvas premultiplied alpha. */
export interface MaterialMask { regions: HTMLCanvasElement; protection: HTMLCanvasElement }
export function layoutOrigin(element: HTMLElement) {
  let x=0, y=0;
  for (let node: HTMLElement | null=element; node; node=node.offsetParent as HTMLElement | null) {
    x+=node.offsetLeft; y+=node.offsetTop;
  }
  return { x,y };
}
const ART='[data-material-region="artwork"], [data-material-layer="artwork"], .card-face-art, .factory-art-element[data-kind="illustration"]';
const FRAME='[data-material-region="frame"]';
const ACCENT='[data-material-region="accent"]';
// A knockout excludes coating; transparent top-print ink is composited above it
// and must NOT punch a rectangular hole in the laminate behind the information.
// Legacy adapters for explicitly backed print remain confined to this module.
const PROTECTED='[data-material-layer="protected"], [data-material-region="text"], [data-material-region="background"], [data-material-region="icon"], .card-face-copy, .card-face-detail, .card-face-badge, .card-face-corner, .card-face-symbol, .factory-art-element:not([data-kind="illustration"]), hr, [role="separator"]';
const CONTROLS='button, input, textarea, select, [contenteditable="true"]';
export const MATERIAL_LAYOUT_PARTS=[ART,FRAME,ACCENT,PROTECTED,CONTROLS].join(', ');

export function createMaterialMask(host: HTMLElement, layer: HTMLElement, width: number, height: number): MaterialMask {
  const make=() => { const c=document.createElement('canvas'); c.width=width; c.height=height; return c; };
  const regions=make(), protection=make();
  const ctx=regions.getContext('2d')!, protect=protection.getContext('2d')!;
  const w=Math.max(1,layer.offsetWidth), h=Math.max(1,layer.offsetHeight), origin=layoutOrigin(layer);
  for (const context of [ctx,protect]) {
    context.scale(width/w,height/h); context.fillStyle='#000'; context.fillRect(0,0,w,h);
  }
  const select=(selector: string) => [...host.querySelectorAll<HTMLElement>(selector)]
    .filter(e => e.closest('.card-finish-surface')===host && !e.closest('[data-ink-source]') && e.offsetWidth && e.offsetHeight);
  const rect=(context: CanvasRenderingContext2D, element: HTMLElement, colour: string, pad=0) => {
    const pos=layoutOrigin(element), radius=parseFloat(getComputedStyle(element).borderTopLeftRadius)||0;
    context.beginPath();
    context.roundRect(pos.x-origin.x-pad,pos.y-origin.y-pad,element.offsetWidth+pad*2,element.offsetHeight+pad*2,radius+pad);
    context.fillStyle=colour; context.fill();
  };
  // Additive channels allow overlapping semantic regions without erasing one another.
  ctx.globalCompositeOperation='lighter';
  select(ART).forEach(e => rect(ctx,e,'#f00'));
  if (host.matches('.factory-artwork') && !host.querySelector(ART)) { ctx.fillStyle='#f00'; ctx.fillRect(0,0,w,h); }
  select(FRAME).forEach(e => rect(ctx,e,'#0f0'));
  select(ACCENT).forEach(e => rect(ctx,e,'#00f'));
  const radius=parseFloat(getComputedStyle(host).borderTopLeftRadius)||0;
  ctx.beginPath(); ctx.roundRect(.75,.75,Math.max(0,w-1.5),Math.max(0,h-1.5),Math.max(0,radius-.75));
  ctx.strokeStyle='#0f0'; ctx.lineWidth=1; ctx.stroke();
  // Protection always wins, independent of region order or material parameters.
  // Padding covers antialiased edges and texture filtering, including at thumbnail size.
  const factory = host.matches('.factory-artwork');
  select(PROTECTED).filter(e => !factory || !e.closest('[data-face-element]')).forEach(e => rect(protect,e,'#fff',Math.max(2,w/width*2)));
  if (factory) paintFactoryInkMask(host,layer,protect);
  select(CONTROLS).filter(e => {
    const style=getComputedStyle(e);
    return !(factory && e.closest('[data-face-element]')) && !e.closest('[data-material-layer="top-print"]') && style.visibility!=='hidden' && style.opacity!=='0';
  })
    .forEach(e => rect(protect,e,'#fff',Math.max(2,w/width*2)));
  // A nested surface owns its own masks and must not inherit the parent's coating.
  host.querySelectorAll<HTMLElement>('.card-finish-surface').forEach(e => {
    if (e.parentElement?.closest('.card-finish-surface')===host) rect(protect,e,'#fff',2);
  });
  return { regions,protection };
}
