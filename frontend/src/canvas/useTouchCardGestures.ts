import { Position, useReactFlow, useStoreApi } from '@xyflow/react';
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react';
import { useTouchHold, type TouchPoint } from '../interaction/useTouchHold';
import { tutorialAllowsCanvasTarget } from '../onboarding/interactionGuard';
import { validateConnection } from '../state/relationships';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { CanvasNode } from '../cards/types';
import type { CanvasEdge } from '../edges/SemanticEdge';
import { useInteractionEnvironment } from '../interaction/environment';

const CONTROLS = 'button, input, textarea, select, a, summary, [role="button"], [role="textbox"], [contenteditable]:not([contenteditable="false"]), .nodrag, .nowheel';

interface Options {
  disabled: boolean;
  glueActive: boolean;
  move: {
    canStart(id: string): boolean;
    start(id: string, point: TouchPoint): boolean;
    update(point: TouchPoint): void;
    release(point: TouchPoint): void;
    cancel(): void;
  };
}

/** One hold recognizer: card crown/grip picks the card up; body draws a link.
 * Native React Flow still owns navigation before the hold and after cancellation. */
export function useTouchCardGestures(root: RefObject<HTMLDivElement>, options: Options) {
  const flow = useReactFlow<CanvasNode, CanvasEdge>();
  const store = useStoreApi<CanvasNode, CanvasEdge>();
  const compact = useInteractionEnvironment(state => state.compact);
  const moveCallbacks = useRef(options.move); moveCallbacks.current = options.move;
  type Candidate = { id: string; element: HTMLElement; intent: 'move' | 'connect'; point: TouchPoint; pointerType: string };
  const candidate = useRef<Candidate>();
  const active = useRef<Candidate & { target?: HTMLElement; moved?: boolean }>();
  const lastPoint = useRef<TouchPoint>();
  const panFrame = useRef<number>();
  const [preview, setPreview] = useState<{ source: string; target?: string; valid?: boolean }>();

  const clear = () => {
    const current = active.current;
    candidate.current = undefined;
    if (!current) return;
    active.current = undefined;
    if (panFrame.current !== undefined) cancelAnimationFrame(panFrame.current);
    panFrame.current = undefined;
    lastPoint.current = undefined;
    current.element.removeAttribute('data-touch-hold');
    current.element.removeAttribute('data-touch-connection');
    current.element.removeAttribute('data-touch-move');
    current.target?.removeAttribute('data-touch-connection');
    if (current.intent === 'connect') {
      store.getState().cancelConnection();
      useNodeSurfaceStore.getState().endConnection();
    }
    setPreview(undefined);
  };
  const update = (point: TouchPoint) => {
    const current = active.current;
    if (!current) return;
    lastPoint.current = { clientX: point.clientX, clientY: point.clientY, pointerId: point.pointerId };
    const source = flow.getInternalNode(current.id);
    if (!source || !current.element.isConnected) {
      if (current.intent === 'move') moveCallbacks.current.cancel();
      clear(); return;
    }
    if (current.intent === 'move') {
      current.moved ||= Math.hypot(point.clientX - current.point.clientX, point.clientY - current.point.clientY) > 3;
      if (current.moved) moveCallbacks.current.update(point);
      return;
    }
    const hit = document.elementFromPoint(point.clientX, point.clientY);
    const element = hit?.closest<HTMLElement>('.react-flow__node');
    const target = element?.closest('.react-flow')?.id === 'oaw-world-map'
      ? flow.getInternalNode(element.dataset.id!) : undefined;
    const world = useWorldStore.getState();
    const sourceCard = world.cards.find(card => card.id === current.id);
    const targetCard = world.cards.find(card => card.id === target?.id);
    const destination = target && target.id !== source.id ? target : undefined;
    const valid = !!(sourceCard && targetCard && destination && !targetCard.ephemeral
      && destination.connectable !== false && !destination.data.equipmentDetail
      && tutorialAllowsCanvasTarget(destination.id, 'connect')
      && validateConnection(world.catalog, source.id, destination.id, sourceCard.type, targetCard.type, world.edges, world.cards).valid);
    const targetElement = destination ? element ?? undefined : undefined;
    if (current.target !== targetElement) current.target?.removeAttribute('data-touch-connection');
    current.target = targetElement;
    targetElement?.setAttribute('data-touch-connection', valid ? 'valid' : 'invalid');
    const bounds = root.current!.getBoundingClientRect();
    const pointer = { x: point.clientX - bounds.left, y: point.clientY - bounds.top };
    const from = { x: source.internals.positionAbsolute.x + (source.measured.width ?? source.width ?? 0) / 2, y: source.internals.positionAbsolute.y };
    store.getState().updateConnection({
      inProgress: true, isValid: destination ? valid : null, from, fromNode: source,
      fromHandle: { id: 'boundary-top', nodeId: source.id, type: 'source', position: Position.Top, x: 0, y: 0, width: 1, height: 1 },
      fromPosition: Position.Top, to: pointer, pointer,
      toNode: destination ?? null, toHandle: null, toPosition: Position.Bottom,
    });
    setPreview(previous => previous?.source === source.id && previous.target === destination?.id && previous.valid === valid
      ? previous : { source: source.id, target: destination?.id, valid });
    return valid ? destination?.id : undefined;
  };
  const panAtEdge = () => {
    const point = lastPoint.current;
    const element = root.current;
    if (!active.current || !point || !element) return;
    const bounds = element.getBoundingClientRect();
    const speed = (value: number, low: number, high: number) => value < low || value > high ? 0
      : value < low + 32 ? 8 * (low + 32 - value) / 32
      : value > high - 32 ? -8 * (value - high + 32) / 32 : 0;
    const delta = { x: speed(point.clientX, bounds.left, bounds.right), y: speed(point.clientY, bounds.top, bounds.bottom) };
    if ((active.current.intent === 'connect' || active.current.moved) && (delta.x || delta.y)) void store.getState().panBy(delta).then(() => {
      if (active.current && lastPoint.current) update(lastPoint.current);
    });
    panFrame.current = requestAnimationFrame(panAtEdge);
  };
  const hold = useTouchHold({
    disabled: options.disabled,
    allowMouse: true,
    onHold(point) {
      const source = candidate.current;
      if (!source || !flow.getInternalNode(source.id) || !tutorialAllowsCanvasTarget(source.id, source.intent === 'move' ? 'move' : 'connect')) return;
      if (source.intent === 'move' && !moveCallbacks.current.start(source.id, point)) return;
      active.current = source;
      source.element.dataset.touchHold = 'active';
      if (source.intent === 'move') source.element.dataset.touchMove = 'active';
      else {
        source.element.dataset.touchConnection = 'source';
        useNodeSurfaceStore.getState().beginConnection(source.id);
      }
      update(point);
      panFrame.current = requestAnimationFrame(panAtEdge);
    },
    onMove: update,
    onRelease(point) {
      if (active.current?.intent === 'move') {
        update(point);
        moveCallbacks.current.release(point);
        clear();
        return;
      }
      const source = active.current?.id;
      const target = update(point);
      clear();
      // Revalidate against the current world before showing capabilities.
      if (source && target) useWorldStore.getState().requestConnection(source, target);
    },
    onCancel() { if (active.current?.intent === 'move') moveCallbacks.current.cancel(); clear(); },
  });
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const move = (event: TouchEvent) => {
      if (active.current && event.touches.length === 1) { event.preventDefault(); event.stopPropagation(); }
    };
    const start = (event: TouchEvent) => {
      if (event.target instanceof Element && event.target.closest('.semantic-handle')) event.stopPropagation();
    };
    const mouseMove = (event: MouseEvent) => {
      if (active.current?.pointerType !== 'mouse') return;
      event.preventDefault(); event.stopImmediatePropagation();
    };
    element.addEventListener('touchstart', start, true);
    element.addEventListener('touchmove', move, { capture: true, passive: false });
    window.addEventListener('mousemove', mouseMove, { capture: true, passive: false });
    return () => { element.removeEventListener('touchstart', start, true); element.removeEventListener('touchmove', move, true); window.removeEventListener('mousemove', mouseMove, true); };
  }, [root]);

  return {
    preview,
    onPointerDownCapture(event: ReactPointerEvent<HTMLDivElement>) {
      if (!['touch', 'mouse'].includes(event.pointerType) || !event.isPrimary || options.disabled || !(event.target instanceof Element)) return;
      const target = event.target;
      if (target.closest('.react-flow')?.id !== 'oaw-world-map' || target.closest(CONTROLS)) return;
      const element = target.closest<HTMLElement>('.react-flow__node');
      const node = element && flow.getInternalNode(element.dataset.id!);
      if (!node || node.data.card.ephemeral || node.data.equipmentDetail) return;
      const crown = target.closest('.node-drag-region, .container-drag-region');
      const box = element.getBoundingClientRect();
      const inCrown = event.clientY < box.top + Math.min(44, box.height * .35);
      const moving = inCrown && (!!target.closest('.touch-card-grip') || !!crown);
      if (event.pointerType === 'mouse' && (!compact || !moving)) return;
      if (moving ? !options.move.canStart(node.id) || !tutorialAllowsCanvasTarget(node.id, 'move')
        : event.pointerType !== 'touch' || options.glueActive || node.connectable === false || !tutorialAllowsCanvasTarget(node.id, 'connect')) return;
      candidate.current = { id: node.id, element, intent: moving ? 'move' : 'connect',
        point: { clientX: event.clientX, clientY: event.clientY, pointerId: event.pointerId }, pointerType: event.pointerType };
      hold.bind.onPointerDown(event);
    },
    onClickCapture: hold.bind.onClickCapture,
    onContextMenuCapture: hold.bind.onContextMenu,
    onLostPointerCapture: hold.bind.onLostPointerCapture,
  };
}
