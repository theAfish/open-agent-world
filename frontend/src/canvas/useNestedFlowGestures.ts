import { useEffect, useRef, useState, type RefObject } from "react";
import { useReactFlow, type Node, type XYPosition } from "@xyflow/react";

/** React Flow's native HTML pointer math assumes unscaled ancestors. Work in the
 * owning canvas's coordinates so nested maps follow the pointer at any world zoom. */
export function useNestedFlowGestures(root: RefObject<HTMLElement>, onDragEnd: (nodes: Node[]) => void) {
  const flow = useReactFlow();
  const finishRef = useRef(onDragEnd);
  finishRef.current = onDragEnd;
  const [selection, setSelection] = useState<{ x: number; y: number; width: number; height: number } | null>(null);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const local = (event: { clientX: number; clientY: number }): XYPosition => {
      const rect = element.getBoundingClientRect();
      return { x: (event.clientX - rect.left) * element.clientWidth / rect.width,
        y: (event.clientY - rect.top) * element.clientHeight / rect.height };
    };
    type Gesture = { pointer: number; start: XYPosition; viewport: ReturnType<typeof flow.getViewport>; nodes: Node[]; selected: Set<string>; mode: "pan" | "nodes" | "select"; moved: boolean };
    let gesture: Gesture | undefined;
    const pointers = new Map<number, XYPosition>();
    let pinch: { distance: number; anchor: XYPosition; viewport: ReturnType<typeof flow.getViewport> } | undefined;
    let suppressClick = false;
    const finish = (cancelled: boolean, mounted = true) => {
      const current = gesture;
      gesture = undefined;
      if (current?.moved && current.mode === 'nodes') {
        const starts = new Map(current.nodes.map(node => [node.id, node.position]));
        flow.setNodes(nodes => nodes.map(node => ({ ...node, dragging: false,
          ...(cancelled ? { selected: current.selected.has(node.id) } : {}),
          ...(cancelled && starts.has(node.id) ? { position: starts.get(node.id)! } : {}) })));
        if (!cancelled) finishRef.current(flow.getNodes().filter(node => starts.has(node.id)));
      }
      if (cancelled && current?.mode === 'select') flow.setNodes(nodes => nodes.map(node => ({ ...node, selected: current.selected.has(node.id) })));
      if (mounted) setSelection(null);
    };
    const startPinch = () => {
      const [a, b] = [...pointers.values()], viewport = flow.getViewport();
      const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      pinch = { distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), viewport,
        anchor: { x: (center.x - viewport.x) / viewport.zoom, y: (center.y - viewport.y) / viewport.zoom } };
      suppressClick = true;
    };
    const capture = (pointer: number) => {
      try { element.setPointerCapture?.(pointer); }
      catch { /* Window listeners still own an active pointer outside the pane. */ }
    };
    const release = (pointer: number) => { if (element.hasPointerCapture?.(pointer)) element.releasePointerCapture(pointer); };
    const down = (event: PointerEvent) => {
      // A drag released outside this canvas may never send its click here.
      // Only suppress the drag's click, never a later click on a control.
      suppressClick = false;
      if (event.button !== 0 && event.button !== 1) return;
      const target = event.target as Element;
      if (target.closest('button,input,select,textarea,a,summary,[contenteditable],.react-flow__controls')) return;
      const nodeElement = target.closest<HTMLElement>('.react-flow__node');
      const node = nodeElement ? flow.getNode(nodeElement.dataset.id!) : undefined;
      if (!node && !target.closest('.react-flow__pane') && event.button !== 1) return;
      if (event.pointerType === 'touch') {
        pointers.set(event.pointerId, local(event));
        if (pointers.size >= 2) {
          // A second finger changes ownership to camera navigation, never a
          // partially completed node move or marquee selection.
          finish(true);
          if (pointers.size === 2) startPinch();
          pointers.forEach((_point, pointer) => capture(pointer));
          suppressClick = true;
          event.preventDefault(); event.stopPropagation();
          return;
        }
      }
      const mode = event.button === 1 ? "pan" : event.shiftKey ? "select" : node ? "nodes" : "pan";
      const nodes = node ? node.selected ? flow.getNodes().filter(n => n.selected) : [node] : [];
      gesture = { pointer: event.pointerId, start: local(event), viewport: flow.getViewport(), nodes, selected: new Set(flow.getNodes().filter(node => node.selected).map(node => node.id)), mode, moved: false };
      event.stopPropagation();
      if (event.button === 1) event.preventDefault();
    };
    const move = (event: PointerEvent) => {
      if (pointers.has(event.pointerId)) pointers.set(event.pointerId, local(event));
      if (pinch && pointers.has(event.pointerId)) {
        const [a, b] = [...pointers.values()];
        const zoom = Math.max(.1, Math.min(2, pinch.viewport.zoom * Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance));
        void flow.setViewport({ zoom, x: (a.x + b.x) / 2 - pinch.anchor.x * zoom, y: (a.y + b.y) / 2 - pinch.anchor.y * zoom });
        event.preventDefault(); event.stopPropagation();
        return;
      }
      if (!gesture || event.pointerId !== gesture.pointer) return;
      const point = local(event), { start, viewport, mode, nodes } = gesture;
      const dx = point.x - start.x, dy = point.y - start.y;
      if (!gesture.moved && Math.hypot(dx, dy) < 3) return;
      gesture.moved = true;
      capture(event.pointerId);
      suppressClick = true;
      event.preventDefault();
      if (mode === "pan") void flow.setViewport({ ...viewport, x: viewport.x + dx, y: viewport.y + dy });
      if (mode === "nodes") {
        const starts = new Map(nodes.map(n => [n.id, n.position]));
        flow.setNodes(current => current.map(n => {
          const p = starts.get(n.id);
          return { ...n, selected: !!p, ...(p ? { dragging: true, position: { x: p.x + dx / viewport.zoom, y: p.y + dy / viewport.zoom } } : {}) };
        }));
      }
      if (mode === "select") {
        const box = { x: Math.min(start.x, point.x), y: Math.min(start.y, point.y), width: Math.abs(dx), height: Math.abs(dy) };
        setSelection(box);
        flow.setNodes(current => current.map(n => {
          const x = n.position.x * viewport.zoom + viewport.x, y = n.position.y * viewport.zoom + viewport.y;
          return { ...n, selected: x >= box.x && y >= box.y && x + (n.measured?.width ?? 110) * viewport.zoom <= box.x + box.width && y + (n.measured?.height ?? 110) * viewport.zoom <= box.y + box.height };
        }));
      }
    };
    const cancel = (mounted = true) => {
      const captured = new Set([...pointers.keys(), ...(gesture ? [gesture.pointer] : [])]);
      finish(true, mounted);
      pinch = undefined;
      pointers.clear(); captured.forEach(release);
    };
    const up = (event: PointerEvent) => {
      if (!pointers.has(event.pointerId) && gesture?.pointer !== event.pointerId) return;
      if (event.type === 'pointercancel') { cancel(); return; }
      pointers.delete(event.pointerId);
      release(event.pointerId);
      if (pinch) {
        event.preventDefault(); event.stopPropagation();
        pinch = undefined;
        if (pointers.size >= 2) startPinch();
        else if (pointers.size === 1) {
          // Rebase a remaining finger so zoom-to-pan never jumps or moves a node.
          const [pointer, point] = [...pointers][0];
          gesture = { pointer, start: point, viewport: flow.getViewport(), nodes: [], selected: new Set(), mode: 'pan', moved: false };
        }
        return;
      }
      finish(false);
    };
    const click = (event: MouseEvent) => {
      if (suppressClick && event.detail !== 0) { event.stopPropagation(); event.preventDefault(); suppressClick = false; }
    };
    const mouseDown = (event: MouseEvent) => { if (gesture) event.stopPropagation(); };
    const wheel = (event: WheelEvent) => {
      if ((event.target as Element).closest('input,textarea,select,[contenteditable]')) return;
      cancel();
      event.preventDefault(); event.stopPropagation();
      const point = local(event), viewport = flow.getViewport();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      const zoom = Math.max(0.1, Math.min(2, viewport.zoom * Math.exp(-delta * (event.ctrlKey ? 0.01 : 0.002))));
      void flow.setViewport({ zoom, x: point.x - (point.x - viewport.x) * zoom / viewport.zoom, y: point.y - (point.y - viewport.y) * zoom / viewport.zoom });
    };
    element.addEventListener('pointerdown', down, true);
    element.addEventListener('click', click, true);
    element.addEventListener('mousedown', mouseDown, true);
    element.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('pointermove', move, { passive: false });
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    const blur = () => cancel();
    const visibility = () => { if (document.hidden) cancel(); };
    window.addEventListener('blur', blur);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      element.removeEventListener('pointerdown', down, true);
      element.removeEventListener('click', click, true);
      element.removeEventListener('mousedown', mouseDown, true);
      element.removeEventListener('wheel', wheel);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      window.removeEventListener('blur', blur);
      document.removeEventListener('visibilitychange', visibility);
      cancel(false);
    };
  }, [root, flow]);
  return selection;
}
