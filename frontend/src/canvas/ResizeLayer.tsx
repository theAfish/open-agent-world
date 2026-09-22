import { useReactFlow } from '@xyflow/react';
import { useEffect, useRef, type Dispatch, type SetStateAction, type PointerEvent } from 'react';
import { t } from '../i18n';
import { apiErrorMessage } from '../api/client';
import type { CanvasNode } from '../cards/types';
import { containerDefinition, containerShowsWorkspace } from '../state/containers';
import { useEquipmentPanel } from '../state/equipment';
import { beginGlueEdit, glueGroup, persistGlue, refreshGlue, useGlueStore, type GlueBox } from '../state/glue';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { minimumSurfaceSize } from '../state/surfaceGeometry';
import { isShadow } from '../state/shadowCollection';
import { useWorldStore } from '../state/worldStore';
import { ViewportPortal } from './FlowPortal';
import { nodePositionFromSurfacePosition } from './nodeDisplacement';
import { freeCorners, resizeFromCorner, type ResizeBox, type ResizeConstraints, type ResizeCorner } from './resizeGeometry';
import './resize.css';

/** Resolve nested React Flow positions once, in the viewport portal's coordinates. */
function surfaceBoxes(nodes: CanvasNode[]): Record<string, GlueBox> {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const boxes: Record<string, GlueBox> = {};
  const visit = (node: CanvasNode): GlueBox => {
    if (boxes[node.id]) return boxes[node.id];
    const parent = byId.get(node.parentId ?? '');
    const origin = parent ? visit(parent) : { x: 0, y: 0 };
    return boxes[node.id] = { x: origin.x + node.position.x, y: origin.y + node.position.y,
      width: Number(node.style?.width ?? node.width), height: Number(node.style?.height ?? node.height), level: node.data.surfaceLevel };
  };
  nodes.forEach(visit);
  return boxes;
}

interface ResizeGesture {
  node: CanvasNode;
  nodes: CanvasNode[];
  box: GlueBox;
  next: ResizeBox;
  corner: ResizeCorner;
  x: number;
  y: number;
  zoom: number;
  constraints: ResizeConstraints;
  glued?: GlueBox;
  endEdit: () => void;
}

/** The only canvas resize controller. Geometry is shared; persistence follows
 * the existing owner (container, presentation, equipment placement or glue). */
