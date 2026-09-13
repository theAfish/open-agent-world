import { t, useLocale } from "../i18n";
import { getNodesBounds, getViewportForBounds, useReactFlow, useViewport } from '@xyflow/react';
import { ArrowRight, ChevronDown, Compass, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { beginGlueEdit, glueGroup, persistGlue, useGlueStore } from '../state/glue';
import { nodePositionFromSurfacePosition } from '../canvas/nodeDisplacement';
import { OawGuide, type GuideMotion } from './OawGuide';
import { CHAPTERS, STEPS, type Role, type Target } from './steps';
import { tutorial, useTutorialStore, type GuideVisuals } from './controller';
import './onboarding.css';
import { placeGuide, vacantPosition } from './placement';

function nodeElement(id: string) { return document.querySelector<HTMLElement>(`.world-canvas > .react-flow .react-flow__node[data-id="${CSS.escape(id)}"]`); }
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
  const sync = useWorldStore(w => w.syncState);
  const cards = useWorldStore(w => w.cards);
  const surfaces = useNodeSurfaceStore(w => w.surfaceLevels);
  const flow = useReactFlow();
  const viewport = useViewport();
  const [compact, setCompact] = useState(false);
  const [position, setPosition] = useState({ x: window.innerWidth / 2 - 80, y: window.innerHeight * (window.innerHeight <= 650 ? .32 : .38) - 112 });
  const [trace, setTrace] = useState<{ a: { x: number; y: number }; b: { x: number; y: number } }>();
  const tracePath = useRef<SVGPathElement>(null);
  const guide = useRef<HTMLDivElement>(null);
  const focusSequence = useRef(0);
  const welcome = s.view === 'welcome';
  const active = s.view === 'active';
  const step = STEPS.find(item => item.id === s.session?.step) ?? STEPS[0];
  const origin = useRef(flow.screenToFlowPosition({ x: window.innerWidth * .72, y: window.innerHeight * .42 }));

  const focusSubjects = useCallback(async (ids: string[]) => {
    const sequence = ++focusSequence.current;
    const deadline = performance.now() + 6000;
    let previous = '', stableFrames = 0;
    while (performance.now() < deadline && sequence === focusSequence.current) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const nodes = ids.flatMap(id => { const node = flow.getNode(id); return node ? [node] : []; });
      const levels = useNodeSurfaceStore.getState().surfaceLevels;
      if (nodes.length !== ids.length || nodes.some(node => node.type !== 'minister' && node.data.surfaceLevel !== (levels[node.id] ?? 'preview'))) continue;
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
      const { width, height } = useWorldStore.getState().viewport;
      const gutter = width >= 900 ? 290 : 0;
      const viewport = getViewportForBounds(bounds, width - gutter - 20, Math.max(280, height - 150), .3, .85, .25);
      await flow.setViewport({ ...viewport, x: viewport.x + gutter, y: viewport.y + 28 }, { duration: reducedMotion() ? 0 : 350 });
      return;
    }
  }, [flow]);

  useEffect(() => {
    const bridge: GuideVisuals = {
      focus: focusSubjects,
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
        const box = element.getBoundingClientRect();
        const deck = document.querySelector('[data-tutorial="deck"]')?.getBoundingClientRect();
        const x = (deck?.left ?? 30) + (deck?.width ?? 160) / 2 - box.left;
        const y = (deck?.top ?? window.innerHeight - 100) - box.top;
        await animate(element, [{ translate: `${x}px ${y}px`, scale: '.55', opacity: .3 }, { translate: '0 0', scale: '1', opacity: 1 }], signal);
      },
      async connect(source, target, signal) {
        const a = (await frameElement(source, signal)).getBoundingClientRect();
        const b = (await frameElement(target, signal)).getBoundingClientRect();
        setTrace({ a: { x: a.right, y: a.top + a.height / 2 }, b: { x: b.left, y: b.top + b.height / 2 } });
        useTutorialStore.setState({ target: 'conversation' });
        try {
          await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
          if (tracePath.current) await animate(tracePath.current, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], signal, 1050);
        } finally { setTrace(undefined); }
      },
      async move(id, position, signal) {
        const element = await frameElement(id, signal);
        const card = useWorldStore.getState().cards.find(item => item.id === id)!;
        const dx = position.x - card.position.x, dy = position.y - card.position.y;
        const glue = useGlueStore.getState();
        const group = glueGroup(id, glue.bonds);
        const elements = [...group].map(nodeElement).filter((el): el is HTMLElement => Boolean(el));
        // Animate the existing entities; commit through the same position and
        // shared glue paths as the canvas when the visual gesture finishes.
        await Promise.all((elements.length ? elements : [element]).map(el => animate(el, [{ translate: '0 0' }, { translate: `${dx * flow.getZoom()}px ${dy * flow.getZoom()}px` }], signal)));
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
  }, [flow, focusSubjects]);

  useEffect(() => { void tutorial.checkWelcome(); }, [sync]);
  useEffect(() => { setCompact(false); }, [step.id]);
  useEffect(() => {
    if (step.id === 'pan') origin.current = flow.screenToFlowPosition({ x: window.innerWidth * .73, y: window.innerHeight * .43 });
  }, [step.id, flow]);

  const targetElement = useCallback((target: Target) => {
    if (target === 'deck' || target === 'tools') return document.querySelector<HTMLElement>(`[data-tutorial="${target}"]`);
    const id = useTutorialStore.getState().session?.refs[target as Role];
    return id ? nodeElement(id) : null;
  }, []);

  useEffect(() => {
    if (!active || s.busy || useNodeSurfaceStore.getState().dragging) return;
    const ids = step.id === 'sandbox-connect' ? [s.session?.refs.agent, s.session?.refs.sandbox]
      : step.role && ['inspector', 'workspace'].includes(surfaces[s.session?.refs[step.role] ?? ''] ?? '') ? [s.session?.refs[step.role]] : [];
    if (!ids.length) return;
    void focusSubjects(ids.filter((id): id is string => Boolean(id)));
    return () => { focusSequence.current++; };
  }, [active, step.id, surfaces, focusSubjects]);

  useLayoutEffect(() => {
    if (s.view === 'hidden') return;
    let frame = 0;
    const target = s.target ?? step.target;
    let highlighted: HTMLElement | null = null;
    function place() {
      const element = targetElement(target);
      if (element !== highlighted) {
        highlighted?.removeAttribute('data-tutorial-highlight');
        highlighted = element;
        highlighted?.setAttribute('data-tutorial-highlight', 'true');
      }
      const width = window.innerWidth, height = window.innerHeight;
      let x = width / 2 - 80, y = height * (height <= 650 ? .32 : .38) - 112;
      if (!welcome) {
        const rect = targetElement(target)?.getBoundingClientRect();
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
        if (target === 'tools') { x = width - bubbleWidth - 78; y = height - 205; }
        x = Math.max(16, Math.min(width - bubbleWidth - 16, x));
        const bubbleHeight = guide.current?.querySelector<HTMLElement>('.tutorial-bubble')?.offsetHeight ?? 180;
        const obstacles = [...document.querySelectorAll<HTMLElement>('.world-canvas .react-flow__node, .top-bar, .component-palette, .map-tools, .world-controls, .minister-presence:not([hidden]), .minister-panel, .toast-stack, .edge-inspector')]
          .map(element => element.getBoundingClientRect()).filter(box => box.width > 0 && box.height > 0);
        const placed = placeGuide({ x, y }, rect, { width: bubbleWidth, height: bubbleHeight }, { width, height }, obstacles);
        x = placed.x; y = placed.y;
      }
      setPosition(previous => Math.abs(previous.x - x) + Math.abs(previous.y - y) > 1 ? { x, y } : previous);
      frame = requestAnimationFrame(place);
    }
    place();
    return () => { cancelAnimationFrame(frame); highlighted?.removeAttribute('data-tutorial-highlight'); };
  }, [welcome, s.view, s.target, step.target, targetElement, flow, viewport.x, viewport.y, viewport.zoom, cards.length]);

  if (s.view === 'hidden') return null;
  const motion: GuideMotion = welcome || s.view === 'paused' ? 'idle' : s.busy ? 'think' : step.id === 'enter' ? 'enter' : step.expects ? 'indicate' : 'speak';
  const role = step.role;
  const missing = role && step.expects !== 'place' && step.expects !== 'delete' && !cards.some(card => card.id === s.session?.refs[role]);
  return <div className={`onboarding-layer ${welcome ? 'is-welcome' : 'is-tutorial'}`}>
    <div className={`onboarding-logo-ring ${welcome ? '' : 'has-entered'}`}><OawGuide ringOnly /></div>
    {welcome && <section className="onboarding-welcome" aria-label={t("Welcome to Open Agent World")}>
      <span className="onboarding-eyebrow">{t("A world of possibilities")}</span>
      <h1>{t("Open Agent World")}</h1>
      <p>{t("A little space. A few cards. Something entirely yours.")}</p>
      <div className="onboarding-actions">
        <button className="primary-button onboarding-start" disabled={s.busy || sync === 'offline'} onClick={() => void tutorial.start()}><span>{t("Start Tutorial")}<small>{t("A guided walk through your first world")}</small></span><ArrowRight size={19} /></button>
        <button className="secondary-button" disabled={s.busy || sync === 'offline'} onClick={() => void tutorial.minister()}>{t("Place Minister Card")}</button>
        <button className="onboarding-text-button" disabled={s.busy} onClick={() => void tutorial.directly()}>{t("Start Directly")}</button>
      </div>
      {s.error && <p className="onboarding-error" role="alert">{s.error}</p>}
    </section>}
    {trace && <svg className="tutorial-connection-trace" aria-hidden="true"><path ref={tracePath} d={`M ${trace.a.x},${trace.a.y} C ${trace.a.x + 65},${trace.a.y} ${trace.b.x - 65},${trace.b.y} ${trace.b.x},${trace.b.y}`} pathLength="1" /></svg>}
    <div ref={guide} className={`tutorial-guide ${welcome ? 'is-logo' : ''} ${compact ? 'is-compact' : ''}`}
      style={{ '--guide-x': `${position.x}px`, '--guide-y': `${position.y}px` } as CSSProperties}>
      {!welcome && <div className="tutorial-bubble" role="region" aria-label={t("Tutorial guide")} data-step={step.id}>
        <header><span>{s.view === 'paused' ? t("Your walk is saved") : `${step.chapter + 1} / ${CHAPTERS.length} · ${t(CHAPTERS[step.chapter])}`}</span>
          <button className="onboarding-icon-button" aria-label={compact ? t("Show tutorial hint") : t("Minimize tutorial hint")} onClick={() => setCompact(value => !value)}><ChevronDown size={13} /></button>
          <button className="onboarding-icon-button" aria-label={t("Skip tutorial")} title={t("Skip tutorial and tidy temporary props")} onClick={() => void tutorial.exit('skipped')}><X size={13} /></button>
        </header>
        {!compact && <>
          <p aria-live="polite" aria-atomic="true">{s.view === 'paused' ? t("Pick up where you left off, or start a new walk. Your own cards stay with you.") : missing ? t("Looks like that card moved away or was removed. I can help you find it or return to placing one.") : t(step.dialogue)}</p>
          {s.error ? <p className="onboarding-error" role="alert">{s.error}</p> : sync === 'offline' ? <small role="status">{t("Waiting for the world service to reconnect. Your progress is saved.")}</small> : step.hint && <small>{t(step.hint)}</small>}
          <footer>
            {s.view === 'paused' ? <>
              <button className="tutorial-next" disabled={s.busy} onClick={() => tutorial.resume()}>{t("Resume")}</button>
              <button className="onboarding-icon-button" disabled={s.busy} onClick={() => void tutorial.replay()} aria-label={t("Restart tutorial")}><RotateCcw size={14} /></button>
              {s.error && <button className="onboarding-text-button" disabled={s.busy} onClick={() => void tutorial.exit('skipped')}>{t("Retry cleanup")}</button>}
            </> : <>
              {step.button && <button className="tutorial-next" disabled={s.busy || sync === 'offline'} onClick={() => void tutorial.continue()}>{s.busy ? t("One moment…") : t(step.button)}<ArrowRight size={13} /></button>}
              {!step.button && <span className="tutorial-waiting"><i />{s.busy ? t("One moment…") : s.ready ? t("Settings saved") : t("Your turn")}</span>}
              {(step.expects || s.error) && <button className="onboarding-icon-button" aria-label={t("Recover this step")} title={t("Find the card, or recover a missing card")} disabled={s.busy} onClick={() => void tutorial.recover()}><Compass size={15} /></button>}
            </>}
          </footer>
          {active && step.optional && <button className="onboarding-text-button tutorial-optional" disabled={s.busy || sync === 'syncing' || (step.id === 'configure' && !['inspector', 'workspace'].includes(surfaces[s.session?.refs.agent ?? ''] ?? ''))} onClick={() => void tutorial.continue()}>{s.ready ? t("Continue with these settings") : t(step.optional)}</button>}
        </>}
      </div>}
      <div className="tutorial-mascot"><OawGuide motion={motion} inLogo={welcome} movementTarget={guide} celebration={s.celebration} /></div>
    </div>
  </div>;
}
