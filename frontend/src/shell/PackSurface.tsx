import { useContext, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Package } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { CardFace, CardStock, type CardFaceProps } from '../components/CardFace';
import type { CardFinish } from '../cards/cardFinish';
import type { PackDefinition } from '../types/world';
import { PACK_DESIGNS, packPackaging } from './packDesign';
import { PackViewContext, type PackNotice, type PackRenderHandle, type PackRenderOptions } from './pack3d/types';
import { packModelKey, packPreviewKey, restorePackPreview } from './pack3d/preview';
import { paintPackCover } from './pack3d/artwork';
import { PACK_OPENING_PRESETS } from './pack3d/motion';
import './libraryPack.css';

export type PackPhase = 'idle' | 'pending' | 'revealing';
export interface PackPreviewCard extends Pick<CardFaceProps, 'definition' | 'label' | 'icon' | 'description'> { id: string; iconUrl?: string; finish?: CardFinish }

function iconImage(element: Element | null) {
  const svg = element?.querySelector('svg');
  if (!svg) return undefined;
  const copy = svg.cloneNode(true) as SVGElement;
  copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('width', '160'); copy.setAttribute('height', '160');
  return `data:image/svg+xml,${encodeURIComponent(new XMLSerializer().serializeToString(copy).replaceAll('currentColor', '#23464c'))}`;
}

