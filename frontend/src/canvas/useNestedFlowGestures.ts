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
    type Gesture = { pointer: number; start: XYPosition; viewport: ReturnType<typeof flow.getViewport>; nodes: Node[]; mode: "pan" | "nodes" | "select"; moved: boolean };
    let gesture: Gesture | undefined;
    let suppressClick = false;
    const down = (event: PointerEvent) => {
      // A drag released outside this canvas may never send its click here.
      // Only suppress the drag's click, never a later click on a control.
      suppressClick = false;
      if (event.button !== 0 && event.button !== 1) return;
      const target = event.target as Element;
      if (target.closest('button,input,select,textarea,a,.react-flow__controls')) return;
      const nodeElement = target.closest<HTMLElement>('.react-flow__node');
      const node = nodeElement ? flow.getNode(nodeElement.dataset.id!) : undefined;
      if (!node && !target.closest('.react-flow__pane') && event.button !== 1) return;
      const mode = event.button === 1 ? "pan" : event.shiftKey ? "select" : node ? "nodes" : "pan";
      const nodes = node ? node.selected ? flow.getNodes().filter(n => n.selected) : [node] : [];
      gesture = { pointer: event.pointerId, start: local(event), viewport: flow.getViewport(), nodes, mode, moved: false };
      event.stopPropagation();
      if (event.button === 1) event.preventDefault();
    };
    const move = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.pointer) return;
      const point = local(event), { start, viewport, mode, nodes } = gesture;
      const dx = point.x - start.x, dy = point.y - start.y;
      if (!gesture.moved && Math.hypot(dx, dy) < 3) return;
      gesture.moved = true;
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
    const up = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.pointer) return;
      if (gesture.moved && gesture.mode === "nodes") {
        flow.setNodes(current => current.map(n => n.dragging ? { ...n, dragging: false } : n));
        finishRef.current(flow.getNodes().filter(n => gesture!.nodes.some(start => start.id === n.id)));
      }
      gesture = undefined;
      setSelection(null);
    };
    const click = (event: MouseEvent) => {
      if (suppressClick) { event.stopPropagation(); event.preventDefault(); suppressClick = false; }
    };
    const mouseDown = (event: MouseEvent) => { if (gesture) event.stopPropagation(); };
    const wheel = (event: WheelEvent) => {
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
    return () => {
      element.removeEventListener('pointerdown', down, true);
      element.removeEventListener('click', click, true);
      element.removeEventListener('mousedown', mouseDown, true);
      element.removeEventListener('wheel', wheel);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
  }, [root, flow]);
  return selection;
}
