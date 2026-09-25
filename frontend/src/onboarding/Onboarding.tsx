import { t, useLocale } from "../i18n";
import { createPortal } from 'react-dom';
import { getNodesBounds, getViewportForBounds, useReactFlow } from '@xyflow/react';
import { ArrowRight, ChevronDown, Compass, Pause, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { useCardLibrary } from '../state/cardLibrary';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { beginGlueEdit, glueGroup, persistGlue, useGlueStore } from '../state/glue';
import { nodePositionFromSurfacePosition } from '../canvas/nodeDisplacement';
import { OawGuide, type GuideMotion } from './OawGuide';
import { CHAPTERS, STEPS, STARTER_CARDS, starterPack, type Role, type Target } from './steps';
import { tutorial, useTutorialStore, type GuideVisuals } from './controller';
import './onboarding.css';
import { useMinisterRole } from '../state/ministerRole';
import { QuickStartGuide } from './QuickStartGuide';
import { BlueprintChooser } from './BlueprintChooser';
import { installTutorialInteractionGuard } from './interactionGuard';
import { Spotlight, type SpotlightHandle } from './Spotlight';
import { relationshipPath } from '../edges/geometry';
import { nodeCornerRadius } from '../edges/nodeGeometry';
import type { CanvasNode } from '../cards/types';
import { GuideTravel } from './guideTravel';
import { placeGuide, vacantPosition } from './placement';

function nodeElement(id: string) { return document.querySelector<HTMLElement>(`.world-canvas > .react-flow .react-flow__node[data-id="${CSS.escape(id)}"]`); }
/** Only illuminate the part of a target visible inside its scroll containers. */
function visibleBounds(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  let left = Math.max(0, rect.left), top = Math.max(0, rect.top);
  let right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent), bounds = parent.getBoundingClientRect();
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) { left = Math.max(left, bounds.left); right = Math.min(right, bounds.right); }
    if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) { top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom); }
  }
  return new DOMRect(left, top, Math.max(0, right - left), Math.max(0, bottom - top));
}
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
async function frameElement(id: string, signal: AbortSignal) {
  const deadline = performance.now() + 6000;
  while (performance.now() < deadline) {
    signal.throwIfAborted();
    const element = nodeElement(id);
    if (element && element.getBoundingClientRect().width > 0) return element;
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  }
  throw new Error(t("The card is outside the visible canvas. Use Recover this step and retry."));
}
async function animate(element: Element, keyframes: Keyframe[], signal: AbortSignal, duration = 850) {
  signal.throwIfAborted();
  if (reducedMotion()) return;
  const animation = element.animate(keyframes, { duration, easing: 'cubic-bezier(.35,0,.25,1)', fill: 'both' });
  const cancel = () => animation.cancel();
  signal.addEventListener('abort', cancel, { once: true });
  try { await animation.finished; }
  finally { signal.removeEventListener('abort', cancel); animation.cancel(); }
  signal.throwIfAborted();
}

