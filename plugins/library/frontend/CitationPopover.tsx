import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { CitationMarker, CitationRect, CitationReference } from "./citationIndex";
import { CitationCollect } from "./CitationCollect";

export type ActiveCitation = { marker: CitationMarker; anchor: HTMLButtonElement; pinned: boolean };

export function CitationTargets({ markers, active, onOpen, onLeave }: {
  markers: CitationMarker[]; active?: ActiveCitation;
  onOpen: (marker: CitationMarker, anchor: HTMLButtonElement, pinned: boolean) => void; onLeave: () => void;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const [positioned, setPositioned] = useState<CitationMarker[]>([]);
  useLayoutEffect(() => {
    const page = layer.current?.parentElement;
    const textLayer = page?.querySelector<HTMLElement>(".textLayer");
    if (!page || !textLayer) { setPositioned([]); return; }
    let frame = 0, disposed = false;
    const measure = () => {
      frame = 0;
      if (disposed) return;
      setPositioned(markers.flatMap(marker => {
        const rects = marker.native ? marker.rects : renderedCitationRects(marker, page, textLayer);
        return rects.length ? [{ ...marker, rects }] : [];
      }));
    };
    const schedule = () => { if (!disposed && !frame) frame = requestAnimationFrame(measure); };
    // PDF.js builds and replaces its text layer asynchronously on page/zoom changes.
    // Never expose a guessed proportional-font hitbox while that layer is preparing.
    const mutations = new MutationObserver(schedule);
    mutations.observe(textLayer, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ["style", "data-text-item-index"] });
    const resize = new ResizeObserver(schedule);
    resize.observe(page); resize.observe(textLayer);
    window.addEventListener("resize", schedule);
    document.fonts?.addEventListener("loadingdone", schedule);
    measure();
    return () => {
      disposed = true; cancelAnimationFrame(frame); mutations.disconnect(); resize.disconnect();
      window.removeEventListener("resize", schedule);
      document.fonts?.removeEventListener("loadingdone", schedule);
    };
  }, [markers]);
  return <div ref={layer} className="library-citation-layer">{positioned.flatMap(marker => marker.rects.map((rect, i) =>
    <button key={marker.id + "-" + i} type="button" className="library-citation-target"
      aria-label={t("引用 {citation}", { citation: marker.text })} aria-haspopup="dialog"
      aria-expanded={active?.marker.id === marker.id} tabIndex={i === 0 ? 0 : -1}
      style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%` }}
      onPointerEnter={event => { if (event.pointerType !== "touch" && !event.buttons && !window.getSelection()?.toString()) onOpen(marker, event.currentTarget, false); }}
      onPointerLeave={onLeave}
      onPointerDown={event => event.stopPropagation()}
      onMouseDown={event => event.stopPropagation()} onMouseUp={event => event.stopPropagation()}
      onClick={event => { event.stopPropagation(); onOpen(marker, event.currentTarget, true); }}
    />))}</div>;
}

/** Measure real rendered glyphs; equal-character interpolation is wrong for PDF fonts. */
function renderedCitationRects(marker: CitationMarker, page: HTMLElement, textLayer: HTMLElement): CitationRect[] {
  if (!marker.textRanges?.length) return [];
  const bounds = page.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return [];
  const rects: CitationRect[] = [];
  for (const source of marker.textRanges) {
    const span = textLayer.querySelector<HTMLElement>(`[data-text-item-index="${source.textItemIndex}"]`);
    if (!span) return []; // The entire marker waits until all of its source runs exist.
    const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT);
    let offset = 0, node: Node | null, start: { node: Node; offset: number } | undefined;
    let end: { node: Node; offset: number } | undefined;
    while ((node = walker.nextNode())) {
      const length = node.textContent?.length ?? 0;
      if (!start && source.startOffset >= offset && source.startOffset < offset + length)
        start = { node, offset: source.startOffset - offset };
      if (source.endOffset > offset && source.endOffset <= offset + length) {
        end = { node, offset: source.endOffset - offset }; break;
      }
      offset += length;
    }
    if (!start || !end) return [];
    const range = document.createRange();
    range.setStart(start.node, start.offset); range.setEnd(end.node, end.offset);
    for (const rect of Array.from(range.getClientRects())) {
      if (rect.width <= 0 || rect.height <= 0) continue;
      const left = Math.max(bounds.left, rect.left), top = Math.max(bounds.top, rect.top);
      const right = Math.min(bounds.right, rect.right), bottom = Math.min(bounds.bottom, rect.bottom);
      if (right > left && bottom > top) rects.push({ x: (left - bounds.left) / bounds.width,
        y: (top - bounds.top) / bounds.height, width: (right - left) / bounds.width, height: (bottom - top) / bounds.height });
    }
  }
  // A citation can span several PDF.js text items (font changes, kerning, or
  // individual glyphs). Their browser rectangles may overlap; expose one target
  // per visual line so a later fragment cannot cover the keyboard-focusable one.
  const rows: CitationRect[][] = [];
  for (const rect of rects.sort((a, b) => a.y - b.y || a.x - b.x)) {
    const row = rows.find(parts => {
      const anchor = parts[0];
      const overlap = Math.min(anchor.y + anchor.height, rect.y + rect.height) - Math.max(anchor.y, rect.y);
      return overlap >= Math.min(anchor.height, rect.height) * .5;
    });
    if (row) row.push(rect); else rows.push([rect]);
  }
  return rows.map(parts => {
    const x = Math.min(...parts.map(rect => rect.x)), y = Math.min(...parts.map(rect => rect.y));
    return { x, y, width: Math.max(...parts.map(rect => rect.x + rect.width)) - x,
      height: Math.max(...parts.map(rect => rect.y + rect.height)) - y };
  });
}

export function CitationPopover({ active, references, host, scroller, onClose, onEnter, onLeave, onLocate, paperId, documentVersionId }: {
  active: ActiveCitation; references: CitationReference[]; host: RefObject<HTMLDivElement>; scroller: RefObject<HTMLDivElement>;
  onClose: (restoreFocus?: boolean) => void; onEnter: () => void; onLeave: () => void; onLocate: (reference: CitationReference) => void;
  paperId?: string; documentVersionId?: string;
}) {
  useLocale();
  const root = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0, visible: false });
  useEffect(() => { if (active.pinned) root.current?.focus({ preventScroll: true }); }, [active.pinned, active.marker.id]);
  useLayoutEffect(() => {
    let frame = 0;
    const place = () => {
      frame = 0;
      const box = host.current, popup = root.current, anchor = active.anchor;
      if (!box || !popup || !anchor.isConnected) { setPosition(p => ({ ...p, visible: false })); return; }
      const h = box.getBoundingClientRect(), a = anchor.getBoundingClientRect(), clip = scroller.current?.getBoundingClientRect() ?? h;
      const sx = h.width / box.offsetWidth || 1, sy = h.height / box.offsetHeight || 1;
      const width = popup.offsetWidth * sx, height = popup.offsetHeight * sy, gap = 8;
      const left = Math.max(h.left + gap, Math.min((a.left + a.right - width) / 2, h.right - width - gap));
      const below = a.bottom + gap;
      const top = Math.max(h.top + gap, Math.min(below + height <= h.bottom - gap ? below : a.top - height - gap, h.bottom - height - gap));
      setPosition({ left: (left - h.left) / sx, top: (top - h.top) / sy,
        visible: a.bottom >= clip.top && a.top <= clip.bottom && a.right >= clip.left && a.left <= clip.right });
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(place); };
    const observer = new ResizeObserver(schedule);
    [host.current, root.current, active.anchor].forEach(el => { if (el) observer.observe(el); });
    const container = scroller.current;
    container?.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule); place();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); container?.removeEventListener("scroll", schedule); window.removeEventListener("resize", schedule); };
  }, [active, host, scroller]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if ((event.target as Element | null)?.closest?.("dialog") !== root.current?.closest("dialog")) return;
      event.preventDefault(); event.stopImmediatePropagation(); onClose(true);
    };
    const outside = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (target?.closest("dialog") && target.closest("dialog") !== root.current?.closest("dialog")) return;
      if (root.current?.contains(target) || target?.closest(".library-citation-target")) return;
      onClose();
    };
    window.addEventListener("keydown", key, true); document.addEventListener("pointerdown", outside, true);
    return () => { window.removeEventListener("keydown", key, true); document.removeEventListener("pointerdown", outside, true); };
  }, [onClose]);
  return <div ref={root} role="dialog" tabIndex={-1} aria-modal="false" aria-label={t("参考文献")}
    className="library-selection-popup library-citation-popup nodrag nopan nowheel"
    onPointerEnter={onEnter} onPointerLeave={onLeave}
    style={{ left: position.left, top: position.top, visibility: position.visible ? "visible" : "hidden" }}>
    <header><div><strong>{t("参考文献")}</strong><span className="library-citation-label">{active.marker.text}</span></div>
      <button type="button" aria-label={t("关闭引用气泡")} onClick={() => onClose(true)}>×</button></header>
    <div className="library-citation-entries">{references.map(reference =>
      <article key={reference.id} data-reference-id={reference.id}>
        <p className="library-citation-reference">{references.length>1&&/^\d+$/.test(reference.label)&&<strong className="library-citation-number">[{reference.label}] </strong>}{reference.text}</p>
        <div className="library-citation-actions">
          <button type="button" onClick={() => onLocate(reference)}>{t("定位原文")} · {t("Page {page}", { page: reference.page })}</button>
          {(reference.doi || reference.url) && <a href={safeReferenceUrl(reference)} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{reference.doi ? "DOI ↗" : t("文献链接 ↗")}</a>}
          <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(reference.text)}`} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{t("检索文献")} ↗</a>
        </div>
        {paperId&&active.pinned&&<CitationCollect paperId={paperId} documentVersionId={documentVersionId} reference={reference}/>}
      </article>)}</div>
    <small>{t("来自本篇文末参考文献 · 点击链接才会打开外部网站")}</small>
  </div>;
}

function safeReferenceUrl(reference: CitationReference) {
  if (reference.doi && /^10\.\d{4,9}\//.test(reference.doi)) return "https://doi.org/" + encodeURI(reference.doi).replace(/[?#]/g, encodeURIComponent);
  try { const url = new URL(reference.url ?? ""); if (url.protocol === "http:" || url.protocol === "https:") return url.href; } catch { /* Text is always rendered without HTML. */ }
  return undefined;
}
