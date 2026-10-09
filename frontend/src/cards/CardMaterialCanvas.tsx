import { useEffect, useRef } from 'react';
import type { CardFinish } from './cardFinish';
import type { CardFinishQuality } from './CardFinishLayer';
import { drawCardMaterial, MATERIAL_LIGHT_EVENT, type MaterialLight } from './cardMaterialRenderer';
import { createMaterialMask, MATERIAL_LAYOUT_PARTS } from './cardMaterialMask';
import type { MaterialMask } from './cardMaterialMask';
import type { CardMaterial, MaterialEnvironment, MaterialDebugView } from './cardMaterial';
import type { CardProduction, PrintFinishing, ProductionLayer } from './cardProduction';
import { createProductionLayerMask } from './productionLayerMask';

export interface CardMaterialOptions {
  production?: CardProduction;
  finishing?: PrintFinishing;
  material?: CardMaterial;
  environment?: Partial<MaterialEnvironment>;
  debugView?: MaterialDebugView;
  backend?: 'auto' | 'fallback';
  pose?: { x: number; y: number };
  processLayer?: ProductionLayer;
}
export function CardMaterialCanvas({ finish, quality, restrained = false, roughness, material, finishing, environment, debugView, backend, pose, processLayer }: CardMaterialOptions & {
  finish: CardFinish; quality: CardFinishQuality;
  restrained?: boolean; roughness?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current, layer = canvas?.parentElement;
    const host = layer?.closest<HTMLElement>('.card-finish-surface');
    if (!canvas || !layer || !host || typeof ResizeObserver === 'undefined' || typeof IntersectionObserver === 'undefined') return;
    let visible = false, pending: number | null = null, dirty = true;
    let light: MaterialLight = { x: pose?.x ?? 0, y: pose?.y ?? 0, active: false };
    let target = light, lastPaint = 0;
    let mask: MaterialMask | undefined;
    let maskImage: HTMLImageElement | undefined;
    let width = 0, height = 0;
    const paint = (now = performance.now()) => {
      pending = null;
      if (!visible || !layer.offsetWidth || !layer.offsetHeight) return;
      const distance = Math.hypot(target.x-light.x, target.y-light.y);
      const blend = 1-Math.exp(-Math.min(80, Math.max(8, now-lastPaint))/55);
      light = distance < .002 || target.immediate ? target : { ...target,
        x: light.x+(target.x-light.x)*blend, y: light.y+(target.y-light.y)*blend };
      lastPaint = now;
      if (dirty) {
        const w = layer.offsetWidth, h = layer.offsetHeight, small = quality === 'thumbnail';
        const scale = Math.min(small ? 2 : Math.min(window.devicePixelRatio || 1, 1.5),
          (small ? 256 : 640)/w, (small ? 320 : 800)/h);
        width = Math.max(1, Math.round(w*scale)); height = Math.max(1, Math.round(h*scale));
        mask = processLayer ? createProductionLayerMask(host, layer, width, height, processLayer.mask, maskImage) : createMaterialMask(host, layer, width, height);
        dirty = false;
      }
      if (!mask) return;
      const renderer = drawCardMaterial(canvas, { finish, width, height, ...light, mask, restrained, roughness,
        material, finishing, environment, debugView, backend, processLayer, aspect: layer.offsetHeight/layer.offsetWidth });
      layer.toggleAttribute('data-material-ready', Boolean(renderer));
      if (renderer) layer.dataset.materialRenderer = renderer;
      const settled = light.x === target.x && light.y === target.y;
      layer.toggleAttribute('data-material-settled', settled);
      if (!settled) queue();
    };
    const queue = () => { if (visible && pending === null) pending = requestAnimationFrame(paint); };
    const measure = () => { dirty = true; layer.removeAttribute('data-material-settled'); queue(); };
    if (processLayer?.mask.source === 'png' && processLayer.mask.png) {
      maskImage = new Image(); maskImage.onload = measure; maskImage.onerror = measure; maskImage.src = processLayer.mask.png;
    }
    const update = (event: Event) => {
      if (pose) return;
      const detail = (event as CustomEvent<MaterialLight>).detail;
      if (!Number.isFinite(detail.x) || !Number.isFinite(detail.y)) return;
      target = { ...detail, x: Math.max(-1, Math.min(1, detail.x)), y: Math.max(-1, Math.min(1, detail.y)) };
      // Reduced motion stays at a static pose, including when preference changes.
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) target.immediate = true;
      layer.removeAttribute('data-material-settled');
      queue();
    };
    const resize = new ResizeObserver(measure);
    const observeLayout = () => {
      resize.disconnect(); resize.observe(layer);
      host.querySelectorAll(MATERIAL_LAYOUT_PARTS).forEach(element => resize.observe(element));
      measure();
    };
    const mutation = new MutationObserver(records => {
      // Pointer pose writes host CSS variables; those do not change print coordinates.
      if (records.some(record => record.attributeName !== 'style' || (record.target !== host && !layer.contains(record.target)))) observeLayout();
    });
    mutation.observe(host, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['data-material-region', 'data-material-layer', 'data-face-shape', 'class', 'hidden', 'style'] });
    const intersection = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      if (visible) { observeLayout(); queue(); }
      else {
        light = target;
        if (pending !== null) { cancelAnimationFrame(pending); pending = null; }
      }
    });
    intersection.observe(layer);
    host.addEventListener(MATERIAL_LIGHT_EVENT, update);
    host.addEventListener('load', measure, true);
    document.fonts?.addEventListener('loadingdone', measure);
    observeLayout();
    return () => {
      if (pending !== null) cancelAnimationFrame(pending);
      resize.disconnect(); intersection.disconnect(); mutation.disconnect();
      host.removeEventListener(MATERIAL_LIGHT_EVENT, update);
      host.removeEventListener('load', measure, true);
      document.fonts?.removeEventListener('loadingdone', measure);
      if (maskImage) { maskImage.onload = null; maskImage.onerror = null; }
      layer.removeAttribute('data-material-ready');
      layer.removeAttribute('data-material-renderer');
      layer.removeAttribute('data-material-settled');
    };
  }, [finish, quality, restrained, roughness, material, finishing, environment, debugView, backend, pose, processLayer]);
  return <canvas ref={ref} className="card-material-canvas" aria-hidden="true" />;
}
