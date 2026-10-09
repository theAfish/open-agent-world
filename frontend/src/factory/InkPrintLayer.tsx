import { useEffect, useRef, type ReactNode } from 'react';
import type { ProductionLayer } from '../cards/cardProduction';
import { createProductionLayerMask } from '../cards/productionLayerMask';

/** DOM ink stays editable and keeps live controls; its coverage plate clips the actual print. */
export function InkPrintLayer({ layer, order, children, hidden = false }: {
  layer: ProductionLayer; order: number; children: ReactNode; hidden?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current, host = node?.closest<HTMLElement>('.card-finish-surface');
    if (!node || !host || typeof ResizeObserver === 'undefined') return;
    if (layer.mask.source === 'all' && !layer.mask.invert) {
      node.style.maskImage = 'none'; return;
    }
    const source = host.querySelector<HTMLElement>('[data-ink-source]') ?? host;
    let frame = 0, image: HTMLImageElement | undefined;
    const paint = () => {
      frame = 0;
      const w = node.offsetWidth, h = node.offsetHeight;
      if (!w || !h) return;
      const scale = Math.min(2, 800 / Math.max(w, h));
      const mask = createProductionLayerMask(host, node, Math.max(1, Math.round(w * scale)), Math.max(1, Math.round(h * scale)), layer.mask, image).regions;
      const ctx = mask.getContext('2d')!, pixels = ctx.getImageData(0, 0, mask.width, mask.height);
      for (let i = 0; i < pixels.data.length; i += 4) pixels.data[i + 3] = pixels.data[i];
      ctx.putImageData(pixels, 0, 0);
      node.style.maskImage = `url("${mask.toDataURL()}")`;
    };
    const queue = () => { if (!frame) frame = requestAnimationFrame(paint); };
    if (layer.mask.source === 'png' && layer.mask.png) {
      image = new Image(); image.onload = queue; image.src = layer.mask.png;
    }
    const resize = new ResizeObserver(queue); resize.observe(node); resize.observe(source);
    const mutation = new MutationObserver(queue);
    mutation.observe(source, { subtree: true, childList: true, characterData: true, attributes: true });
    source.addEventListener('load', queue, true); document.fonts?.addEventListener('loadingdone', queue); queue();
    return () => {
      cancelAnimationFrame(frame); resize.disconnect(); mutation.disconnect();
      source.removeEventListener('load', queue, true); document.fonts?.removeEventListener('loadingdone', queue);
      if (image) image.onload = null;
    };
  }, [layer.mask]);
  return <div ref={ref} className="factory-ink-pass" data-process-layer={layer.id} data-process-kind="ink" data-process-order={order}
    data-process-mask={layer.mask.source} data-ink-content="true" aria-hidden={hidden || !layer.enabled || undefined}
    style={{ zIndex: order + 2, opacity: hidden || !layer.enabled ? 0 : layer.strength, mixBlendMode: layer.blend ?? 'normal',
      pointerEvents: hidden || !layer.enabled || layer.strength === 0 ? 'none' : undefined }}>
    {children}
  </div>;
}
