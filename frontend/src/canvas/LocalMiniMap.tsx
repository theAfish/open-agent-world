import { Panel, useReactFlow, useStore } from '@xyflow/react';
import { useRef } from 'react';
import { shallow } from 'zustand/shallow';
import type { CanvasNodeData } from '../cards/types';
import { getNodeType } from '../state/catalog';
import { useWorldStore } from '../state/worldStore';

function LocalNode({ id }: { id: string }) {
  const catalog = useWorldStore(state => state.catalog);
  const rect = useStore(state => {
    const node = state.nodeLookup.get(id);
    if (!node || node.hidden) return null;
    return {
      ...node.internals.positionAbsolute,
      width: node.measured.width ?? node.width ?? 0,
      height: node.measured.height ?? node.height ?? 0,
      type: (node.data as CanvasNodeData).card.type,
    };
  }, shallow);
  return rect && <rect x={rect.x} y={rect.y} width={rect.width} height={rect.height}
    rx={5} fill={getNodeType(catalog, rect.type)?.color ?? 'var(--ink-soft)'} />;
}

/** A local lens: bounds depend only on the live viewport, never on node extents. */
export function LocalMiniMap() {
  const { setViewport, getViewport } = useReactFlow();
  const { x, y, zoom, width, height } = useStore(state => ({
    x: state.transform[0], y: state.transform[1], zoom: state.transform[2],
    width: state.width, height: state.height,
  }), shallow);
  const ids = useStore(state => Array.from(state.nodeLookup.keys()), shallow);
  const drag = useRef<{ clientX: number; clientY: number; x: number; y: number; zoom: number; scale: number }>();
  if (!width || !height) return null;
  const scale = Math.min(132 / width, 96 / height);
  const mapWidth = width * scale;
  const mapHeight = height * scale;
  const view = { x: -x / zoom, y: -y / zoom, width: width / zoom, height: height / zoom };
  // Show 15% extra on each side, with the current viewport always centered.
  const bounds = { x: view.x - view.width * 0.15, y: view.y - view.height * 0.15,
    width: view.width * 1.3, height: view.height * 1.3 };
  return <Panel position="bottom-right" className="world-minimap react-flow__minimap"
    style={{ width: mapWidth, height: mapHeight, background: 'var(--canvas)' }}>
    <svg className="react-flow__minimap-svg" width={mapWidth} height={mapHeight}
      viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`} role="img"
      aria-label="Nearby canvas — current view with 15% surroundings; drag to pan"
      onWheel={event => event.stopPropagation()}
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { clientX: event.clientX, clientY: event.clientY, ...getViewport(), scale: bounds.width / mapWidth };
      }}
      onPointerMove={event => {
        const start = drag.current;
        if (!start) return;
        void setViewport({ x: start.x - (event.clientX - start.clientX) * start.scale * start.zoom,
          y: start.y - (event.clientY - start.clientY) * start.scale * start.zoom, zoom: start.zoom });
      }}
      onPointerUp={event => {
        drag.current = undefined;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => { drag.current = undefined; }}
      onLostPointerCapture={() => { drag.current = undefined; }}>
      <title>Nearby canvas · drag to pan</title>
      {ids.map(id => <LocalNode key={id} id={id} />)}
      <path className="local-minimap-mask" fill="var(--minimap-mask)" fillRule="evenodd" pointerEvents="none"
        d={`M${bounds.x},${bounds.y}h${bounds.width}v${bounds.height}h${-bounds.width}z M${view.x},${view.y}h${view.width}v${view.height}h${-view.width}z`} />
      <rect className="local-minimap-viewport" {...view} fill="none" stroke="var(--accent)"
        strokeWidth={1.5} vectorEffect="non-scaling-stroke" pointerEvents="none" />
    </svg>
  </Panel>;
}
