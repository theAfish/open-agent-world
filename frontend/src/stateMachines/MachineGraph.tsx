import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { applyNodeChanges, Background, BaseEdge, Controls, MarkerType, ReactFlow, ReactFlowProvider, useInternalNode, useNodesInitialized, useReactFlow, type Connection, type ConnectionLineComponentProps, type Edge, type EdgeProps, type InternalNode, type Node, type NodeChange, type NodeProps } from '@xyflow/react';
import { t } from '../i18n';
import type { StateMachine } from './model';
import { connectionOffsets, stateConnections } from './graphRepresentation';
import { relationshipPath, relationshipPathToPoint, type NodeRect } from '../edges/geometry';
import { machineSpaces, STATE_SIZE } from './graphLayout';
import { BoundaryConnectionHandles } from '../cards/BoundaryConnectionHandles';
import { clearConnectionHoverHint, updateConnectionHoverHint } from '../cards/ConnectionHoverHint';

export type GraphAnchor = { x: number; y: number };
export type Selection = { entityId: string; stateId?: string } | { ruleId: string };
type GraphNode = Node<{ label: string; system?: boolean; initial?: boolean; active?: boolean; kind?: string; entityId: string; stateId?: string; ruleId?: string; selected?: boolean; connectingFrom?: boolean; solo?: boolean; reference?: boolean }>;
const entityKey = (id: string) => `entity:${encodeURIComponent(id)}`;
// React Flow interpolates node IDs into DOM selectors when connecting handles.
const stateKey = (entityId: string, stateId: string) => `state:${encodeURIComponent(entityId)}:${encodeURIComponent(stateId)}`;

function StateNode({ data }: NodeProps<GraphNode>) {
  return <div onPointerMove={event => updateConnectionHoverHint(event, event.currentTarget)} onPointerLeave={event => clearConnectionHoverHint(event.currentTarget)} className={`sm-state ${data.system ? 'is-system' : ''} ${data.initial ? 'is-initial' : ''} ${data.active ? 'is-active' : ''} ${data.selected ? 'is-selected' : ''} ${data.connectingFrom ? 'is-connection-source' : ''}`}>
    <BoundaryConnectionHandles label={data.label} />
    {data.initial && <span className="sm-initial-label">{t('Initial')}</span>}
    <span title={data.label}>{data.system && <small className="sm-system-mark">🔒 SYSTEM</small>}{data.label}</span>
  </div>;
}
function EntityNode({ data }: NodeProps<GraphNode>) {
  return <div className={`sm-entity-frame ${data.selected ? 'is-selected' : ''} ${data.solo ? 'is-solo' : ''} ${data.reference ? 'is-reference' : ''}`}>
    <div className="sm-entity-heading"><strong>{data.label}</strong></div>
  </div>;
}
const nodeTypes = { machineState: StateNode, machineEntity: EntityNode };
function nodeRect(node: InternalNode<GraphNode>): NodeRect {
  return { ...node.internals.positionAbsolute, width: node.measured.width ?? STATE_SIZE, height: node.measured.height ?? STATE_SIZE };
}
function TransitionEdge(props: EdgeProps) {
  const source = useInternalNode<GraphNode>(props.source), target = useInternalNode<GraphNode>(props.target);
  if (!source || !target) return null;
  const geometry = relationshipPath(nodeRect(source), nodeRect(target), STATE_SIZE / 2, STATE_SIZE / 2,
    { selfLoop: props.source === props.target, offset: Number(props.data?.offset ?? 0), markerOffset: 7 });
  return <>
    <BaseEdge id={props.id} path={geometry.markerPath} labelX={geometry.labelX} labelY={geometry.labelY} label={props.label} markerEnd={props.markerEnd} style={props.style} labelStyle={props.labelStyle} labelBgStyle={props.labelBgStyle} labelBgPadding={[5, 3]} labelBgBorderRadius={4} />
    {[geometry.source, geometry.target].map((point, index) => <circle key={index} cx={point.x} cy={point.y} r={3} fill={props.style?.stroke} opacity={props.style?.opacity} className="sm-edge-endpoint" data-endpoint={index ? 'target' : 'source'} />)}
  </>;
}
const edgeTypes = { transition: TransitionEdge };