export function Onboarding() {
  useLocale();
  const s = useTutorialStore();
  const libraryOpen = useCardLibrary(w => w.open);
  const sync = useWorldStore(w => w.syncState);
  const cards = useWorldStore(w => w.cards);
  const surfaces = useNodeSurfaceStore(w => w.surfaceLevels);
  const activeWorkspaceId = useLegionWorkspace(w => w.activeId);
  const workspaceId = cards.some(card => card.id === activeWorkspaceId && card.type === 'legion') ? activeWorkspaceId : undefined;
  const [workspaceHost, setWorkspaceHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    setWorkspaceHost(workspaceId ? document.querySelector<HTMLElement>(`dialog[data-legion-workspace="${CSS.escape(workspaceId)}"]`) : null);
  }, [workspaceId]);
  useLayoutEffect(() => {
    if (!workspaceHost || s.view === 'hidden') return;
    workspaceHost.classList.add('has-tutorial-guide');
    return () => { workspaceHost.classList.remove('has-tutorial-guide'); workspaceHost.style.removeProperty('--tutorial-guide-height'); };
  }, [workspaceHost, s.view]);
  const flow = useReactFlow<CanvasNode>();
  const [compact, setCompact] = useState(false);
  const [rightGuide, setRightGuide] = useState(false);
  const [position, setPosition] = useState({ x: window.innerWidth / 2 - 80, y: window.innerHeight <= 700 ? 16 : Math.min(72, window.innerHeight * .09) });
  const [trace, setTrace] = useState<{ source: string; target: string }>();
  const tracePath = useRef<SVGPathElement>(null);
  const guide = useRef<HTMLDivElement>(null);
  const spotlight = useRef<SpotlightHandle>(null);
  const deckArrow = useRef<SVGPathElement>(null);
  const placementArrow = useRef<HTMLDivElement>(null);
  const arrowId = useId();
  const flightLayer = useRef<HTMLDivElement>(null);
  const focusSequence = useRef(0);
  const welcome = s.view === 'welcome';
  const active = s.view === 'active';
  const step = STEPS.find(item => item.id === s.session?.step) ?? STEPS[0];
  const reviewing = s.session?.completedDemo === step.id;
  const target = s.target ?? (reviewing ? step.result?.target : undefined) ?? step.target;
  const currentPosition = useRef(position);
  const travel = useRef<GuideTravel>();
  travel.current ??= new GuideTravel(position);
  const anchor = useRef<{ key: string; x: number; y: number } | undefined>(undefined);
  const guideArrived = useRef(false);
  const ministerPanelId = useMinisterRole(state => state.settingsCardId);
  const origin = useRef(flow.screenToFlowPosition({ x: window.innerWidth * .72, y: window.innerHeight * .42 }));

  const focusSubjects = useCallback(async (ids: string[], reserveCardSpace = false) => {
    const sequence = ++focusSequence.current;
    const deadline = performance.now() + 6000;
    let previous = '', stableFrames = 0;
    while (performance.now() < deadline && sequence === focusSequence.current) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const nodes = ids.flatMap(id => { const node = flow.getNode(id); return node ? [node] : []; });
      const levels = useNodeSurfaceStore.getState().surfaceLevels;
      if (nodes.length !== ids.length || nodes.some(node => node.data.surfaceLevel !== (levels[node.id] ?? 'preview'))) continue;
      const signature = JSON.stringify(nodes.map(node => [node.position, node.style?.width, node.style?.height]));
      stableFrames = signature === previous ? stableFrames + 1 : 0;
      previous = signature;
      if (stableFrames < 2) continue;
      if (!nodes.length) return;
      // ResizeObserver's measured size can still describe the closed card.
      // Use the canvas's current surface sizes and wait for its layout to settle.
      const bounds = getNodesBounds(nodes.map(node => ({ ...node, measured: {
        width: Number(node.style?.width ?? node.width), height: Number(node.style?.height ?? node.height),
      } })));
      if (reserveCardSpace) {
        const size = { width: 224, height: 300 };
        const obstacles = flow.getNodes().filter(node => !node.hidden).map(node => ({
          ...(flow.getInternalNode(node.id)?.internals.positionAbsolute ?? node.position),
          width: Number(node.style?.width ?? node.measured?.width ?? 96), height: Number(node.style?.height ?? node.measured?.height ?? 96),
        }));
        const space = vacantPosition({ x: bounds.x + bounds.width + 100, y: bounds.y, ...size }, obstacles, 80);
        const right = Math.max(bounds.x + bounds.width, space.x + size.width), bottom = Math.max(bounds.y + bounds.height, space.y + size.height);
        bounds.x = Math.min(bounds.x, space.x); bounds.y = Math.min(bounds.y, space.y);
        bounds.width = right - bounds.x; bounds.height = bottom - bounds.y;
      }
      const { width, height } = useWorldStore.getState().viewport;
      const gutter = width >= 900 ? 290 : 0;
      const viewport = getViewportForBounds(bounds, width - gutter - 20, Math.max(280, height - 150), .3, .85, .25);
      await flow.setViewport({ ...viewport, x: viewport.x + gutter, y: viewport.y + 28 }, { duration: reducedMotion() ? 0 : 650 });
      return;
    }
  }, [flow]);

  const connectionGeometry = useCallback((source: string, target: string) => {
    const a = flow.getInternalNode(source), b = flow.getInternalNode(target);
    if (!a || !b) return;
    const rect = (node: typeof a) => ({ ...node.internals.positionAbsolute,
      width: node.measured?.width ?? Number(node.style?.width ?? 96),
      height: node.measured?.height ?? Number(node.style?.height ?? 96),
    });
    const v = flow.getViewport();
    return { ...relationshipPath(rect(a), rect(b), nodeCornerRadius(a), nodeCornerRadius(b)),
      transform: `translate(${v.x} ${v.y}) scale(${v.zoom})` };
  }, [flow]);

  const waitForGuide = useCallback(async (signal: AbortSignal) => {
    // Let the new target render before checking arrival. Demonstrations begin
    // only after the guide has walked over and the new spotlight has appeared.
    const started = performance.now();
    while (performance.now() - started < 5000) {
      signal.throwIfAborted();
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      if (performance.now() - started > 520 && guideArrived.current) return;
    }
  }, []);

  useEffect(() => {
    let revealPlacement: (() => void) | undefined;
    const bridge: GuideVisuals = {
      focus: focusSubjects,
      preparePlace(existingId) {
        // Subscribe before creation so the rule exists before React paints the
        // new node, including while its name is saved and the camera settles.
        const existing = new Set(useWorldStore.getState().cards.map(card => card.id));
        const style = document.createElement('style');
        document.head.append(style);
        let staged = existingId;
        const hide = (id: string) => {
          style.textContent = `.world-canvas .react-flow__node[data-id="${CSS.escape(id)}"] { visibility: hidden !important; opacity: 0 !important; }`;
        };
        if (staged) hide(staged);
        const unsubscribe = useWorldStore.subscribe(state => {
          if (staged) return;
          const card = state.cards.find(card => card.type === 'text' && !existing.has(card.id));
          if (card) { staged = card.id; hide(card.id); }
        });
        revealPlacement = () => { unsubscribe(); style.remove(); };
        return revealPlacement;
      },
      findSpace(preferred, size) {
        const offset = { x: (96 - size.width) / 2, y: (96 - size.height) / 2 };
        const obstacles = flow.getNodes().filter(node => !node.hidden).map(node => ({
          ...(flow.getInternalNode(node.id)?.internals.positionAbsolute ?? node.position),
          width: Number(node.style?.width ?? node.measured?.width ?? 96), height: Number(node.style?.height ?? node.measured?.height ?? 96),
        }));
        const point = vacantPosition({ x: preferred.x + offset.x, y: preferred.y + offset.y, ...size }, obstacles);
        return { x: point.x - offset.x, y: point.y - offset.y };
      },
      async place(id, signal) {
        const element = await frameElement(id, signal);
        await waitForGuide(signal);
        if (reducedMotion()) { revealPlacement?.(); return; }
        // Animate an inert screen-space copy. The real node stays at its final
        // bounds, so guide placement and the spotlight never chase the flight.
        const box = element.getBoundingClientRect();
        const source = document.querySelector('[data-palette-card="text"] [data-deck-visual]')?.getBoundingClientRect();
        const deck = document.querySelector('[data-tutorial="deck"]')?.getBoundingClientRect();
        const x = (source && source.width ? source.left : (deck?.left ?? 30) + (deck?.width ?? 160) / 2) - box.left;
        const y = (source && source.width ? source.top : deck?.top ?? window.innerHeight - 100) - box.top;
        const flight = document.createElement('div');
        flight.className = 'tutorial-placement-flight';
        Object.assign(flight.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
        const copy = element.cloneNode(true) as HTMLElement;
        for (const child of [copy, ...copy.querySelectorAll<HTMLElement>('*')]) {
          for (const attribute of [...child.attributes]) if (attribute.name === 'id' || attribute.name.startsWith('data-') || attribute.name.startsWith('aria-')) child.removeAttribute(attribute.name);
        }
        copy.classList.remove('react-flow__node', 'selected');
        Object.assign(copy.style, { position: 'absolute', left: '0', top: '0', visibility: 'visible', transform: `scale(${flow.getZoom()})`, transformOrigin: 'top left', transition: 'none' });
        copy.inert = true;
        flight.append(copy); flightLayer.current?.append(flight);
        try {
          await animate(flight, [
            { translate: `${x}px ${y}px`, scale: '.55', opacity: .75 },
            { translate: '0 0', scale: '1', opacity: 1 },
          ], signal, 1000);
          // Reveal and remove the identical copy in one turn: no reset frame.
          revealPlacement?.();
        } finally { flight.remove(); }
      },
      async connect(source, target, signal) {
        await frameElement(source, signal); await frameElement(target, signal);
        await waitForGuide(signal);
        setTrace({ source, target });
        try {
          await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
          if (tracePath.current) await animate(tracePath.current, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], signal, 1500);
          // Keep the completed trace until the real relationship has been saved.
          return () => setTrace(undefined);
        } catch (error) { setTrace(undefined); throw error; }
      },
      async move(id, position, signal) {
        await frameElement(id, signal);
        await waitForGuide(signal);
        const card = useWorldStore.getState().cards.find(item => item.id === id)!;
        const dx = position.x - card.position.x, dy = position.y - card.position.y;
        const glue = useGlueStore.getState();
        const group = glueGroup(id, glue.bonds);
        const starts = new Map([...group].flatMap(key => {
          const node = flow.getNode(key); return node ? [[key, { ...node.position }] as const] : [];
        }));
        // Move React Flow's live coordinates, as a drag does. CSS translation
        // multiplied zoom twice and snapped back before the saved position arrived.
        const started = performance.now();
        let progress = 0;
        try {
          do {
            signal.throwIfAborted();
            progress = reducedMotion() ? 1 : Math.min(1, (performance.now() - started) / 1200);
            const eased = progress * progress * (3 - 2 * progress);
            flow.setNodes(nodes => nodes.map(node => {
              const start = starts.get(node.id);
              return start ? { ...node, position: { x: start.x + dx * eased, y: start.y + dy * eased } } : node;
            }));
            await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
          } while (progress < 1);
        } catch (error) {
          flow.setNodes(nodes => nodes.map(node => starts.has(node.id) ? { ...node, position: starts.get(node.id)! } : node));
          throw error;
        }
        signal.throwIfAborted();
        if (group.size > 1) {
          const end = beginGlueEdit();
          try {
            const boxes = Object.fromEntries([...group].filter(key => glue.boxes[key]).map(key => [key, { ...glue.boxes[key], x: glue.boxes[key].x + dx, y: glue.boxes[key].y + dy }]));
            useGlueStore.getState().setLayout(boxes);
            await useWorldStore.getState().updateCardPositions(Object.entries(boxes).map(([key, box]) => ({ id: key, position: nodePositionFromSurfacePosition(box, box.level) })));
            await persistGlue();
          } finally { end(); }
        } else await useWorldStore.getState().updateCardPositions([{ id, position }]);
      },
    };
    return tutorial.attach(bridge);
  }, [flow, focusSubjects, waitForGuide]);

  useLayoutEffect(() => {
    if (!trace) return;
    let frame = 0;
    const update = () => {
      const geometry = connectionGeometry(trace.source, trace.target);
      if (geometry && tracePath.current) {
        tracePath.current.setAttribute('d', geometry.path);
        tracePath.current.setAttribute('transform', geometry.transform);
      }
      frame = requestAnimationFrame(update);
    };
    update(); return () => cancelAnimationFrame(frame);
  }, [trace, connectionGeometry]);

  useEffect(() => { void tutorial.checkWelcome(); }, [sync]);
  useEffect(() => { setCompact(false); }, [step.id]);
  useEffect(() => {
    if (step.id === 'pan') origin.current = flow.screenToFlowPosition({ x: window.innerWidth * .73, y: window.innerHeight * .43 });
  }, [step.id, flow]);

  const targetElement = useCallback((target: Target) => {
    if (target.startsWith('legion-')) {
      const control = document.querySelector<HTMLElement>(`[data-tutorial="${target}"]`);
      if (control) return control;
      const id = useTutorialStore.getState().session?.refs.legion;
      return id ? nodeElement(id)?.querySelector<HTMLElement>('[data-tutorial="legion-open"]') ?? nodeElement(id) : null;
    }
    if (target === 'library' || target.startsWith('library-')) {
      const library = useCardLibrary.getState();
      if (!library.open || target === 'library') return document.querySelector<HTMLElement>('[data-tutorial="library"]');
      if (target === 'library-pack') {
        if (library.tab !== 'packs') return document.querySelector<HTMLElement>('[data-tutorial="library-tab-packs"]');
        const pack = starterPack(library.snapshot);
        return pack ? document.querySelector<HTMLElement>(`[data-pack-id="${CSS.escape(pack.definition.id)}"]`) : null;
      }
      return document.querySelector<HTMLElement>('[data-tutorial="deck"]') ?? document.querySelector<HTMLElement>('[data-tutorial="library-tab-cards"]');
    }
    if (target === 'zoom-controls') return document.querySelector<HTMLElement>('.world-canvas .world-controls');
    if (target === 'deck' || target === 'tools' || target === 'settings' || target.startsWith('model-')) {
      if (target.startsWith('model-') && !useWorldStore.getState().settingsOpen) return document.querySelector<HTMLElement>('[data-tutorial="settings"]');
      return document.querySelector<HTMLElement>(`[data-tutorial="${target}"]`)
        ?? document.querySelector<HTMLElement>('[data-tutorial="model-connection"]')
        ?? document.querySelector<HTMLElement>('[data-tutorial="models-tab"]');
    }
    const id = useTutorialStore.getState().session?.refs[target as Role];
    return id ? nodeElement(id) : null;
  }, []);

  useEffect(() => installTutorialInteractionGuard(() => {
    const current = useTutorialStore.getState();
    return { active: current.view === 'active', busy: current.busy, selectedIds: useWorldStore.getState().selectedCardIds,
      step: STEPS.find(item => item.id === current.session?.step) ?? STEPS[0], refs: current.session?.refs ?? {} };
  }, () => tutorial.pause()), []);

  const [resolvedTarget, setResolvedTarget] = useState<string>();
  const [hasConnection, setHasConnection] = useState(false);

  useEffect(() => {
    if (!active || s.busy || useNodeSurfaceStore.getState().dragging) return;
    const ids = step.target === 'minister' && ministerPanelId === s.session?.refs.minister ? [ministerPanelId]
      : step.id === 'sandbox-connect' ? [s.session?.refs.agent, s.session?.refs.sandbox]
      : step.role && ['inspector', 'workspace'].includes(surfaces[s.session?.refs[step.role] ?? ''] ?? '') ? [s.session?.refs[step.role]] : [];
    if (!ids.length) return;
    void focusSubjects(ids.filter((id): id is string => Boolean(id)));
    return () => { focusSequence.current++; };
  }, [active, step.id, surfaces, ministerPanelId, focusSubjects]);

  useLayoutEffect(() => {
    if (s.view === 'hidden') return;
    let frame = 0;
    let highlighted: HTMLElement[] = [];
    let lastTime = performance.now();
    let modelTargetHeight = 0;
    function place() {
      const placement = s.view === 'active' && target === 'deck';
      const placementType = step.role === 'ministerRole' ? 'core.minister-role' : step.role === 'agent' ? 'agent' : step.role === 'conversation' ? 'conversation' : step.role === 'sandbox' ? 'sandbox' : 'text';
      const placementCard = placement ? document.querySelector<HTMLElement>(`[data-palette-card="${placementType}"]`) : null;
      const element = placementCard ?? targetElement(target);
      const participants = (step.participants ?? []).flatMap(role => {
        const element = targetElement(role); return element ? [{ id: role, element }] : [];
      });
      const subjects: { id: string; element: HTMLElement }[] = [...participants, ...(element && !participants.some(item => item.element === element) ? [{ id: target, element }] : [])];
      const liveLayout = step.id === 'legion-return' ? document.querySelector<HTMLElement>('[data-tutorial="legion-layout"]') : null;
      if (liveLayout) subjects.push({ id: 'legion-layout', element: liveLayout });
      if (step.id === 'legion-layout') {
        for (const element of document.querySelectorAll<HTMLElement>('[data-tutorial="legion-edit"], [data-tutorial="legion-save"]'))
          subjects.push({ id: element.dataset.tutorial!, element });
      }
      const ministerControls = target === 'minister' && s.session?.refs.minister
        ? [...document.querySelectorAll<HTMLElement>(`[data-minister-for="${CSS.escape(s.session.refs.minister)}"], [data-tutorial-card-id="${CSS.escape(s.session.refs.minister)}"]`)]
          .filter(control => !control.hidden).map((element, i) => ({ id: `minister-control-${i}`, element })) : [];
      const library = useCardLibrary.getState();
      const deck = library.snapshot?.decks.find(item => item.id === library.snapshot?.active_deck_id);
      const candidates = s.view === 'active' && step.id === 'deck-build' && library.open && library.tab === 'cards'
        ? STARTER_CARDS.filter(id => !deck?.entries.some(entry => entry.kind === 'node' && entry.id === id)).flatMap(id => {
          const card = document.querySelector<HTMLElement>(`[data-library-card="${id}"]`);
          return card ? [{ id: `library-card-${id}`, element: card.closest<HTMLElement>('.library-card') ?? card }] : [];
        }) : [];
      const chooser = participants.length ? document.querySelector<HTMLElement>('.connection-dialog') : null;
      const regions = [...subjects, ...ministerControls, ...candidates, ...(chooser ? [{ id: 'capability-chooser', element: chooser }] : [])];
      const elements = regions.map(item => item.element);
      for (const old of highlighted) if (!elements.includes(old)) old.removeAttribute('data-tutorial-highlight');
      for (const next of elements) if (!highlighted.includes(next)) {
        if (s.view === 'active') next.setAttribute('data-tutorial-highlight', 'true');
        if (!candidates.some(item => item.element === next) && (target.startsWith('model-') || target.startsWith('library-'))) next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
      highlighted = elements;
      // Adding a model grows the same target; reveal its newly mounted inputs.
      if (target === 'model-list' && element && element.offsetHeight !== modelTargetHeight) {
        modelTargetHeight = element.offsetHeight;
        element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
      const visible = regions.map(item => ({ id: item.id, glow: !chooser || item.id === 'capability-chooser', ...(() => {
        const box = visibleBounds(item.element); return { x: box.x, y: box.y, width: box.width, height: box.height };
      })() })).filter(box => box.width > 0 && box.height > 0);
      if (placementArrow.current) {
        const box = placementCard ? visibleBounds(placementCard) : undefined;
        const show = box && box.width > 0 && box.height > 0;
        placementArrow.current.style.display = show ? '' : 'none';
        if (show) {
          placementArrow.current.style.left = `${box.x + box.width / 2 - 26}px`;
          placementArrow.current.style.top = `${Math.max(8, box.y - 82)}px`;
          placementArrow.current.dataset.card = placementType;
        }
      }
      const source = visible.find(box => candidates.some(item => item.id === box.id));
      const destination = document.querySelector<HTMLElement>('.component-palette .deck-stage[data-deck-destination]');
      const end = destination ? visibleBounds(destination) : undefined;
      if (deckArrow.current) {
        const show = source && end && end.width > 0 && end.height > 0;
        deckArrow.current.style.display = show ? '' : 'none';
        if (show) {
          const x = source.x + source.width - 8, y = source.y + source.height / 2;
          const tx = end.x + 16, ty = end.y + end.height / 2;
          const bend = Math.max(35, Math.abs(tx - x) * .45);
          deckArrow.current.setAttribute('d', `M ${x} ${y} C ${x + bend} ${y}, ${tx - bend} ${ty}, ${tx} ${ty}`);
          deckArrow.current.dataset.source = source.id;
        }
      }
      const pair = step.participants?.map(role => useTutorialStore.getState().session?.refs[role]);
      let route = pair?.length === 2 && pair[0] && pair[1] ? connectionGeometry(pair[0], pair[1]) : undefined;
      if (route && step.participants?.[0] === 'glueA' && participants.length === 2) {
        const [a, b] = participants.map(item => item.element.getBoundingClientRect());
        // Physical sticking has no capability curve. A simple corridor stays
        // inside the pair, even when their boundaries touch or overlap.
        route = { ...route, path: `M ${a.x + a.width / 2},${a.y + a.height / 2} L ${b.x + b.width / 2},${b.y + b.height / 2}`, transform: '' };
      }
      spotlight.current?.update(s.view === 'active' && !placement ? visible : [], s.view === 'active' && route ? {
        id: `route-${pair!.join('-')}`, path: route.path, transform: route.transform,
      } : undefined, !placement && step.id !== 'deck-build');
      // The character stays beside the interacting cards, even when an extra
      // tool is also illuminated. Including that distant toolbar in the bounds
      // would push the guide to an unrelated corner of the viewport.
      const nearby = source ? [new DOMRect(source.x, source.y, source.width, source.height)] : chooser ? [visibleBounds(chooser)] : participants.length
        ? participants.map(item => visibleBounds(item.element)).filter(box => box.width && box.height)
        : element ? [visibleBounds(element)] : [];
      const left = Math.min(...nearby.map(box => box.left)), top = Math.min(...nearby.map(box => box.top));
      const bounds = nearby.length ? new DOMRect(left, top, Math.max(...nearby.map(box => box.right)) - left, Math.max(...nearby.map(box => box.bottom)) - top) : undefined;
      setResolvedTarget(element?.dataset.tutorial);
      setHasConnection(Boolean(document.querySelector('[data-tutorial="model-credentials"]')));
      const width = window.innerWidth, height = window.innerHeight;
      let x = width / 2 - 80, y = height <= 700 ? 16 : Math.min(72, height * .09);
      if (!welcome) {
        const rect = bounds && bounds.width > 0 && bounds.height > 0 ? bounds : undefined;
        const bubbleWidth = Math.min(256, width - 32);
        if (rect) {
          // Prefer space beside the subject. Large workspaces leave a readable
          // margin at their top; never put the bubble on their composer.
          x = rect.left > bubbleWidth + 35 ? rect.left - bubbleWidth - 20 : rect.right + 18;
          y = rect.top + 60;
          if (x + bubbleWidth > width - 16) { x = Math.max(18, rect.left); y = rect.top - 80; }
        } else if (target === 'terrain') {
          const screen = flow.flowToScreenPosition(origin.current);
          x = screen.x; y = screen.y;
        } else { x = width * .57; y = height * .43; }
        if (target === 'deck') { x = Math.min(width - bubbleWidth - 20, (rect?.right ?? 260) + 24); y = height - 185; }
        if (target === 'tools' && !participants.length) { x = width - bubbleWidth - 78; y = height - 205; }
        const mascotOffset = (target === 'zoom-controls' || target === 'library-decks' || (rect && x + bubbleWidth <= rect.left)) ? bubbleWidth - 92 : 0;
        setRightGuide(mascotOffset > 0);
        if (target === 'zoom-controls' && rect) { x = rect.left + rect.width / 2 - mascotOffset - 46; y = rect.top - 108; }
        if (target === 'library-decks' && rect) { x = rect.left - bubbleWidth - 18; y = rect.top - 16; }
        x = Math.max(16, Math.min(width - bubbleWidth - 16, x));
        const bubbleHeight = guide.current?.querySelector<HTMLElement>('.tutorial-bubble')?.offsetHeight ?? 180;
        const obstacles = [...document.querySelectorAll<HTMLElement>(target.startsWith('model-')
          ? '.settings-dialog input, .settings-dialog select, .settings-dialog button, .settings-dialog .field-label'
          : libraryOpen ? '.library-tabs button, .pack-touch-area, .library-card-inspect, .library-card-add, .component-palette' : '.world-canvas .react-flow__node, .top-bar, .component-palette, .map-tools, .world-controls, .react-flow__minimap, .minister-presence:not([hidden]), .minister-panel, .toast-stack, .edge-inspector, .connection-dialog')]
          .map(element => element.getBoundingClientRect()).filter(box => box.width > 0 && box.height > 0);
        const key = source ? source.id : chooser ? 'capability-chooser' : participants.length ? participants.map(item => item.id).join(':') : target;
        const placed = placeGuide({ x, y }, rect, { width: bubbleWidth, height: bubbleHeight }, { width, height }, obstacles, mascotOffset, anchor.current?.key === key ? anchor.current : undefined);
        anchor.current = { ...placed, key };
        x = placed.x; y = placed.y;
      }
      if (workspaceHost) {
        // Reserve space inside the native modal so guidance never covers docking controls.
        const bubbleHeight = guide.current?.querySelector<HTMLElement>('.tutorial-bubble')?.offsetHeight ?? 220;
        workspaceHost.style.setProperty('--tutorial-guide-height', `${bubbleHeight + 136}px`);
        x = width - 278;
        y = width >= 1100 ? 80 + bubbleHeight : height - 142;
      }
      setPosition(previous => Math.abs(previous.x - x) + Math.abs(previous.y - y) > 1 ? { x, y } : previous);
      const now = performance.now();
      const trip = travel.current!.update({ x, y }, now - lastTime, welcome || s.view === 'paused' || reducedMotion());
      currentPosition.current = trip.position;
      lastTime = now;
      guideArrived.current = !trip.moving;
      if (guide.current) {
        guide.current.style.transform = `translate(${currentPosition.current.x}px, ${currentPosition.current.y}px)`;
        guide.current.dataset.moving = String(trip.moving);
        guide.current.dataset.travelPhase = trip.phase;
        const portal = trip.phase === 'departing' || trip.phase === 'arriving';
        const visibility = trip.phase === 'departing' ? 1 - trip.progress : trip.progress;
        guide.current.style.setProperty('--portal-open', String(portal ? Math.min(1, Math.sin(Math.PI * trip.progress) * 1.8) : 0));
        guide.current.style.setProperty('--traveler-opacity', String(portal ? visibility : 1));
        guide.current.style.setProperty('--traveler-scale', String(portal ? .04 + .96 * visibility : 1));
        guide.current.style.setProperty('--traveler-x', `${portal ? (trip.phase === 'departing' ? 14 : -14) * (1 - visibility) : 0}px`);
        const bubble = guide.current.querySelector<HTMLElement>('.tutorial-bubble');
        bubble?.toggleAttribute('inert', trip.moving);
        bubble?.setAttribute('aria-hidden', String(trip.moving));
      }
      frame = requestAnimationFrame(place);
    }
    place();
    return () => { cancelAnimationFrame(frame); highlighted.forEach(element => element.removeAttribute('data-tutorial-highlight')); };
  }, [welcome, libraryOpen, workspaceHost, s.view, target, step.id, step.participants, targetElement, flow, connectionGeometry, cards.length]);

  if (s.view === 'hidden') return <QuickStartGuide />;
  const motion: GuideMotion = welcome || s.view === 'paused' ? 'idle' : s.busy ? 'think' : step.id === 'enter' ? 'enter' : step.expects ? 'indicate' : 'speak';
  const role = step.role;
  const settingsStep = step.target.startsWith('model-');
  const needsSettings = settingsStep && resolvedTarget === 'settings';
  const needsModelsTab = settingsStep && resolvedTarget === 'models-tab';
  const needsConnection = settingsStep && step.target !== 'model-connection' && resolvedTarget === 'model-connection';
  const waitingForTarget = settingsStep && (needsSettings || needsModelsTab || needsConnection);
  const libraryStep = step.target === 'library' || step.target.startsWith('library-');
  const dialogue = ['legion-layout', 'legion-return'].includes(step.id) && !workspaceId ? 'Open Workspace mode again to continue arranging your Legion.'
    : libraryStep && !libraryOpen && step.id !== 'deck' ? 'Open the Library again to continue preparing your deck.' : needsSettings ? 'Click settings to continue setting up your model.'
    : needsModelsTab ? 'Click Models here.'
    : needsConnection ? 'Add or select a connection first.' : reviewing ? step.result!.dialogue : step.dialogue;
  const missing = role && step.expects !== 'place' && step.expects !== 'delete' && !cards.some(card => card.id === s.session?.refs[role]);
  return createPortal(<div hidden={welcome && libraryOpen} className={`onboarding-layer ${welcome ? 'is-welcome' : 'is-tutorial'} ${settingsStep ? 'is-settings-guide' : ''} ${libraryOpen && !welcome ? 'is-library-guide' : ''} ${step.participants ? 'is-interaction-guide' : ''}`}>
    <Spotlight ref={spotlight} />
    <div ref={placementArrow} className="tutorial-placement-arrow" aria-hidden="true" style={{ display: 'none' }}>
      <svg viewBox="0 0 52 72"><path d="M 18 4 H 34 V 40 H 47 L 26 65 L 5 40 H 18 Z" /></svg>
    </div>
    <svg className="tutorial-deck-arrow" aria-hidden="true">
      <defs><marker id={arrowId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 1 1 L 8 5 L 1 9" /></marker></defs>
      <path ref={deckArrow} className="tutorial-deck-arrow-path" markerEnd={`url(#${arrowId})`} style={{ display: 'none' }} />
    </svg>
    <div className={`onboarding-logo-ring ${welcome ? '' : 'has-entered'}`}><OawGuide ringOnly /></div>
    {welcome && <section className="onboarding-welcome" aria-label={t("Welcome to Open Agent World")}>
      <span className="onboarding-eyebrow">{t("A world of possibilities")}</span>
      <h1>{t("Open Agent World")}</h1>
      <p>{t("A little space. A few cards. Something entirely yours.")}</p>
      <BlueprintChooser />
      <div className="onboarding-actions">
        <button className="primary-button onboarding-start" disabled={s.busy || sync === 'offline'} onClick={() => void tutorial.start()}><span>{t("Start Tutorial")}<small>{t("Recommended · A guided walk through your first world")}</small></span><ArrowRight size={19} /></button>
        <button className="onboarding-text-button" disabled={s.busy} onClick={() => void tutorial.directly()}>{t("Start Empty")}</button>
      </div>
      {s.error && <p className="onboarding-error" role="alert">{s.error}</p>}
    </section>}
    <div ref={flightLayer} className="tutorial-flight-layer" aria-hidden="true" />
    {trace && <svg className="tutorial-connection-trace" aria-hidden="true"><path ref={tracePath} pathLength="1" vectorEffect="non-scaling-stroke" /></svg>}
    <div ref={guide} className={`tutorial-guide ${welcome ? 'is-logo' : ''} ${compact ? 'is-compact' : ''} ${rightGuide ? 'is-right-guide' : ''}`}
      style={{ '--guide-x': `${position.x}px`, '--guide-y': `${position.y}px` } as CSSProperties}>
      {!welcome && <div className="tutorial-bubble" role="region" aria-label={t("Tutorial guide")} data-step={step.id} data-reviewing={reviewing}>
        <header>{active && <button className="onboarding-icon-button" disabled={s.busy} aria-label={t("Pause tutorial")} title={t("Pause tutorial")} onClick={() => tutorial.pause()}><Pause size={13} /></button>}<span>{s.view === 'paused' ? t("Your walk is saved") : `${step.chapter + 1} / ${CHAPTERS.length} · ${t(CHAPTERS[step.chapter])}`}</span>
          <button className="onboarding-icon-button" aria-label={compact ? t("Show tutorial hint") : t("Minimize tutorial hint")} onClick={() => setCompact(value => !value)}><ChevronDown size={13} /></button>
          <button className="onboarding-icon-button" aria-label={t("Skip tutorial")} title={t("Skip tutorial and tidy temporary props")} onClick={() => void tutorial.exit('skipped')}><X size={13} /></button>
        </header>
        {!compact && <>
          <p key={`${step.id}-${reviewing}`} className="tutorial-dialogue" aria-live="polite" aria-atomic="true">{s.view === 'paused' ? t("Pick up where you left off, or start a new walk. Your own cards stay with you.") : missing ? t("Looks like that card moved away or was removed. I can help you find it or return to placing one.") : t(dialogue)}</p>
          {s.error ? <p className="onboarding-error" role="alert">{s.error}</p> : sync === 'offline' ? <small role="status">{t("Waiting for the world service to reconnect. Your progress is saved.")}</small> : step.hint && <small>{t(step.hint)}</small>}
          {active && <small className="tutorial-lock-hint">{t("Other controls are locked. Pause to explore freely.")}</small>}
          {active && step.id === 'minister' && s.session?.refs.agent && <>
            <div className="minister-promotion-equation"><span>{t('Agent')}</span><b>+</b><span>{t('Minister role')}</span><b>=</b><span>{t('Minister Agent')}</span></div>
          </>}
          <footer>
            {s.view === 'paused' ? <>
              <button className="tutorial-next" disabled={s.busy} onClick={() => tutorial.resume()}>{t("Resume")}</button>
              <button className="onboarding-icon-button" disabled={s.busy} onClick={() => void tutorial.replay()} aria-label={t("Restart tutorial")}><RotateCcw size={14} /></button>
              {s.error && <button className="onboarding-text-button" disabled={s.busy} onClick={() => void tutorial.exit('skipped')}>{t("Retry cleanup")}</button>}
            </> : <>
              {step.button && <button className="tutorial-next" disabled={s.busy || sync === 'offline' || waitingForTarget || (['deck-build', 'minister'].includes(step.id) && !s.ready)} onClick={() => void tutorial.continue()}>{s.busy ? t("One moment…") : t(reviewing ? "Continue" : step.button)}<ArrowRight size={13} /></button>}
              {!step.button && <span className="tutorial-waiting"><i />{s.busy ? t("One moment…") : s.ready ? t("Settings saved") : t("Your turn")}</span>}
              {((step.expects && !settingsStep && step.id !== 'model-settings') || s.error) && <button className="onboarding-icon-button" aria-label={t("Recover this step")} title={t("Find the card, or recover a missing card")} disabled={s.busy} onClick={() => void tutorial.recover()}><Compass size={15} /></button>}
            </>}
          </footer>
          {active && step.optional && <button className="onboarding-text-button tutorial-optional" disabled={s.busy || sync === 'syncing' || (step.id === 'model-connection' && (waitingForTarget || !hasConnection)) || (step.id === 'configure' && !['inspector', 'workspace'].includes(surfaces[s.session?.refs.agent ?? ''] ?? ''))} onClick={() => void tutorial.continue()}>{s.ready ? t("Continue with these settings") : t(step.optional)}</button>}
        </>}
      </div>}
      <div className="tutorial-mascot"><div className="tutorial-portal" aria-hidden="true" /><div className="tutorial-traveler"><OawGuide motion={motion} inLogo={welcome} movementTarget={guide} celebration={s.celebration} /></div></div>
    </div>
  </div>, workspaceHost ?? document.body);
}
