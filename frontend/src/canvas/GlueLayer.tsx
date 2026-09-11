import { useReactFlow } from '@xyflow/react';
import { useEffect, useRef, type PointerEvent } from 'react';
import { EdgeLabelRenderer } from './FlowPortal';
import { freeCorners, glueGroup, resizeGlued, seam, beginGlueEdit, persistGlue, useGlueStore, type GlueBox, type GlueCandidate } from '../state/glue';
import { apiErrorMessage } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { nodePositionFromSurfacePosition } from './nodeDisplacement';
import type { CanvasNode } from '../cards/types';
import './glue.css';

export function GlueLayer({ nodes, preview }: { nodes: CanvasNode[]; preview?: GlueCandidate }) {
  const boxes = useGlueStore(s => s.boxes);
  const bonds = useGlueStore(s => s.bonds);
  const edges = useWorldStore(s => s.edges);
  const selectedEdge = useWorldStore(s => s.selectedEdgeId);
  const { getViewport } = useReactFlow();
  const drag = useRef<{ id: string; corner: string; x: number; y: number; box: GlueBox; endEdit: () => void }>();
  useEffect(() => () => drag.current?.endEdit(), []);
  const live = Object.fromEntries(nodes.filter(n => !n.hidden).map(n => [n.id, { x: n.position.x, y: n.position.y, width: Number(n.style?.width), height: Number(n.style?.height), level: n.data.surfaceLevel }]));
  const move = (event: PointerEvent) => {
    const start = drag.current;
    if (!start) return;
    const group = glueGroup(start.id, bonds);
    const zoom = getViewport().zoom;
    const next = resizeGlued(start.box, start.corner, (event.clientX - start.x) / zoom, (event.clientY - start.y) / zoom,
      [...group].filter(id => id !== start.id && boxes[id]).map(id => boxes[id]), 10 / zoom);
    for (const bond of bonds) {
      if (bond.a !== start.id && bond.b !== start.id) continue;
      const side = bond.a === start.id ? bond.side : ({ left: 'right', right: 'left', top: 'bottom', bottom: 'top' } as const)[bond.side];
      if (side === 'left') { next.width += next.x - start.box.x; next.x = start.box.x; }
      if (side === 'right') next.width = start.box.x + start.box.width - next.x;
      if (side === 'top') { next.height += next.y - start.box.y; next.y = start.box.y; }
      if (side === 'bottom') next.height = start.box.y + start.box.height - next.y;
    }
    // A resize may shorten a seam, but must not tear an existing bond apart.
    if (bonds.some(b => (b.a === start.id || b.b === start.id) && boxes[b.a] && boxes[b.b] &&
      seam(b.a === start.id ? next : boxes[b.a], b.b === start.id ? next : boxes[b.b], b.side).end -
      seam(b.a === start.id ? next : boxes[b.a], b.b === start.id ? next : boxes[b.b], b.side).start < 24)) return;
    useGlueStore.getState().setLayout({ [start.id]: next });
  };
  const finish = (cancel = false) => {
    const start = drag.current;
    if (!start) return;
    drag.current = undefined;
    if (cancel) { useGlueStore.getState().setLayout({ [start.id]: start.box }); start.endEdit(); }
    else { const box = useGlueStore.getState().boxes[start.id]; void useWorldStore.getState().updateCardPositions([{ id: start.id, position: nodePositionFromSurfacePosition(box, box.level) }])
      .then(() => persistGlue()).catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: 'Glue resize needs a retry', detail: apiErrorMessage(reason) })).finally(start.endEdit); }
  };
  return <EdgeLabelRenderer>
    {bonds.concat(preview ? [preview] : []).map((bond, index) => {
      const a = live[bond.a], b = live[bond.b];
      if (!a || !b) return null;
      const s = seam(a, b, bond.side);
      if (s.end <= s.start) return null;
      const pending = index === bonds.length;
      const connected = edges.filter(e => e.source === bond.a && e.target === bond.b || e.source === bond.b && e.target === bond.a);
      return <div key={`${bond.a}-${bond.b}-${index}`} className={`glue-seam ${s.vertical ? 'vertical' : 'horizontal'} ${pending ? 'is-preview' : ''}`}
        style={s.vertical ? { left: s.axis - 6, top: s.start + 5, width: 12, height: Math.max(0, s.end - s.start - 10) } : { top: s.axis - 6, left: s.start + 5, height: 12, width: Math.max(0, s.end - s.start - 10) }}>
        {!pending && connected.map(edge => <button key={edge.id} className={`glue-stitch nodrag nopan ${selectedEdge === edge.id ? 'is-selected' : ''}`}
          aria-label={`打开连接设置：${edge.relationship}`} title="缝合的连接 · 点击设置" onClick={() => useWorldStore.getState().selectEdge(edge.id)}>
          <svg className="glue-stitch-marks" viewBox={s.vertical ? '0 0 12 56' : '0 0 56 12'} aria-hidden="true">
            <g transform={s.vertical ? undefined : 'translate(56 0) rotate(90)'}>
              <path d="M 2 9 L 10 15 M 2 25 L 10 31 M 2 41 L 10 47" />
              {[9, 25, 41].map(y => <g key={y}><circle cx="2" cy={y} r="1" /><circle cx="10" cy={y + 6} r="1" /></g>)}
            </g>
          </svg>
        </button>)}
      </div>;
    })}
    {nodes.filter(n => n.selected && !n.hidden && boxes[n.id]).flatMap(node => freeCorners(node.id, live, bonds).map(corner => {
      const box = { ...boxes[node.id], ...live[node.id] };
      return <button key={`${node.id}-${corner}`} className={`glue-resize nodrag nopan ${corner}`} aria-label={`缩放 ${node.data.card.name} ${corner}`}
        style={{ left: box.x + (corner.endsWith('right') ? box.width : 0) - 7, top: box.y + (corner.startsWith('bottom') ? box.height : 0) - 7 }}
        onPointerDown={event => { event.preventDefault(); event.stopPropagation(); const endEdit = beginGlueEdit(); event.currentTarget.setPointerCapture(event.pointerId); drag.current = { id: node.id, corner, x: event.clientX, y: event.clientY, box, endEdit }; }}
        onPointerMove={move} onPointerUp={() => finish()} onPointerCancel={() => finish(true)} onLostPointerCapture={() => finish()} />;
    }))}
    {nodes.filter(n => n.selected && boxes[n.id]).map(node => <button key={`detach-${node.id}`} className="glue-detach nodrag nopan"
      style={{ left: node.position.x + 10, top: node.position.y - 29 }} onClick={() => { const endEdit = beginGlueEdit(); useGlueStore.getState().detach(node.id); void persistGlue([node.id]).catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: 'Could not detach glue', detail: apiErrorMessage(reason) })).finally(endEdit); useNodeSurfaceStore.getState().setDragging(false); }}>解除粘连</button>)}
  </EdgeLabelRenderer>;
}
