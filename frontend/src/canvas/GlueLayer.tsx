import { t, useLocale } from "../i18n";
import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ViewportPortal } from './FlowPortal';
import { beginGlueEdit, glueGroup, persistGlue, useGlueStore, type GlueBond, type GlueCandidate } from '../state/glue';
import { apiErrorMessage } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { CanvasNode } from '../cards/types';
import { seam } from './resizeGeometry';
import { nodeBoundaryShape, nodeCornerRadius } from '../edges/nodeGeometry';
import './glue.css';

/** Composite the group's silhouette once, then remove its faces from the shadow. */
function GlueShadows({ nodes, bonds }: { nodes: CanvasNode[]; bonds: GlueBond[] }) {
  const filterId = useId();
  const catalog = useWorldStore(s => s.catalog);
  const remaining = new Set(bonds.flatMap(bond => [bond.a, bond.b]));
  const groups: CanvasNode[][] = [];
  for (const id of remaining) {
    const group = glueGroup(id, bonds);
    group.forEach(key => remaining.delete(key));
    const visible = nodes.filter(node => group.has(node.id) && !node.hidden && node.type === 'worldCard' && !node.parentId);
    if (visible.length) groups.push(visible);
  }
  return groups.map((group, index) => {
    const left = Math.min(...group.map(node => node.position.x)) - 96;
    const top = Math.min(...group.map(node => node.position.y)) - 96;
    const width = Math.max(...group.map(node => node.position.x + Number(node.style?.width))) + 96 - left;
    const height = Math.max(...group.map(node => node.position.y + Number(node.style?.height))) + 96 - top;
    const id = `${filterId}-${index}`;
    return <svg key={group[0].id} className="glue-shadow" aria-hidden="true"
      style={{ left, top, width, height }} viewBox={`${left} ${top} ${width} ${height}`}>
      <defs><filter id={id} x={left} y={top} width={width} height={height} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
        <feDropShadow in="SourceAlpha" dx="0" dy="18" stdDeviation="16" floodColor="var(--shadow-deep)" result="ambient" />
        <feDropShadow in="SourceAlpha" dx="0" dy="4" stdDeviation="5" floodColor="var(--shadow)" result="contact" />
        <feMerge><feMergeNode in="ambient" /><feMergeNode in="contact" /></feMerge>
        <feComposite in2="SourceAlpha" operator="out" />
      </filter></defs>
      <g filter={`url(#${id})`} fill="black">{group.map(node => {
        const { x, y } = node.position, w = Number(node.style?.width), h = Number(node.style?.height);
        return nodeBoundaryShape(node, catalog) === 'ellipse'
          ? <ellipse key={node.id} cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} />
          : <rect key={node.id} x={x} y={y} width={w} height={h} rx={nodeCornerRadius(node)} />;
      })}</g>
    </svg>;
  });
}

/** Preserve the seam across preview and commit so only an actual snap settles. */
function GlueSeam({ pending, vertical, style, children }: {
  pending: boolean; vertical: boolean; style: CSSProperties; children: ReactNode;
}) {
  const wasPending = useRef(pending);
  const [settling, setSettling] = useState(false);
  useEffect(() => {
    const committed = wasPending.current && !pending;
    wasPending.current = pending;
    setSettling(committed);
    if (!committed) return;
    const timer = setTimeout(() => setSettling(false), 320);
    return () => clearTimeout(timer);
  }, [pending]);
  return <div className={`glue-seam ${vertical ? 'vertical' : 'horizontal'} ${pending ? 'is-preview' : ''} ${settling ? 'is-settling' : ''}`} style={style}>
    {children}
  </div>;
}

export function GlueLayer({ nodes, preview }: { nodes: CanvasNode[]; preview?: GlueCandidate }) {
  useLocale();
  const boxes = useGlueStore(s => s.boxes);
  const bonds = useGlueStore(s => s.bonds);
  const edges = useWorldStore(s => s.edges);
  const selectedEdge = useWorldStore(s => s.selectedEdgeId);
  const live = Object.fromEntries(nodes.filter(n => !n.hidden).map(n => [n.id, { x: n.position.x, y: n.position.y, width: Number(n.style?.width), height: Number(n.style?.height), level: n.data.surfaceLevel }]));
  const pendingBonds = (preview?.bonds ?? []).filter(candidate => !bonds.some(bond => bond.a === candidate.a && bond.b === candidate.b || bond.a === candidate.b && bond.b === candidate.a));
  return <ViewportPortal>
    <GlueShadows nodes={nodes} bonds={bonds} />
    {bonds.concat(pendingBonds).map((bond, index) => {
      const a = live[bond.a], b = live[bond.b];
      if (!a || !b) return null;
      const pending = index >= bonds.length;
      const s = seam(a, b, bond.side);
      if (s.end <= s.start) return null;
      const connected = edges.filter(e => e.source === bond.a && e.target === bond.b || e.source === bond.b && e.target === bond.a);
      return <GlueSeam key={`${bond.a}-${bond.b}-${bond.side}`} pending={pending} vertical={s.vertical}
        style={s.vertical ? { left: s.axis - 6, top: s.start + 5, width: 12, height: Math.max(0, s.end - s.start - 10) } : { top: s.axis - 6, left: s.start + 5, height: 12, width: Math.max(0, s.end - s.start - 10) }}>
        {!pending && connected.map(edge => <button key={edge.id} className={`glue-stitch nodrag nopan ${selectedEdge === edge.id ? 'is-selected' : ''}`}
          aria-label={t("打开连接设置：{v0}", { v0: String(edge.relationship) })} title={t("缝合的连接 · 点击设置")} onClick={() => useWorldStore.getState().selectEdge(edge.id)}>
          <svg className="glue-stitch-marks" viewBox={s.vertical ? '0 0 12 56' : '0 0 56 12'} aria-hidden="true">
            <g transform={s.vertical ? undefined : 'translate(56 0) rotate(90)'}>
              <path d="M 2 9 L 10 15 M 2 25 L 10 31 M 2 41 L 10 47" />
              {[9, 25, 41].map(y => <g key={y}><circle cx="2" cy={y} r="1" /><circle cx="10" cy={y + 6} r="1" /></g>)}
            </g>
          </svg>
        </button>)}
      </GlueSeam>;
    })}
    {nodes.filter(n => n.selected && boxes[n.id]).map(node => <button key={`detach-${node.id}`} className="glue-detach nodrag nopan"
      style={{ left: node.position.x + 10, top: node.position.y - 29 }} onClick={() => { const endEdit = beginGlueEdit(); useGlueStore.getState().detach(node.id); void persistGlue([node.id]).catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: t("Could not detach glue"), detail: apiErrorMessage(reason) })).finally(endEdit); useNodeSurfaceStore.getState().setDragging(false); }}>{t("解除粘连")}</button>)}
  </ViewportPortal>;
}
