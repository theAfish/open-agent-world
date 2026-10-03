import { useEffect, useRef } from 'react';
import type { CardFinish } from './cardFinish';
import type { CardFinishQuality, CardFinishSurface } from './CardFinishLayer';
import { drawCardMaterial, MATERIAL_LIGHT_EVENT, type MaterialLight } from './cardMaterialRenderer';

/** Layout coordinates exclude deck rotation and React Flow zoom. Sampling a screen-space
 * bounding box made the same print change shape between a fanned hand and the world. */
function layoutOrigin(element: HTMLElement) {
  let x = 0, y = 0;
  for (let node: HTMLElement | null = element; node; node = node.offsetParent as HTMLElement | null) {
    x += node.offsetLeft;
    y += node.offsetTop;
  }
  return { x, y };
}

export function CardMaterialCanvas({ finish, quality, surface }: {
  finish: Exclude<CardFinish, 'normal'>; quality: CardFinishQuality; surface: CardFinishSurface;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const layer = canvas?.parentElement;
    const host = layer?.closest<HTMLElement>('.card-finish-surface');
    if (!canvas || !layer || !host || typeof ResizeObserver === 'undefined' || typeof IntersectionObserver === 'undefined') return;
    let visible = false, pending: number | null = null;
    let light: MaterialLight = { x: 0, y: 0 };
    let width = 0, height = 0, artEnd = 1;
    let glass: [number, number, number, number] = [0,0,0,0];
    let print: [number, number, number, number] = [1,1,0,0];
    const paint = () => {
      pending = null;
      if (!visible || width < 1 || height < 1) return;
      const ready = drawCardMaterial(canvas, { finish,width,height,...light,artEnd,glass,print });
      layer.toggleAttribute('data-material-ready',ready);
    };
    const queue = () => { if (visible && pending === null) pending = requestAnimationFrame(paint); };
    const measure = () => {
      const w = layer.offsetWidth, h = layer.offsetHeight;
      if (!w || !h) return;
      // Small cards use the same shader at a bounded resolution, not a different print.
      const small = quality === 'thumbnail';
      const chrome = surface === 'chrome';
      // A titlebar is a crop of 320x400 stock, not a portrait squeezed into a ribbon.
      // Give wide chrome its own budget: a thumbnail's 256px cap left only 8–15 rows.
      const scale = Math.min(small && !chrome ? 2 : Math.min(window.devicePixelRatio || 1,1.5),
        (chrome ? 1536 : small ? 256 : 640)/w, (chrome ? 160 : small ? 320 : 800)/h);
      print = chrome ? [w/320,h/400,0,.15] : [1,1,0,0];
      width = Math.max(1,Math.round(w*scale));
      height = Math.max(1,Math.round(h*scale));
      const origin = layoutOrigin(layer);
      const copy = host.querySelector<HTMLElement>('.card-face-copy')
        ?? (host.matches('.world-card.is-node, .world-card.is-preview')
          ? host.querySelector<HTMLElement>('.card-title-group, .card-lod-title, .card-lod-symbol > strong') : null);
      artEnd = copy ? Math.max(0,Math.min(1,(layoutOrigin(copy).y-origin.y-4)/h)) : 1;
      const badge = host.querySelector<HTMLElement>('.card-face-symbol, .card-kind-icon, .card-lod-kind, .workspace-app-mark');
      const badgeOrigin = badge ? layoutOrigin(badge) : origin;
      glass = badge ? [(badgeOrigin.x-origin.x)/w,(badgeOrigin.y-origin.y)/h,
        badge.offsetWidth/w,badge.offsetHeight/h] : [0,0,0,0];
      layer.style.setProperty('--finish-copy-start',`${artEnd*100}%`);
      queue();
    };
    const update = (event: Event) => {
      light = (event as CustomEvent<MaterialLight>).detail;
      // The pointer hook already coalesces moves into one frame; do not add a second frame of latency.
      if (pending !== null) cancelAnimationFrame(pending);
      paint();
    };
    const resize = new ResizeObserver(measure);
    resize.observe(layer);
    host.querySelectorAll('.card-face-copy, .card-title-group, .card-kind-icon, .card-face-symbol, .card-lod-title, .card-lod-kind')
      .forEach(element => resize.observe(element));
    const intersection = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      if (visible) queue();
      else if (pending !== null) { cancelAnimationFrame(pending); pending = null; }
    });
    intersection.observe(layer);
    host.addEventListener(MATERIAL_LIGHT_EVENT,update);
    measure();
    return () => {
      if (pending !== null) cancelAnimationFrame(pending);
      resize.disconnect(); intersection.disconnect();
      host.removeEventListener(MATERIAL_LIGHT_EVENT,update);
      layer.removeAttribute('data-material-ready');
      layer.style.removeProperty('--finish-copy-start');
    };
  }, [finish, quality, surface]);
  return <canvas ref={ref} className="card-material-canvas" aria-hidden="true" />;
}
