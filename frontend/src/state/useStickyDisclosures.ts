import { useCallback, useLayoutEffect, useState, type MutableRefObject } from 'react';

const expandedHeaders = 'details[open] > summary, .conversation-execution-summary[aria-expanded="true"]';

/** CSS owns scrolling; measure only the space needed by nested disclosure titles. */
export function useStickyDisclosures<T extends HTMLElement>(element: MutableRefObject<T | null>) {
  const [viewport, setViewport] = useState<T | null>(null);
  // Sections can mount later through a portal without changing the session.
  const attach = useCallback((node: T | null) => {
    element.current = node;
    setViewport(node);
  }, [element]);
  useLayoutEffect(() => {
    if (!viewport || typeof ResizeObserver === 'undefined') return;
    const observed = new Set<HTMLElement>();
    let frame = 0;
    const clear = (header: HTMLElement) => {
      delete header.dataset.stickyDisclosure;
      header.style.removeProperty('--disclosure-top');
      header.style.removeProperty('--disclosure-depth');
    };
    const update = () => {
      frame = 0;
      const headers = new Set(viewport.querySelectorAll<HTMLElement>(expandedHeaders));
      for (const header of observed) if (!headers.has(header)) {
        resize.unobserve(header);
        observed.delete(header);
        clear(header);
      }
      const blocks = new Map<Element, { top: number; depth: number }>();
      for (const header of headers) {
        let parent = header.parentElement?.parentElement;
        while (parent && parent !== viewport && !blocks.has(parent)) parent = parent.parentElement;
        const { top, depth } = parent && blocks.get(parent) || { top: 0, depth: 0 };
        header.dataset.stickyDisclosure = '';
        header.style.setProperty('--disclosure-top', `${top}px`);
        header.style.setProperty('--disclosure-depth', String(depth));
        blocks.set(header.parentElement!, { top: top + header.offsetHeight, depth: depth + 1 });
        if (!observed.has(header)) { observed.add(header); resize.observe(header); }
      }
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const resize = new ResizeObserver(schedule);
    const mutation = new MutationObserver(schedule);
    mutation.observe(viewport, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'aria-expanded'] });

    const beforeCollapse = (event: MouseEvent) => {
      const header = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-sticky-disclosure]') : null;
      if (event.defaultPrevented || event.button !== 0 || !header?.matches(expandedHeaders) || !viewport.contains(header)) return;
      // Put the block's natural start under its pinned title before content is
      // removed. This also works for keyboard clicks and scaled canvas cards.
      const bounds = viewport.getBoundingClientRect();
      const scale = bounds.height / viewport.offsetHeight || 1;
      const top = parseFloat(header.style.getPropertyValue('--disclosure-top')) || 0;
      const delta = (header.parentElement!.getBoundingClientRect().top - bounds.top) / scale - top;
      if (delta < -1) viewport.scrollTop += delta;
    };
    viewport.addEventListener('click', beforeCollapse, true);
    update();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      mutation.disconnect();
      viewport.removeEventListener('click', beforeCollapse, true);
      observed.forEach(clear);
    };
  }, [viewport]);
  return attach;
}
