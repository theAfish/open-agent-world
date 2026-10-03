import { useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ChevronRight, Leaf, Package } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { useSurfaceTilt } from '../components/useSurfaceTilt';
import { CardFinishLayer } from '../cards/CardFinishLayer';
import type { CardFinish } from '../cards/cardFinish';
import type { PackDefinition } from '../types/world';
import { PACK_DESIGNS, packPackaging } from './packDesign';
import { PackViewContext, type PackRenderHandle, type PackRenderOptions } from './pack3d/types';
import { packPreviewKey, restorePackPreview } from './pack3d/preview';
import './libraryPack.css';

export type PackPhase = 'idle' | 'pending' | 'revealing';
export interface PackPreviewCard { id: string; label: string; icon: ReactNode; iconUrl?: string; color?: string; finish?: CardFinish }

function iconImage(element: Element | null) {
  const svg = element?.querySelector('svg');
  if (!svg) return undefined;
  const copy = svg.cloneNode(true) as SVGElement;
  copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('width', '160'); copy.setAttribute('height', '160');
  return `data:image/svg+xml,${encodeURIComponent(new XMLSerializer().serializeToString(copy).replaceAll('currentColor', '#23464c'))}`;
}

/** The production pack surface, also used by the isolated developer design sheet. */
export function PackSurface({ definition, edition, cards, count, opened, phase = 'idle', finishVisible = false,
  issue, status, label, title, sealLabel, disabled, onClick, onSettled }: {
  definition: PackDefinition; edition: string; cards: PackPreviewCard[]; count: number | null;
  opened: boolean; phase?: PackPhase; finishVisible?: boolean; issue?: 'missing' | 'error'; status?: string;
  label: string; title?: string; sealLabel: string; disabled?: boolean; onClick: () => void; onSettled?: () => void;
}) {
  useLocale();
  const tilt = useSurfaceTilt(9);
  const view = useContext(PackViewContext);
  const article = useRef<HTMLElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<PackRenderHandle>();
  const previewKey = useRef('');
  const [renderState, setRenderState] = useState<'loading' | 'webgl' | 'fallback'>(() => window.WebGL2RenderingContext ? 'loading' : 'fallback');
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false);
  const gesture = useRef<{ x: number; y: number; yaw: number; pitch: number; moved: boolean }>();
  const suppressClick = useRef(false);
  const [failedArtwork, setFailedArtwork] = useState<string | null>(null);
  const packaging = packPackaging(definition.packaging);
  const color = definition.accent_color ?? PACK_DESIGNS[packaging].color;
  const artwork = definition.artwork_url && definition.artwork_url !== failedArtwork ? definition.artwork_url : null;
  const options = useRef<() => PackRenderOptions>();
  options.current = () => ({ id: definition.id, name: definition.name, description: definition.description || t('A collection of possibilities.'),
    edition, packaging, color, count, countLabel: t('CARDS'), icon: cards[0]?.iconUrl ?? iconImage(article.current?.querySelector('.pack-emblem') ?? null),
    artwork: artwork ?? undefined, issue, opened, revealing: phase === 'revealing', finishVisible, reducedMotion, view,
    cards: cards.map((card, index) => ({ id: card.id, label: card.label, color: card.color, finish: card.finish,
      icon: card.iconUrl ?? iconImage(article.current?.querySelectorAll('.pack-drawn-card')[index] ?? null) })),
  });
  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(preference?.matches ?? false);
    preference?.addEventListener('change', update);
    return () => preference?.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    // No GPU is required for tests, server rendering or unsupported browsers.
    if (!window.WebGL2RenderingContext || !canvas.current) return;
    let cancelled = false, visible = false, loading = false, release = 0;
    const attach = async () => {
      if (renderer.current || loading) return;
      loading = true;
      try {
        const { attachPackRenderer } = await import('./pack3d/renderer');
        if (cancelled || !visible || !canvas.current) return;
        renderer.current = attachPackRenderer(canvas.current, options.current!(), value => { if (!cancelled) setRenderState(value ? 'webgl' : 'fallback'); });
      } catch (error) {
        if (!cancelled) { setRenderState('fallback'); console.warn('Pack 3D unavailable; showing its cover.', error); }
      } finally { loading = false; }
    };
    const visibility = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      window.clearTimeout(release);
      if (visible) void attach();
      else release = window.setTimeout(() => {
        renderer.current?.destroy(); renderer.current = undefined;
      }, 1500);
    }, { rootMargin: '60px' });
    visibility.observe(canvas.current);
    return () => { cancelled = true; visibility.disconnect(); window.clearTimeout(release); renderer.current?.destroy(); renderer.current = undefined; };
  }, []);
  useEffect(() => {
    const next = options.current!(), key = packPreviewKey(next);
    if (previewKey.current !== key) {
      previewKey.current = key;
      if (!renderer.current && canvas.current && window.WebGL2RenderingContext) {
        setRenderState(restorePackPreview(key, canvas.current) ? 'webgl' : 'loading');
      }
    }
    renderer.current?.update(next);
  });
  return <article ref={article} aria-label={definition.name} data-pack-id={definition.id} data-pack-issue={issue} data-packaging={packaging} data-renderer={renderState}
    className={`library-pack ${opened ? 'is-opened' : ''} is-${phase}`} style={{ '--pack-color': color } as CSSProperties}>
    <button type="button" className="pack-touch-area" {...tilt} aria-label={label} title={title}
      aria-busy={phase === 'pending' || renderState === 'loading'} disabled={disabled}
      onPointerDown={event => {
        if (!renderer.current || event.button !== 0) return;
        const angles = renderer.current.angles(); suppressClick.current = false;
        gesture.current = { x: event.clientX, y: event.clientY, ...angles, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={event => {
        const drag = gesture.current;
        if (drag) {
          const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
          if (Math.hypot(dx, dy) > 5) { drag.moved = true; suppressClick.current = true; }
          if (drag.moved) renderer.current?.rotate(drag.yaw + dx * .55, drag.pitch + dy * .35);
        } else {
          tilt.onPointerMove(event);
          if (event.pointerType !== 'touch') { const box = event.currentTarget.getBoundingClientRect(); renderer.current?.hover((event.clientX - box.left) / box.width - .5, (event.clientY - box.top) / box.height - .5); }
        }
      }}
      onPointerUp={event => { gesture.current = undefined; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onLostPointerCapture={() => { gesture.current = undefined; }}
      onPointerCancel={() => { gesture.current = undefined; suppressClick.current = true; tilt.onPointerCancel(); renderer.current?.hover(0, 0); }}
      onPointerLeave={() => { tilt.onPointerLeave(); if (!gesture.current) renderer.current?.hover(0, 0); }}
      onBlur={() => { tilt.onBlur(); renderer.current?.hover(0, 0); }}
      onKeyDown={event => {
        if (!renderer.current || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) return;
        event.preventDefault(); const angles = renderer.current.angles();
        renderer.current.rotate(event.key === 'Home' ? view.yaw ?? (packaging === 'collector' ? -17 : -7) : angles.yaw + (event.key === 'ArrowLeft' ? -15 : event.key === 'ArrowRight' ? 15 : 0),
          event.key === 'Home' ? view.pitch ?? -3 : angles.pitch + (event.key === 'ArrowUp' ? -10 : event.key === 'ArrowDown' ? 10 : 0));
      }}
      onClick={event => { if (suppressClick.current && event.detail !== 0) { suppressClick.current = false; return; } onClick(); }}>
      <canvas className="pack-webgl" ref={canvas} aria-hidden="true" />
      {renderState === 'loading' && <span className="pack-loading" aria-hidden="true"><span /><small>{definition.name}</small></span>}
      <span className="pack-fallback" aria-hidden="true">
      <span className="pack-shadow" aria-hidden="true" />
      <span className="pack-object" aria-hidden="true" onAnimationEnd={event => {
        if (renderState === 'fallback' && event.target === event.currentTarget && event.animationName === 'packDeflate') onSettled?.();
      }}>
        <span className="pack-back" /><span className="pack-mouth" />
        <span className="pack-card-pocket"><span className="pack-drawn-cards">{cards.map((card, index) =>
          <span className="pack-drawn-card card-finish-surface" key={card.id}
            style={{ '--card-offset': index - (cards.length - 1) / 2, '--collection-color': card.color ?? color } as CSSProperties}>
            {card.icon}<strong>{card.label}</strong><small>{t('COLLECTED CARD')}</small>
            {phase === 'revealing' && finishVisible && <CardFinishLayer finish={card.finish} quality="standard" reveal />}
          </span>)}</span></span>
        <span className="pack-facet pack-facet-left" /><span className="pack-facet pack-facet-right" />
        <span className="pack-facet pack-facet-top" /><span className="pack-facet pack-facet-bottom" />
        <span className="pack-box-side"><span>OAW</span><i /><small>PLAY<br />EXPLORE<br />CREATE</small></span>
        <span className="pack-wrapper">
          <span className="pack-print">
            {!issue && (artwork ? <img className="pack-artwork" src={artwork} alt="" draggable={false} onError={() => setFailedArtwork(artwork)} /> : <>
              <span className="pack-guilloche" />
              {packaging === 'collector' && <span className="pack-landscape"><i /><i /><i /></span>}
            </>)}
            <span className="pack-brand"><b>OAW</b><small>OPEN AGENT WORLD</small></span>
            <span className="pack-emblem">{issue ? <strong className="pack-issue-mark">{issue === 'missing' ? 'MISSING' : 'ERROR'}</strong> : cards[0]?.icon ?? <Package size={38} strokeWidth={1.5} />}</span>
            <span className="pack-title">{definition.name}</span>
            <span className="pack-subtitle">{definition.description || t('A collection of possibilities.')}</span>
            <span className="pack-print-footer"><b>{count === null ? '—' : String(count).padStart(2, '0')} <small>{t('CARDS')}</small></b><span>OPEN AGENT<br />WORLD</span></span>
          </span>
          {packaging === 'premium' && <span className="pack-hologram"><span>EXPLORE · CREATE · GROW</span></span>}
          <span className="pack-foil" />
        </span>
        <span className="pack-bottom-seal" />
        <span className="pack-top-seal"><span>{sealLabel}</span><ChevronRight size={10} /></span>
        {packaging === 'paper' && <span className="pack-paper-flap"><span className="pack-brand"><b>OAW</b><small>OPEN AGENT WORLD</small></span><span className="pack-paper-stamp"><Leaf size={17} strokeWidth={1.4} /></span></span>}
        {packaging === 'collector' && <span className="pack-box-lid" />}
      </span>
      </span>
    </button>
    <span className="pack-edition">{edition}</span>
    {status && <span className="pack-inventory-status">{status}</span>}
  </article>;
}
