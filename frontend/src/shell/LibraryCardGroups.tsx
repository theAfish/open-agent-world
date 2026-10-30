import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { t } from '../i18n';
import { LibraryCardSurface } from './LibraryCard';
import type { LibraryCard, LibrarySource } from './libraryCatalog';
import { libraryGroupOutline, placeLibraryGroups, type GroupRect } from './libraryGroupLayout';
import './libraryCardGroups.css';

interface Group { source: LibrarySource; items: LibraryCard[] }
interface Frame { id: string; path: string; left: number; top: number; width: number }
interface Snapshot { node: HTMLElement; box: DOMRect; face?: { node: HTMLElement; box: DOMRect } }
interface Change { before: Map<string, Snapshot>; id: string; closing: boolean; height: number }
const duration = 420;
const cardKey = (card: LibraryCard) => `${card.kind}:${card.id}`;

export function LibraryCardGroups({ groups, renderCard }: { groups: Group[]; renderCard: (card: LibraryCard) => ReactNode }) {
  const prefix = useId();
  const board = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [geometry, setGeometry] = useState<{ width: number; height: number; contentHeight: number; frames: Frame[];
    positions: { left: number; top: number }[][] }>({ width: 0, height: 0, contentHeight: 0, frames: [], positions: [] });
  const pending = useRef<Change | null>(null);
  const animations = useRef<Animation[]>([]);
  const ghosts = useRef<HTMLElement[]>([]);
  const epoch = useRef(0);
  const folded = (group: Group) => group.items.length > 1 && collapsed.has(group.source.id);

  function stopMotion() {
    epoch.current++;
    animations.current.forEach(animation => animation.cancel());
    ghosts.current.forEach(node => node.remove());
    animations.current = [];
    ghosts.current = [];
    board.current?.classList.remove('is-moving');
  }

  function capture() {
    return new Map([...board.current!.querySelectorAll<HTMLElement>('[data-group-item], [data-group-toggle]')].map(node => {
      const face = node.querySelector<HTMLElement>('.library-card-stock');
      return [node.dataset.groupItem ?? `tab:${node.dataset.groupToggle}`, { node, box: node.getBoundingClientRect(),
        face: face ? { node: face, box: face.getBoundingClientRect() } : undefined } satisfies Snapshot];
    }));
  }

  function toggle(group: Group) {
    if (group.items.length < 2) return;
    const before = capture();
    const height = board.current!.getBoundingClientRect().height;
    stopMotion();
    pending.current = { before, height, id: group.source.id, closing: !folded(group) };
    setCollapsed(previous => {
      const next = new Set(previous);
      next.has(group.source.id) ? next.delete(group.source.id) : next.add(group.source.id);
      return next;
    });
  }

  useLayoutEffect(() => {
    const element = content.current!, surface = board.current!;
    const measure = () => {
      if (!element.clientWidth) return;
      const items = [...element.querySelectorAll<HTMLElement>('[data-group-item]')];
      if (!items.length) { setGeometry({ width: surface.clientWidth, height: 0, contentHeight: 0, frames: [], positions: [] }); return; }
      const nodes = groups.map(group => items.filter(item => item.dataset.source === group.source.id));
      const styles = getComputedStyle(element);
      const layout = placeLibraryGroups(nodes.map(group => group.map(item => item.offsetHeight)), {
        width: element.clientWidth, cardWidth: items[0].offsetWidth,
        cardGap: parseFloat(styles.getPropertyValue('--card-gap')),
        groupGap: parseFloat(styles.getPropertyValue('--group-gap')),
        rowGap: parseFloat(styles.getPropertyValue('--row-gap')),
      });
      const positions = layout.positions.map(group => group.map(position => ({
        left: element.offsetLeft + position.left, top: element.offsetTop + position.top,
      })));
      const labels = element.querySelectorAll<HTMLElement>('.library-group-label');
      const frames = groups.map((group, index) => {
        const rows = new Map<number, GroupRect>();
        for (const [itemIndex, node] of nodes[index].entries()) {
          const { top, left } = positions[index][itemIndex];
          const row = rows.get(top) ?? { left: Infinity, right: -Infinity, top: top - 8, bottom: top + node.offsetHeight + 8 };
          row.left = Math.min(row.left, left - 8);
          row.right = Math.max(row.right, left + node.offsetWidth + 8);
          row.bottom = Math.max(row.bottom, top + node.offsetHeight + 8);
          rows.set(top, row);
        }
        const runs = [...rows.values()].sort((a, b) => a.top - b.top);
        const first = runs[0];
        const width = first.right - first.left - 16;
        // Folding changes this constraint; apply it before measuring the tab.
        labels[index].style.maxWidth = `${width}px`;
        return { id: group.source.id, path: libraryGroupOutline(runs, 8, labels[index].offsetWidth), left: first.left,
          top: first.top - 24, width };
      });
      setGeometry({ width: surface.clientWidth, contentHeight: layout.height, positions,
        height: element.offsetTop + layout.height + parseFloat(getComputedStyle(surface).paddingBottom), frames });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      // Intrinsic card sizes stay stable while the board and positions animate.
      if (animations.current.length && element.clientWidth === width) return;
      if (element.clientWidth !== width) { stopMotion(); width = element.clientWidth; }
      measure();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [groups, collapsed]);

  useLayoutEffect(() => {
    const change = pending.current;
    if (!change) return;
    pending.current = null;
    const surface = board.current!;
    const group = groups.find(item => item.source.id === change.id);
    if (!group) return;
    const tab = [...surface.querySelectorAll<HTMLElement>('[data-group-toggle]')].find(node => node.dataset.groupToggle === change.id);
    tab?.focus({ preventScroll: true });
    if (!surface.animate || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const token = ++epoch.current;
    surface.classList.add('is-moving');
    const options: KeyframeAnimationOptions = { duration, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'both' };
    const animate = (node: HTMLElement, frames: Keyframe[], extra: KeyframeAnimationOptions = {}) => {
      animations.current.push(node.animate(frames, { ...options, ...extra }));
    };
    const after = capture();
    const originStack = change.before.get(`stack:${change.id}`)?.box;
    for (const [key, current] of after) {
      let origin = change.before.get(key)?.box;
      let delay = 0;
      if (!origin && !change.closing && current.node.dataset.source === change.id && originStack) {
        origin = originStack;
        delay = Math.min(group.items.findIndex(item => cardKey(item) === key) * 12, 48);
      }
      if (!origin) continue;
      const dx = origin.left - current.box.left, dy = origin.top - current.box.top;
      const sx = origin.width / current.box.width, sy = origin.height / current.box.height;
      if (Math.abs(dx) + Math.abs(dy) + Math.abs(sx - 1) + Math.abs(sy - 1) < .5 && !delay) continue;
      current.node.style.transformOrigin = '0 0';
      animate(current.node, [{ transform: `translate(${dx}px,${dy}px) scale(${sx},${sy})` }, { transform: 'none' }], { delay });
    }
    if (change.closing) {
      const stack = after.get(`stack:${change.id}`)!.node.querySelector<HTMLElement>('.library-group-stack-front')!;
      const target = stack.getBoundingClientRect(), bounds = surface.getBoundingClientRect();
      group.items.forEach((item, index) => {
        const face = change.before.get(cardKey(item))?.face;
        if (!face) return;
        const ghost = face.node.cloneNode(true) as HTMLElement;
        ghost.inert = true;
        ghost.setAttribute('aria-hidden', 'true');
        ghost.classList.add('library-group-ghost');
        Object.assign(ghost.style, { left: `${face.box.left - bounds.left}px`, top: `${face.box.top - bounds.top}px`, width: `${face.box.width}px`, height: `${face.box.height}px` });
        surface.append(ghost);
        ghosts.current.push(ghost);
        const offset = Math.min(index, 2) * 3;
        const transform = `translate(${target.left - face.box.left + offset}px,${target.top - face.box.top + offset}px) scale(${target.width / face.box.width},${target.height / face.box.height}) rotate(${Math.min(index, 2)}deg)`;
        animate(ghost, [{ transform: 'none', opacity: 1 }, { transform, opacity: 1, offset: .84 }, { transform, opacity: 0 }], { delay: Math.min(index * 10, 40) });
      });
    }
    if (Math.abs(change.height - geometry.height) > 1) animate(surface, [{ height: `${change.height}px` }, { height: `${geometry.height}px` }], { duration: duration + 48 });
    Promise.allSettled(animations.current.map(animation => animation.finished)).then(() => {
      if (epoch.current === token) stopMotion();
    });
  }, [geometry]);

  useLayoutEffect(() => () => stopMotion(), []);

  return <div ref={board} className="library-group-board">
    <svg className="library-group-outlines" aria-hidden="true" viewBox={`0 0 ${geometry.width || 1} ${geometry.height || 1}`} style={{ height: geometry.height }}>
      {geometry.frames.map(frame => <path key={frame.id} data-source={frame.id} d={frame.path} />)}
    </svg>
    <div ref={content} className="library-group-layout" style={{ height: geometry.contentHeight }}>
      {groups.map((group, index) => {
        const { source, items } = group;
        const frame = geometry.frames.find(item => item.id === source.id);
        const isFolded = folded(group), canFold = items.length > 1;
        const labelId = `${prefix}-${index}-label`, itemsId = `${prefix}-${index}-cards`;
        const label = <><span className="library-group-name">{source.name}</span><span className="library-group-count">{items.length}</span>{canFold && (isFolded ? <ChevronRight size={13} /> : <ChevronDown size={13} />)}</>;
        const position = (item: number) => geometry.positions[index]?.[item];
        return <section key={source.id} className="library-source-group" role="group" aria-labelledby={labelId} data-source={source.id}>
          <h4 id={labelId} className="library-group-label" aria-label={source.name} style={{ left: frame?.left, top: frame?.top, maxWidth: frame?.width }}>
            {canFold ? <button type="button" className="library-group-tab" data-group-toggle={source.id} aria-expanded={!isFolded} aria-controls={itemsId}
              aria-label={`${t(isFolded ? 'Expand' : 'Collapse')} ${source.name}`} title={source.name} onClick={() => toggle(group)}>{label}</button>
              : <span className="library-group-tab">{label}</span>}
          </h4>
          <div id={itemsId} className="library-group-cards">
            {isFolded ? <div className="library-group-item" data-group-item={`stack:${source.id}`} data-source={source.id} style={position(0)}>
              <button type="button" className="library-group-stack" aria-label={t('View cards in {v0}', { v0: source.name })} onClick={() => toggle(group)}>
                {items.length > 2 && <span className="library-group-stack-back is-last" aria-hidden="true" />}
                <span className="library-group-stack-back" aria-hidden="true" />
                <LibraryCardSurface card={items[0]} className="library-group-stack-front" />
              </button>
            </div> : items.map((item, itemIndex) => <div key={cardKey(item)} className="library-group-item" data-group-item={cardKey(item)} data-source={source.id}
              data-internal={item.internal || undefined} style={position(itemIndex)}>{renderCard(item)}</div>)}
          </div>
        </section>;
      })}
    </div>
  </div>;
}