/** The production pack surface, also used by the isolated developer design sheet. */
export function PackSurface({ definition, edition, cards, count, opened, phase = 'idle', finishVisible = false,
  issue, version, notice, label, title, disabled, onClick, onSettled, caption }: {
  definition: PackDefinition; edition: string; cards: PackPreviewCard[]; count: number | null;
  opened: boolean; phase?: PackPhase; finishVisible?: boolean; issue?: 'missing' | 'error'; version?: string; notice?: PackNotice;
  caption?: ReactNode; label: string; title?: string; disabled?: boolean; onClick: () => void; onSettled?: () => void;
}) {
  useLocale();
  const metadataId = useId();
  const printedNotice = notice ? { ...notice, label: t(notice.label) } : issue
    ? { kind: 'warning' as const, label: t(issue === 'missing' ? 'Pack unavailable' : 'Environment failed') } : undefined;
  const view = useContext(PackViewContext);
  const article = useRef<HTMLElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const cover = useRef<HTMLCanvasElement>(null);
  const cardLayer = useRef<HTMLSpanElement>(null);
  const renderer = useRef<PackRenderHandle>();
  const previewKey = useRef('');
  const [renderState, setRenderState] = useState<'loading' | 'webgl' | 'fallback'>(() => window.WebGL2RenderingContext ? 'loading' : 'fallback');
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false);
  const gesture = useRef<{ x: number; y: number; yaw: number; pitch: number; moved: boolean }>();
  const suppressClick = useRef(false);
  const packaging = packPackaging(definition.packaging);
  const opening = PACK_OPENING_PRESETS[packaging];
  const color = definition.accent_color ?? PACK_DESIGNS[packaging].color;
  const options = useRef<() => PackRenderOptions>();
  options.current = () => ({ id: definition.id, name: definition.name, description: definition.description || t('A collection of possibilities.'),
    edition, packaging, color, count, countLabel: t('CARDS'), icon: cards[0]?.iconUrl ?? iconImage(article.current?.querySelector('.pack-icon-source') ?? null),
    artwork: definition.artwork_url ?? undefined, version, notice: printedNotice, opened, revealing: phase === 'revealing', finishVisible, reducedMotion, view,
    cards: cards.slice(0, 3).map(card => ({ id: card.id })),
  });
  const modelKey = packModelKey(options.current());
  useEffect(() => {
    if (renderState !== 'fallback' || !cover.current) return;
    // Drop the last GPU projection when context loss switches to the flat cover.
    for (const element of cardLayer.current!.children) {
      (element as HTMLElement).style.removeProperty('transform');
      (element as HTMLElement).style.removeProperty('opacity');
    }
    return paintPackCover(cover.current, options.current!());
  }, [renderState, modelKey]);
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
        renderer.current = attachPackRenderer(canvas.current, options.current!(), value => { if (!cancelled) setRenderState(value ? 'webgl' : 'fallback'); }, cardLayer.current!);
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
  return <article ref={article} aria-label={definition.name} data-pack-id={definition.id} data-pack-issue={issue} data-pack-notice={printedNotice?.kind} data-pack-version={version} data-packaging={packaging} data-renderer={renderState} data-preview-opening={view.opening}
    className={`library-pack ${opened ? 'is-opened' : ''} is-${phase}`} style={{ '--pack-color': color, '--pack-opening': view.opening,
      '--pack-opening-dip': `${opening.dip}px` } as CSSProperties}>
    <button type="button" className="pack-touch-area" aria-label={label} title={title} aria-describedby={version || printedNotice ? metadataId : undefined}
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
          if (event.pointerType !== 'touch') { const box = event.currentTarget.getBoundingClientRect(); renderer.current?.hover((event.clientX - box.left) / box.width - .5, (event.clientY - box.top) / box.height - .5); }
        }
      }}
      onPointerUp={event => { gesture.current = undefined; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onLostPointerCapture={() => { gesture.current = undefined; }}
      onPointerCancel={() => { gesture.current = undefined; suppressClick.current = true; renderer.current?.hover(0, 0); }}
      onPointerLeave={() => { if (!gesture.current) renderer.current?.hover(0, 0); }}
      onBlur={() => renderer.current?.hover(0, 0)}
      onKeyDown={event => {
        if (!renderer.current || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) return;
        event.preventDefault(); const angles = renderer.current.angles();
        renderer.current.rotate(event.key === 'Home' ? view.yaw ?? (packaging === 'collector' ? -17 : -7) : angles.yaw + (event.key === 'ArrowLeft' ? -15 : event.key === 'ArrowRight' ? 15 : 0),
          event.key === 'Home' ? view.pitch ?? -3 : angles.pitch + (event.key === 'ArrowUp' ? -10 : event.key === 'ArrowDown' ? 10 : 0));
      }}
      onClick={event => { if (suppressClick.current && event.detail !== 0) { suppressClick.current = false; return; } onClick(); }}>
      <span className="pack-reveal-cards" ref={cardLayer} aria-hidden="true">{(phase === 'revealing' || (view.opening !== undefined && view.opening > 0 && view.opening < 1)) && cards.slice(0, 3).map((card, index, shown) =>
        <span className="pack-reveal-card" key={card.id} data-reveal-card={card.id}
          style={{ '--card-offset': index - (shown.length - 1) / 2 } as CSSProperties}>
          <CardStock finish={card.finish} finishVisible={finishVisible || view.opening !== undefined} quality="standard" reveal>
            <CardFace definition={card.definition} icon={card.icon} label={card.label} description={card.description} />
          </CardStock>
        </span>)}</span>
      <canvas className="pack-webgl" ref={canvas} aria-hidden="true" />
      {renderState === 'loading' && <span className="pack-loading" aria-hidden="true"><span /><small>{definition.name}</small></span>}
      {renderState === 'fallback' && <>
        <canvas className="pack-cover" ref={cover} aria-hidden="true" onAnimationEnd={event => {
          if (event.animationName === 'packOpeningDip') onSettled?.();
        }} />
      </>}
      <span hidden aria-hidden="true">
        <span className="pack-icon-source">{cards[0]?.icon ?? <Package size={38} strokeWidth={1.5} />}</span>
      </span>
    </button>
    {(version || printedNotice) && <span className="sr-only" id={metadataId}>
      {version && <span>{t('Version')} {version}</span>}{version && printedNotice && ' · '}
      {printedNotice && <span>{printedNotice.label}</span>}
    </span>}
    {caption ?? <span className="pack-edition">{edition}</span>}
  </article>;
}