export function ResizeLayer({ nodes, setNodes }: { nodes: CanvasNode[]; setNodes: Dispatch<SetStateAction<CanvasNode[]>> }) {
  const catalog = useWorldStore(state => state.catalog);
  const bonds = useGlueStore(state => state.bonds);
  const gluedBoxes = useGlueStore(state => state.boxes);
  const { getViewport } = useReactFlow();
  const drag = useRef<ResizeGesture>();
  const saving = useRef(false);
  const finishRef = useRef<(cancel?: boolean) => void>(() => undefined);
  const live = surfaceBoxes(nodes);

  const restore = (start: ResizeGesture) => {
    const originals = new Map(start.nodes.map(node => [node.id, node]));
    setNodes(current => current.map(node => {
      const old = originals.get(node.id);
      return old && (node.id === start.node.id || node.parentId === start.node.id)
        ? { ...node, position: old.position, width: old.width, height: old.height, measured: old.measured, style: old.style, resizing: false } : node;
    }));
    if (start.glued) useGlueStore.getState().setLayout({ [start.node.id]: start.glued });
  };

  const finish = async (cancel = false) => {
    const start = drag.current;
    if (!start) return;
    drag.current = undefined;
    saving.current = true;
    const { node, box, next } = start;
    const changed = next.x !== box.x || next.y !== box.y || next.width !== box.width || next.height !== box.height;
    let failed = false;
    try {
      if (cancel || !changed) { restore(start); return; }
      const world = useWorldStore.getState();
      if (node.type === 'container') {
        await world.resizeContainer(node.id, next, { x: next.x, y: next.y });
      } else {
        if (node.data.equipmentDetail) {
          useEquipmentPanel.getState().move(node.id, {
            x: node.position.x + next.x - box.x, y: node.position.y + next.y - box.y,
          });
        } else {
          const position = nodePositionFromSurfacePosition(next, box.level);
          await world.updateCardPositions([{ id: node.id, position }], start.glued ? [...glueGroup(node.id, useGlueStore.getState().bonds)].filter(id => id !== node.id) : undefined);
          // The store reconciles failed position writes and reports their error.
          const saved = useWorldStore.getState().cards.find(card => card.id === node.id);
          if (!saved || Math.abs(saved.position.x - position.x) > .01 || Math.abs(saved.position.y - position.y) > .01) {
            restore(start); return;
          }
        }
        if (start.glued) await persistGlue();
        useNodeSurfaceStore.getState().resizeSurface(node.id, box.level, next);
      }
    } catch (error) {
      failed = true;
      restore(start);
      useWorldStore.getState().pushToast({ tone: 'error', title: t('Resize was not saved'), detail: apiErrorMessage(error) });
    } finally {
      setNodes(current => current.map(node => node.id === start.node.id ? { ...node, resizing: false } : node));
      start.endEdit();
      useNodeSurfaceStore.getState().setDragging(false);
      saving.current = false;
      if (failed && start.glued) void refreshGlue().catch(() => undefined);
    }
  };
  finishRef.current = cancel => { void finish(cancel); };
  useEffect(() => {
    const cancel = () => finishRef.current(true);
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && drag.current) { event.preventDefault(); event.stopImmediatePropagation(); cancel(); }
    };
    window.addEventListener('keydown', key, true);
    window.addEventListener('blur', cancel);
    return () => { window.removeEventListener('keydown', key, true); window.removeEventListener('blur', cancel); cancel(); };
  }, []);

  const start = (event: PointerEvent<HTMLButtonElement>, node: CanvasNode, corner: ResizeCorner) => {
    if (event.button !== 0 || drag.current || saving.current || useNodeSurfaceStore.getState().dragging) return;
    event.preventDefault(); event.stopPropagation();
    const box = live[node.id], spec = containerDefinition(node.data.card, catalog);
    const constraints: ResizeConstraints = { min: spec ? { width: spec.min_size[0], height: spec.min_size[1] } : minimumSurfaceSize(box.level) };
    if (spec && !containerShowsWorkspace(node.data.card, catalog, box.level)) {
      const members = nodes.filter(child => !child.hidden && child.parentId === node.id && child.data.card.parent_id === node.id && !child.data.equipmentOrigin).map(child => live[child.id]);
      if (members.length) {
        const [left, top, right, bottom] = spec.content_inset;
        const x = Math.min(...members.map(member => member.x)) - left;
        const y = Math.min(...members.map(member => member.y)) - top;
        constraints.contains = { x, y, width: Math.max(...members.map(member => member.x + member.width)) + right - x,
          height: Math.max(...members.map(member => member.y + member.height)) + bottom - y };
      }
    }
    const parent = nodes.find(parent => parent.id === node.parentId);
    const parentSpec = parent && containerDefinition(parent.data.card, catalog);
    if (parent && parentSpec && !isShadow(parent.data.card) && !node.data.equipmentDetail) {
      constraints.originMin = { x: live[parent.id].x + parentSpec.content_inset[0], y: live[parent.id].y + parentSpec.content_inset[1] };
    }
    if (gluedBoxes[node.id]) {
      constraints.bonds = bonds.flatMap(bond => {
        if (bond.a !== node.id && bond.b !== node.id) return [];
        const side = bond.a === node.id ? bond.side : ({ left: 'right', right: 'left', top: 'bottom', bottom: 'top' } as const)[bond.side];
        const peer = live[bond.a === node.id ? bond.b : bond.a] ?? gluedBoxes[bond.a === node.id ? bond.b : bond.a];
        return peer ? [{ side, peer }] : [];
      });
      constraints.snap = { peers: [...glueGroup(node.id, bonds)].filter(id => id !== node.id).flatMap(id => live[id] ? [live[id]] : []), threshold: 10 / getViewport().zoom };
    }
    drag.current = { node, nodes, box, next: box, corner, x: event.clientX, y: event.clientY, zoom: getViewport().zoom,
      constraints, glued: gluedBoxes[node.id], endEdit: beginGlueEdit() };
    useNodeSurfaceStore.getState().setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const move = (event: PointerEvent) => {
    const start = drag.current;
    if (!start) return;
    const next = resizeFromCorner(start.box, start.corner, (event.clientX - start.x) / start.zoom, (event.clientY - start.y) / start.zoom, start.constraints);
    start.next = next;
    const dx = next.x - start.box.x, dy = next.y - start.box.y;
    const originals = new Map(start.nodes.map(node => [node.id, node]));
    setNodes(current => current.map(node => {
      if (node.id === start.node.id) return { ...node, position: { x: start.node.position.x + dx, y: start.node.position.y + dy },
        width: next.width, height: next.height, measured: { width: next.width, height: next.height },
        style: { ...node.style, width: next.width, height: next.height }, resizing: true };
      // React Flow stores relative coordinates; container members stay in world space.
      const old = originals.get(node.id);
      if (start.node.type === 'container' && old?.parentId === start.node.id) return { ...node, position: { x: old.position.x - dx, y: old.position.y - dy } };
      return node;
    }));
    if (start.glued) useGlueStore.getState().setLayout({ [start.node.id]: { ...start.glued, ...next } });
  };

  return <ViewportPortal>{nodes.filter(node => node.selected && !node.hidden && node.selectable !== false && !node.data.card.ephemeral && !isShadow(node.data.card)
    && (node.type === 'container' || node.type === 'worldCard' && (node.data.surfaceLevel === 'inspector' || node.data.surfaceLevel === 'workspace' || gluedBoxes[node.id])))
    .flatMap(node => {
      const corners = freeCorners(node.id, { ...gluedBoxes, ...live }, bonds);
      const active = drag.current;
      // Reaching the end of a seam must not unmount the captured handle.
      if (active?.node.id === node.id && !corners.includes(active.corner)) corners.push(active.corner);
      return corners.map(corner => {
        const box = live[node.id];
        return <button key={`${node.id}-${corner}`} type="button" className={`surface-resize-arc nodrag nopan ${corner}`} data-resize-node={node.id} data-resize-corner={corner}
          aria-label={t('缩放 {v0} {v1}', { v0: node.data.card.name, v1: corner })}
          style={{ left: box.x + (corner.endsWith('right') ? box.width : 0), top: box.y + (corner.startsWith('bottom') ? box.height : 0) }}
          onPointerDown={event => start(event, node, corner)} onPointerMove={move} onPointerUp={() => { void finish(); }}
          onPointerCancel={() => { void finish(true); }} onLostPointerCapture={() => { void finish(true); }}
          onClick={event => event.stopPropagation()} />;
      });
    })}</ViewportPortal>;
}