function ConnectionLine({ fromNode, toNode, toX, toY }: ConnectionLineComponentProps<GraphNode>) {
  const path = toNode
    ? relationshipPath(nodeRect(fromNode), nodeRect(toNode), STATE_SIZE / 2, STATE_SIZE / 2,
      { selfLoop: fromNode.id === toNode.id }).path
    : relationshipPathToPoint(nodeRect(fromNode), { x: toX, y: toY }, STATE_SIZE / 2);
  return <path className="sm-connection-preview" d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />;
}

export function MachineGraph(props: { machine: StateMachine; ownerLabels?: Record<string, string>; selection: Selection; snapshot: Record<string, string>; lastRule?: string; ruleStatus?: Record<string, string>; connecting?: boolean; connectionSource?: { entityId: string; stateId: string }; viewport?: { x: number; y: number; zoom: number }; onViewportChange?: (viewport: { x: number; y: number; zoom: number }) => void; onPaneClick?: () => void; onSelect: (selection: Selection, anchor?: GraphAnchor) => void; onMove: (entityId: string, stateId: string, position: { x: number; y: number }) => void; onConnect: (source: { entityId: string; stateId: string }, target: { entityId: string; stateId: string }, anchor?: GraphAnchor) => void }) {
  const { machine, selection, snapshot, lastRule, connecting, connectionSource, onSelect, onMove, onConnect, viewport, onViewportChange, onPaneClick } = props;
  const element = useRef<HTMLDivElement>(null);
  const connectionEndedAt = useRef(-Infinity);
  const nodes = useMemo(() => {
    const result: GraphNode[] = [], spaces = machineSpaces(machine);
    let x = 20;
    for (const space of spaces) {
      result.push({ id: entityKey(space.id), type: 'machineEntity', position: { x, y: 20 }, draggable: false, selectable: false, focusable: false,
        style: { width: space.width, height: space.height }, data: { entityId: space.entities[0].id, label: props.ownerLabels?.[space.id] ?? space.id, solo: spaces.length === 1 } });
      for (const entity of space.entities) for (const state of entity.states) result.push({ id: stateKey(entity.id, state.id), parentId: entityKey(space.id), type: 'machineState', position: state.position,
        ariaLabel: t('State: {name}', { name: state.label }), style: { width: STATE_SIZE, height: STATE_SIZE }, data: { entityId: entity.id, stateId: state.id, label: state.label, system: entity.ownership === 'system', connectingFrom: connectionSource?.entityId === entity.id && connectionSource?.stateId === state.id, initial: state.id === entity.initial_state, active: snapshot[entity.id] === state.id, selected: 'entityId' in selection && selection.entityId === entity.id && selection.stateId === state.id } });
      x += space.width + 60;
    }
    return result;
  }, [machine.entities, selection, snapshot, connectionSource, props.ownerLabels]);
  // Apply controlled-node drag changes every frame; persist only on drag stop
  // so moving a node does not continuously reset the machine's preview state.
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>(nodes);
  useEffect(() => { setGraphNodes(nodes); }, [nodes]);
  const onNodesChange = useCallback((changes: NodeChange<GraphNode>[]) => {
    setGraphNodes(current => applyNodeChanges(changes, current));
  }, []);
  const edges = useMemo(() => {
    const connections = stateConnections(machine), offsets = connectionOffsets(connections);
    return connections.map(({id, rule, source, target, command}): Edge => {
      const entity = machine.entities.find(group => group.id === target.entityId)!;
      const label = rule.canonical ? undefined : command ? entity.commands?.find(item => item.id === rule.command?.command_id)?.label
        : rule.trigger.event === 'state.entered' ? t('On enter') : rule.trigger.event === 'state.exited' ? t('On exit') : rule.trigger.event === 'state.current' ? t('While in state') : undefined;
      return { id, source: stateKey(source.entityId, source.stateId), target: stateKey(target.entityId, target.stateId), sourceHandle: 'boundary', targetHandle: 'surface-drop', type: 'transition', label,
        ariaLabel: rule.canonical ? `${t('System transition')}: ${source.stateId} → ${target.stateId}` : rule.name,
        className: rule.canonical ? 'is-canonical' : rule.trigger.event === 'unconfigured' ? 'is-unconfigured' : command ? 'is-command' : undefined, data: { ruleId: rule.id, offset: offsets.get(id) }, animated: !rule.canonical && rule.id === lastRule,
        selected: 'ruleId' in selection && selection.ruleId === rule.id,
        markerEnd: { type: MarkerType.ArrowClosed, color: rule.canonical ? 'var(--ink-faint)' : 'var(--accent)' },
        style: { stroke: rule.canonical ? 'var(--ink-faint)' : rule.enabled ? 'var(--accent)' : 'var(--ink-faint)', strokeWidth: rule.canonical ? 1.2 : rule.id === lastRule ? 3 : 2, strokeDasharray: command || !rule.enabled ? '5 5' : undefined, opacity: rule.canonical ? .4 : rule.enabled ? 1 : .5 },
        labelStyle: { fill: 'var(--ink)', fontSize: 12, fontWeight: 500 }, labelBgStyle: { fill: 'var(--surface-solid)' },
      };
    });
  }, [machine, selection, lastRule]);
  const connect = (connection: Connection) => {
    const source = nodes.find(n => n.id === connection.source)?.data, target = nodes.find(n => n.id === connection.target)?.data;
    const sourceElement = Array.from(element.current?.querySelectorAll('[data-id]') ?? []).find(item => item.getAttribute('data-id') === connection.source);
    const targetElement = Array.from(element.current?.querySelectorAll('[data-id]') ?? []).find(item => item.getAttribute('data-id') === connection.target);
    const a = sourceElement?.getBoundingClientRect(), b = targetElement?.getBoundingClientRect();
    const anchor = a && b ? { x: (a.x + a.width / 2 + b.x + b.width / 2) / 2, y: (a.y + b.y) / 2 } : undefined;
    if (source?.stateId && target?.stateId) onConnect({ entityId: source.entityId, stateId: source.stateId }, { entityId: target.entityId, stateId: target.stateId }, anchor);
  };
  return <div ref={element} className={`sm-graph ${connecting ? 'is-connecting' : ''}`} onKeyDownCapture={event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const id = (event.target as Element).closest('.react-flow__edge')?.getAttribute('data-id');
    const edge = edges.find(item => item.id === id);
    if (edge) { event.preventDefault(); event.stopPropagation(); onSelect({ruleId: String(edge.data?.ruleId)}); }
  }}><ReactFlowProvider><ReactFlow nodes={graphNodes} onNodesChange={onNodesChange} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} defaultViewport={viewport} fitView={!viewport} fitViewOptions={{ padding: .18, maxZoom: 1.05 }} minZoom={.15} maxZoom={1.7}
    onConnectEnd={() => { connectionEndedAt.current = performance.now(); }}
    onNodeClick={(event, node) => {
      // A self-link ends on its starting node, so the browser also emits a click.
      // Do not let that trailing click replace the newly opened rule editor.
      if (performance.now() - connectionEndedAt.current < 100 || (event.target as Element).closest('.react-flow__handle')) return;
      onSelect(node.data.ruleId ? {ruleId: node.data.ruleId} : { entityId: node.data.entityId, stateId: node.data.stateId }, { x: event.clientX, y: event.clientY });
    }}
    onEdgeClick={(event, edge) => onSelect({ ruleId: String(edge.data?.ruleId) }, { x: event.clientX, y: event.clientY })}
    onPaneClick={onPaneClick} onMoveEnd={(event, next) => { if (event) onViewportChange?.(next); }}
    onNodeDragStop={(_, node) => { if (node.data.stateId) onMove(node.data.entityId, node.data.stateId, { x: Math.max(24, node.position.x), y: Math.max(78, node.position.y) }); }}
    onConnect={connect} connectionLineComponent={ConnectionLine} connectOnClick connectionRadius={28} panOnScroll zoomOnDoubleClick={false} deleteKeyCode={null} proOptions={{ hideAttribution: true }}><Background gap={20} size={1} color="var(--grid-dot)" /><Controls showInteractive={false} /><FitBoard topology={nodes.map(node => node.id).join('|')} savedViewport={viewport} /></ReactFlow></ReactFlowProvider>
  </div>;
}

function FitBoard({ topology, savedViewport }: { topology: string; savedViewport?: { x: number; y: number; zoom: number } }) {
  const initial = useRef(savedViewport);
  const fitted = useRef(false);
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (!initialized || initial.current || fitted.current) return;
    fitted.current = true;
    const frame = requestAnimationFrame(() => void fitView({ padding: .18, maxZoom: 1.05, duration: 180 }));
    return () => cancelAnimationFrame(frame);
  }, [topology, initialized, fitView]);
  return null;
}
