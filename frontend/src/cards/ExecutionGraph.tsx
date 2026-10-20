import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowDown, ArrowDownRight, ArrowRight, ArrowUpRight, ChevronDown, Circle, FileCode2, GitBranch, GitMerge, Minus, Play, Search, Timer, Wrench, X } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { WorkspaceEmptyState } from '../workspace/WorkspacePrimitives';
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { useExecutionSources, type ExecutionEdge, type ExecutionNode, type ExecutionSourceHost, type ExecutionSnapshot } from '../plugins/executionSources';
import type { WorldCard } from '../types/world';
import { elapsedMilliseconds } from '../state/activityTiming';
import { causalFocus, executionIndex, layoutGraph, laneMemory, nodeEdges, trackPath, statusKind, isPlanned, isInProgress } from './executionHistory';
import './executionGraph.css';

export function ExecutionGraphCard({ card }: { card: WorldCard }) {
  const { deployed, permissions } = useWorkspaceAccess();
  return <ExecutionGraphView api={useExecutionSources(card.id, deployed && !permissions[card.id]?.includes('execution_graph'))} />;
}

/** Publish a coherent revision only after all its pages arrive. */
export async function readExecutionHistory(api: ExecutionSourceHost, source: string): Promise<ExecutionSnapshot> {
  const first = await api.read(source, { limit: 1000 });
  const nodes = [...first.nodes], edges = [...first.edges ?? []];
  let cursor = first.next_cursor;
  const seen = new Set<string>();
  while (cursor) {
    if (seen.has(cursor)) throw new Error('Execution history returned a repeated cursor');
    seen.add(cursor);
    const next = await api.read(source, { limit: 1000, cursor });
    if (next.history_id !== first.history_id || next.revision !== first.revision) throw new Error('Execution history changed while loading; refreshing');
    nodes.push(...next.nodes); edges.push(...next.edges ?? []); cursor = next.next_cursor;
  }
  return { ...first, nodes, edges: first.edges === undefined ? undefined : edges, next_cursor: null };
}

/** Connected Agents share one surface, but retain source-local identities and
 * authorization. No relationship is inferred between independent providers. */
export async function readProjectHistory(api: ExecutionSourceHost, sources: { id: string; name: string }[], source = ''): Promise<ExecutionSnapshot> {
  const selected = source ? sources.filter(item => item.id === source) : sources;
  if (selected.length === 1) return readExecutionHistory(api, selected[0].id);
  const histories = await Promise.all(selected.map(async owner => ({ owner, value: await readExecutionHistory(api, owner.id) })));
  const identity = (source: string, id: string) => JSON.stringify([source, id]);
  return {
    title: t('Connected agents'), phase: histories.some(({ value }) => ['running', 'waiting'].includes(value.phase)) ? 'running' : 'idle',
    history_id: JSON.stringify(histories.map(({ owner, value }) => [owner.id, value.history_id || value.previous_run_id || value.run_id])),
    revision: JSON.stringify(histories.map(({ value }) => value.revision)),
    truncated: histories.some(({ value }) => value.truncated),
    nodes: histories.flatMap(({ owner, value }) => value.nodes.map(node => ({ ...node,
      id: identity(owner.id, node.id), parent_id: node.parent_id ? identity(owner.id, node.parent_id) : undefined,
      execution_id: node.execution_id ? identity(owner.id, node.execution_id) : undefined,
      actor_id: node.actor_id ? identity(owner.id, node.actor_id) : undefined,
      workstream_id: node.workstream_id ? identity(owner.id, node.workstream_id) : undefined,
      realized_by: node.realized_by ? identity(owner.id, node.realized_by) : undefined,
      retry_of: node.retry_of ? identity(owner.id, node.retry_of) : undefined,
      replan_of: node.replan_of ? identity(owner.id, node.replan_of) : undefined,
      dependency_ids: node.dependency_ids?.map(id => identity(owner.id, id)),
    }))),
    edges: histories.flatMap(({ owner, value }) => executionIndex(value).rawEdges.map(edge => ({ ...edge,
      id: identity(owner.id, edge.id), source: identity(owner.id, edge.source), target: identity(owner.id, edge.target),
    }))),
  };
}
const edgeLabel = (kind: ExecutionEdge['kind'], incoming = true) => incoming ? ({
  control: t('Requires'), data: t('Uses result'), retry: t('Retry'), replan: t('Replan'),
  fork: t('Started from'), merge: t('Applied result'),
})[kind] : ({ control: t('Required by'), data: t('Used by'), retry: t('Retried as'),
  replan: t('Replanned as'), fork: t('Launched task'), merge: t('Applied at') })[kind];
function Status({ status, vertex = false, label = true }: { status: string; vertex?: boolean; label?: boolean }) {
  const kind = statusKind(status);
  const Icon = kind === 'failed' ? X : kind === 'stopped' ? Minus : Circle;
  return <span className="execution-status" data-status={status} data-state={kind} title={t(status)}>
    {kind === 'complete' ? <i className="execution-status-core" aria-hidden="true" />
      : kind === 'running' ? <i className="execution-status-ring" aria-hidden="true" />
      : kind === 'waiting' ? <svg className="execution-status-clock" width={vertex ? 18 : 12} height={vertex ? 18 : 12}
        viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.7" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10" fill={vertex ? 'var(--surface-solid)' : 'none'} />
        <path className="execution-clock-hand execution-clock-hour" d="M12 12H16" />
        <path className="execution-clock-hand execution-clock-minute" d="M12 12V6" />
        <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
      </svg>
      : kind === 'rework' ? <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M6 2.25V6.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <circle cx="6" cy="9.5" r="1" fill="currentColor" />
      </svg>
      : <Icon size={12} strokeWidth={kind === 'failed' ? 3.5 : 2.7} aria-hidden="true" />}
    {!vertex && label && kind === 'custom' && <small>{status}</small>}
  </span>;
}
function Operation({ node }: { node: ExecutionNode }) {
  const Icon = node.operation === 'edit' ? FileCode2 : node.operation === 'merge' ? GitMerge
    : node.operation === 'plan' || node.role === 'planning' ? GitBranch
    : node.kind === 'tool' ? Wrench : Play;
  return <Icon size={12} aria-hidden="true" />;
}
const timeText = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';

function EventTiming({ node }: { node: ExecutionNode }) {
  const locale = useLocale(state => state.locale);
  const validDate = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value) : undefined;
  const start = validDate(node.started_at), end = validDate(node.finished_at);
  if (!start && !end) return null;
  const elapsed = elapsedMilliseconds(node.started_at, node.finished_at);
  const seconds = elapsed === undefined ? undefined : elapsed / 1000;
  const duration = seconds === undefined ? undefined : seconds < 60 ? t('{seconds}s', { seconds: Number(seconds.toFixed(1)) })
    : seconds < 3600 ? t('{minutes}m {seconds}s', { minutes: Math.floor(seconds / 60), seconds: Math.floor(seconds % 60) })
    : seconds < 86400 ? t('{hours}h {minutes}m', { hours: Math.floor(seconds / 3600), minutes: Math.floor(seconds % 3600 / 60) })
    : t('{days}d {hours}h', { days: Math.floor(seconds / 86400), hours: Math.floor(seconds % 86400 / 3600) });
  const date = (value: Date) => value.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
  const sameDay = start && end && start.toDateString() === end.toDateString();
  const caption = start && end && !sameDay ? t('Execution timing') : date((start ?? end)!);
  return <div className="execution-timing" aria-label={t('Execution timing')}>
    <div className="execution-timing-caption"><span>{caption}</span>
      {duration !== undefined && <span className="execution-duration" title={t('Elapsed time')}><Timer size={11} aria-hidden="true" />{duration}</span>}
    </div>
    <div className="execution-time-range">
      {[{ label: t('Start time'), value: start, raw: node.started_at }, { label: t('End time'), value: end, raw: node.finished_at }].map((point, i) =>
        <div className="execution-time-point" key={i}>
          <span>{point.label}</span>
          {point.value ? <time dateTime={point.raw!} title={point.value.toLocaleString(locale)}>
            {point.value.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
          </time> : <span className="execution-time-unknown" title={t('Time not recorded')}>{i === 1 && !node.finished_at && isInProgress(node.status) ? t('In progress') : '—'}</span>}
          {start && end && !sameDay && <small>{date(point.value!)}</small>}
        </div>)}
      <span className="execution-time-connector" aria-hidden="true"><ArrowRight size={12} /></span>
    </div>
  </div>;
}

function ToolSummary({ nodes }: { nodes: ExecutionNode[] }) {
  const groups = new Map<string, { node: ExecutionNode; count: number; notes: Set<string> }>();
  for (const node of nodes) {
    const key = JSON.stringify([node.label, node.status]);
    const group = groups.get(key) ?? { node, count: 0, notes: new Set<string>() };
    group.count++;
    for (const note of [node.result, node.detail, ...node.artifact_refs ?? []]) if (note) group.notes.add(note);
    groups.set(key, group);
  }
  return <div className="execution-task-tools"><small>{t('Tool activity')} · {nodes.length}</small>
    <ul>{[...groups].map(([key, { node, count, notes }]) => <li key={key}>
      <Status status={node.status} /><span>{node.label}</span>{count > 1 && <small>×{count}</small>}
      {!!notes.size && <div className="execution-tool-notes">{[...notes].map(note => <p key={note}>{note}</p>)}</div>}
    </li>)}</ul>
  </div>;
}

export function ExecutionGraphView({ api }: { api?: ExecutionSourceHost }) {
  useLocale();
  const completedGapsId = useId();
  const [value, setValue] = useState<ExecutionSnapshot>();
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string>();
  const [hovered, setHovered] = useState<string>();
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);
  const [source, setSource] = useState('');
  const [query, setQuery] = useState('');
  const [match, setMatch] = useState(0);
  const [newCount, setNewCount] = useState(0);
  const [detailHeight, setDetailHeight] = useState(150);
  const [scroll, setScroll] = useState({ top: 0, height: 560, width: 500 });
  const memory = useRef(laneMemory());
  const viewport = useRef<HTMLDivElement>(null);
  const [information, setInformation] = useState<HTMLDivElement | null>(null);
  const gesture = useRef({ x: 0, y: 0, moved: false });
  const pendingNavigation = useRef<string>();
  const index = useMemo(() => executionIndex(value), [value]);
  const graph = useMemo(() => layoutGraph(index, memory.current, selected, detailHeight, query), [index, selected, detailHeight, query]);
  const priorRows = useRef(graph.rows);
  const known = useRef(new Set<string>());

  useEffect(() => {
    let stopped = false, generation = 0, reading = false, queued = false;
    let historyId: string | undefined, initialized = false;
    let timer: ReturnType<typeof setTimeout>;
    const reset = () => {
      setValue(undefined); setError(''); setSelected(undefined); setHovered(undefined); setSources([]);
      setQuery(''); setMatch(0); setNewCount(0); known.current = new Set();
      pendingNavigation.current = undefined; memory.current = laneMemory(); priorRows.current = [];
      if (viewport.current) viewport.current.scrollTop = 0;
    };
    reset();
    const refresh = async () => {
      if (reading) { queued = true; return; }
      reading = true; queued = false;
      const current = generation;
      clearTimeout(timer);
      try {
        const connected = await api!.list();
        if (stopped || current !== generation) return;
        setSources(connected.sources);
        if (!connected.sources.length) { reset(); return; }
        const target = connected.sources.some(item => item.id === source) ? source : '';
        const result = await readProjectHistory(api!, connected.sources, target);
        if (stopped || current !== generation) return;
        const identity = result.history_id || result.previous_run_id || result.run_id;
        if (!initialized || historyId !== identity) {
          setSelected(undefined); memory.current = laneMemory(); priorRows.current = [];
          setNewCount(0); setQuery(''); setMatch(0); known.current = new Set();
          if (viewport.current) viewport.current.scrollTop = 0;
        } else {
          const added = executionIndex(result).activities.filter(node => !known.current.has(node.id)).length;
          if (added) setNewCount(count => count + added);
        }
        initialized = true; historyId = identity;
        known.current = new Set(executionIndex(result).activities.map(node => node.id));
        setValue(result); setError('');
      } catch (reason) {
        if (!stopped && current === generation) {
          setError(String(reason));
          if (/\b(403|404)\b/.test(String(reason))) reset();
        }
      } finally {
        if (!stopped && current === generation) {
          reading = false;
          if (queued) void refresh(); else timer = setTimeout(() => void refresh(), 2000);
        }
      }
    };
    if (!api) return;
    void refresh();
    const unsubscribe = api.subscribe(reason => {
      if (reason !== 'execution') { generation++; reading = false; queued = false; historyId = undefined; initialized = false; reset(); }
      void refresh();
    });
    return () => { stopped = true; clearTimeout(timer); unsubscribe(); };
  }, [api, source]);

  useLayoutEffect(() => {
    const element = information;
    if (!element) return;
    const measure = () => setDetailHeight(element.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, [information, selected, value]);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => setScroll(previous => ({ ...previous, height: element.clientHeight || 560, width: element.clientWidth || 500 }));
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  // Preserve the event at the top of the reader's viewport, never chase live work.
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const navigation = pendingNavigation.current;
    if (navigation) {
      const row = graph.ports.get(navigation);
      if (row) {
        if (row.top < element.scrollTop || row.top + row.height > element.scrollTop + element.clientHeight)
          element.scrollTop = Math.max(0, row.top - element.clientHeight / 4);
        pendingNavigation.current = undefined;
      }
    } else {
      const previous = priorRows.current.find(row => row.top + row.height > element.scrollTop);
      const current = previous && graph.ports.get(previous.id);
      if (current) {
        const offset = element.scrollTop - previous!.top;
        // A reader may be far inside a long detail when it collapses. Keep the
        // same event in view rather than carrying that offset into later events.
        element.scrollTop = current.top + (offset < current.height ? offset : 0);
      }
    }
    priorRows.current = graph.rows;
    setScroll(previous => ({ ...previous, top: element.scrollTop }));
  }, [graph]);
  useEffect(() => {
    if (!selected) return;
    if (!index.byId.has(selected)) setSelected(undefined);
    else if (index.eventId.get(selected) !== selected) {
      pendingNavigation.current = index.eventId.get(selected);
      setSelected(index.eventId.get(selected));
    }
  }, [selected, index]);

  const navigate = (id: string, toggle = false) => {
    if (!index.byId.has(id)) return;
    setHovered(undefined);
    pendingNavigation.current = id;
    setSelected(current => toggle && current === id ? undefined : id);
  };
  const find = (next: number) => {
    if (!graph.matchIds.length) return;
    const position = (next + graph.matchIds.length) % graph.matchIds.length;
    setMatch(position); navigate(graph.matchIds[position]);
  };
  // Selection owns the lens until dismissed; hover cannot replace it.
  const focused = selected ?? hovered;
  const focus = useMemo(() => causalFocus(index, focused), [index, focused]);
  const selectedEdges = nodeEdges(index, selected);
  const relatedNodes = focus.nodes;
  const narrow = scroll.width < 380, gap = narrow ? 18 : scroll.width > 700 ? 30 : 24;
  const laneX = (lane: number) => 24 + lane * gap;
  const labelX = laneX(graph.lanes - 1) + (narrow ? 22 : 28);
  const firstY = Math.max(0, scroll.top - 108), lastY = Math.min(graph.height, scroll.top + scroll.height + 108);
  const visibleRows = graph.rows.filter(row => row.top + row.height >= firstY && row.top <= lastY);
  // Focus changes emphasis, never topology. Folded relations remain available
  // through endpoint counts and detail navigation instead of appearing as new
  // cables elsewhere in the selected event's transitive dependency chain.
  const visibleTracks = graph.tracks.filter(track => !track.reference
    && Math.max(track.source.y, track.target.y) >= firstY && Math.min(track.source.y, track.target.y) <= lastY);
  const active = selected ? index.byId.get(selected) : undefined;
  const streamConnections = active ? graph.tracks.filter(track => track.kind === 'workstream'
    && (track.source.id === active.id || track.target.id === active.id)) : [];
  const taskTools = active ? index.details.get(active.id) ?? [] : [];
  const plans = active ? index.nodes.filter(node => isPlanned(node) && node.parent_id === active.id) : [];
  const unavailableCount = new Map<string, number>();
  for (const edge of graph.unavailable) for (const id of [edge.source, edge.target]) if (graph.ports.has(id))
    unavailableCount.set(id, (unavailableCount.get(id) ?? 0) + 1);

  return <section className="execution-graph nodrag nopan nowheel" aria-label={t('Execution graph')} data-narrow={narrow}
    style={{ '--graph-label-x': labelX + 'px' } as CSSProperties}>
    {sources.length > 1 && <select className="execution-graph-source" aria-label={t('Execution source')} value={sources.some(item => item.id === source) ? source : ''} onChange={event => setSource(event.target.value)}>
      <option value="">{t('All connected agents')}</option>
      {sources.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>}
    {error && <p className="execution-graph-notice" role="alert">{error}</p>}
    {value?.truncated && <p className="execution-graph-notice">{t('Some history is unavailable from this source.')}</p>}
    {value?.previous_run_id && <p className="execution-graph-notice" role="status">{['created', 'running', 'waiting'].includes(value.phase) ? t('Waiting for this run’s graph…') : t('Showing the previous execution')}</p>}
    {graph.rows.length > 0 && <div className="execution-history-toolbar">
      <span title={t('Start time, then recorded order')}>{graph.rows.length} {t('events')} <span aria-hidden="true">↓</span></span>
      <form onSubmit={event => { event.preventDefault(); find(match + 1); }}><Search size={12} />
        <input aria-label={t('Find event')} placeholder={t('Find event')} value={query}
          onChange={event => { setQuery(event.target.value); setMatch(-1); }} onKeyDown={event => { if (event.key === 'Escape') { setQuery(''); setSelected(undefined); } }} />
        {query && <button type="submit" aria-label={t('Next match')}>{graph.matchIds.length ? Math.max(0, match + 1) + '/' + graph.matchIds.length : '0'}<ChevronDown size={12} /></button>}
      </form>
    </div>}
    <div className="execution-graph-viewport" ref={viewport} tabIndex={0} aria-label={t('Execution history')}
      onPointerDownCapture={event => { gesture.current = { x: event.clientX, y: event.clientY, moved: false }; }}
      onPointerMoveCapture={event => { if (Math.hypot(event.clientX - gesture.current.x, event.clientY - gesture.current.y) > 4) gesture.current.moved = true; }}
      onClickCapture={event => { if (event.detail && gesture.current.moved) { event.preventDefault(); event.stopPropagation(); } }}
      onKeyDown={event => {
        if (event.key === 'Escape') { setSelected(undefined); setHovered(undefined); event.stopPropagation(); }
        // The viewport owns navigation across virtualized rows. Inline details
        // keep native page scrolling and link/button keyboard behavior.
        if ((event.target as HTMLElement).closest('.execution-inline') || event.altKey || event.ctrlKey || event.metaKey) return;
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && graph.rows.length) {
          event.preventDefault(); event.stopPropagation();
          const current = selected ? graph.ports.get(selected)?.ordinal ?? 0 : visibleRows[0]?.ordinal ?? 0;
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? graph.rows.length - 1
            : Math.max(0, Math.min(graph.rows.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
          viewport.current?.focus({ preventScroll: true }); navigate(graph.rows[next].id);
        }
      }}
      onClick={event => { if (event.target === event.currentTarget || (event.target as HTMLElement).classList?.contains('execution-graph-content')) { setSelected(undefined); setHovered(undefined); } }}
      onScroll={event => {
        const element = event.currentTarget; setScroll(previous => ({ ...previous, top: element.scrollTop }));
        if (element.scrollTop + element.clientHeight >= element.scrollHeight - 8) setNewCount(0);
      }}>
      {!graph.rows.length ? <WorkspaceEmptyState title={t('No execution yet')} description={sources.length ? t('No runtime activity for this session') : t('Connect an Agent or a callable library to observe its execution in this session.')} /> :
      <div className="execution-graph-content" style={{ height: graph.height }}>
        <svg className="execution-tracks" width={labelX} height={Math.max(0, lastY - firstY)} viewBox={'0 ' + firstY + ' ' + labelX + ' ' + Math.max(1, lastY - firstY)} style={{ top: firstY }} aria-label={t('Recorded dependency tracks')}>
          <defs><mask id={completedGapsId} maskUnits="userSpaceOnUse" x="0" y={firstY} width={labelX} height={Math.max(1, lastY - firstY)}>
            <rect x="0" y={firstY} width={labelX} height={Math.max(1, lastY - firstY)} fill="white" />
            {visibleRows.filter(row => statusKind(row.node.status) === 'complete').map(row =>
              <circle key={row.id} cx={laneX(row.lane)} cy={row.y} r="6" fill="black" />)}
          </mask></defs>
          <g mask={`url(#${completedGapsId})`}>
          {visibleTracks.map(track => {
            const highlighted = track.edges.some(edge => focus.edges.has(edge.id));
            return <path key={track.id} className={'execution-track execution-color-' + track.source.color}
              data-graph-edge={track.edge ? track.edge.source + ':' + track.edge.target : undefined}
              data-source={track.source.id} data-target={track.target.id}
              data-kinds={track.edges.map(edge => edge.kind).join(' ')}
              data-kind={track.kind} data-reference={track.reference} data-highlighted={highlighted}
              data-muted={!!focused && !highlighted}
              d={trackPath(track, laneX)}>
              <title>{track.edges.length ? track.edges.map(edge => edgeLabel(edge.kind)).join(' · ') : t('Same workstream, not a dependency')}: {track.source.node.label} → {track.target.node.label}</title>
            </path>;
          })}
          </g>
        </svg>
        {visibleRows.map(row => {
          const node = row.node, chosen = row.id === selected;
          const references = graph.references.get(row.id) ?? [];
          const referenceCount = references.length + (unavailableCount.get(row.id) ?? 0);
          return <div key={row.id} className={'execution-vertex execution-color-' + row.color} data-row-id={row.id} data-status={node.status} data-state={statusKind(node.status)} data-selected={chosen} data-related={relatedNodes.has(row.id)} data-muted={!!focused && !relatedNodes.has(row.id)} data-pending={isInProgress(node.status)}
            style={{ top: row.top, height: row.height }}>
            <button className="execution-node-select" aria-label={node.label + ': ' + node.status} aria-pressed={chosen} aria-expanded={chosen}
              onClick={() => navigate(row.id, true)} onMouseEnter={() => setHovered(row.id)} onMouseLeave={() => setHovered(undefined)}>
              <span className={'execution-vertex-dot execution-color-' + row.color} data-lane={row.lane} style={{ left: laneX(row.lane) }}><Status status={node.status} vertex /></span>
              <span className="execution-vertex-label">
                <span className="execution-operation-title"><strong title={node.label}>{node.label}</strong><Status status={node.status} /></span>
                <span className="execution-operation-meta"><Operation node={node} /><span className={'execution-actor execution-color-' + row.color} title={node.actor_label || node.actor_id || t('Unknown agent')}>{node.actor_label ? t(node.actor_label) : node.actor_id || t('Unknown agent')}</span>
                  <time dateTime={node.started_at || undefined} title={timeText(node.started_at) ? new Date(node.started_at!).toLocaleString() : t('Time not recorded')}>{timeText(node.started_at) || '—'}</time>
                  {!!referenceCount && <span className="execution-reference-count" title={t('Folded connections: {count}', { count: referenceCount })}>↗{referenceCount}</span>}
                </span>
              </span>
            </button>
            {chosen && <div ref={setInformation} className="execution-inline" aria-label={t('Event details')}>
              <div className="execution-detail-tags">
                <span className="execution-tag execution-detail-status" data-state={statusKind(node.status)}><Status status={node.status} label={false} />{t(node.status)}</span>
                {!!node.attempt && <span className="execution-tag">{t('Attempt {count}', { count: node.attempt })}</span>}
              </div>
              <EventTiming node={node} />
              {(node.result || node.detail) && <div className="execution-detail-summary">
                <h4 className="execution-section-label">{t(node.result ? 'Outcome' : 'Details')}</h4>
                <p>{node.result || node.detail}</p>
                {node.result && node.detail && node.detail !== node.result && <p className="execution-detail-context">{node.detail}</p>}
              </div>}
              {!!selectedEdges.length && <section className="execution-relations" aria-label={t('Recorded relation')}>
                <p className="execution-causal-summary">{t('{up} upstream · {down} downstream', { up: focus.upstream.size, down: focus.downstream.size })}</p>
                {[true, false].map(incomingGroup => {
                  const edges = selectedEdges.filter(edge => (edge.target === row.id) === incomingGroup);
                  return edges.length > 0 && <div key={String(incomingGroup)} className="execution-relation-group">
                    <h4 className="execution-section-label">{incomingGroup ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{t(incomingGroup ? 'Upstream' : 'Downstream')}<span className="execution-section-count">{edges.length}</span></h4>
                    <ul className="execution-anchors">{edges.map(edge => {
                      const incoming = edge.target === row.id, id = incoming ? edge.source : edge.target, other = index.byId.get(id);
                      return <li key={edge.id}><button disabled={!graph.ports.has(id)} onClick={() => navigate(id)}
                        aria-label={edgeLabel(edge.kind, incoming) + ' ' + (other?.label ?? id)}>
                        <span className="execution-relation-body">
                          <span className="execution-relation-meta"><span className="execution-tag" data-kind={edge.kind}>{edgeLabel(edge.kind, incoming)}</span>
                            {!graph.ports.has(id) && <span className="execution-tag">{other && isPlanned(other) ? t('Planned work') : t('Unavailable')}</span>}
                          </span>
                          <span className="execution-relation-title">{other?.label ?? id}</span>
                          {edge.records.some(record => record.source !== edge.source || record.target !== edge.target) &&
                            <small className="execution-edge-evidence">{[...new Set(edge.records.map(record =>
                              (index.byId.get(record.source)?.label ?? record.source) + ' → ' + (index.byId.get(record.target)?.label ?? record.target)))].join('; ')}</small>}
                          {edge.records.some(record => record.evidence || record.artifact_ref) && <small className="execution-edge-proof">{[...new Set(edge.records.flatMap(record => [record.evidence, record.artifact_ref].filter(Boolean)))].join(' · ')}</small>}
                        </span>
                        <ArrowUpRight className="execution-relation-jump" size={13} aria-hidden="true" />
                      </button></li>;
                    })}</ul>
                  </div>;
                })}
              </section>}
              {!!streamConnections.length && <div className="execution-continuity"><h4 className="execution-section-label">{t('Same workstream, not a dependency')}</h4><ul className="execution-anchors">{streamConnections.map(track => {
                const other = track.source.id === row.id ? track.target : track.source;
                return <li key={track.id}><button aria-label={t('Workstream') + ': ' + other.node.label} onClick={() => navigate(other.id)}>
                  <span className="execution-relation-title">{other.node.label}</span><ArrowUpRight className="execution-relation-jump" size={13} aria-hidden="true" /></button></li>;
              })}</ul></div>}
              {plans.length > 0 && <div className="execution-planned"><h4 className="execution-section-label">{t('Planned work')}</h4>{plans.map(plan =>
                <p key={plan.id}><Status status={plan.realized_by ? index.byId.get(plan.realized_by)?.status ?? plan.status : plan.status} />
                  {plan.realized_by && graph.ports.has(plan.realized_by) ? <button onClick={() => navigate(plan.realized_by!)}>{plan.label}<ArrowUpRight size={11} /></button> : plan.label}</p>)}</div>}
              {!!node.artifact_refs?.length && <div className="execution-artifacts"><h4 className="execution-section-label">{t('Output artifacts')}</h4>{node.artifact_refs.map(ref => <p key={ref}><code>{ref}</code></p>)}</div>}
              {taskTools.length > 0 && <ToolSummary nodes={taskTools} />}
              {node.execution_id && <p className="execution-detail-identity"><span>{t('Execution')}:</span> <code>{node.execution_id}</code></p>}
              {!node.detail && !node.result && !node.artifact_refs?.length && !selectedEdges.length && !streamConnections.length && !plans.length && !taskTools.length && <p className="execution-empty-detail">{t('No additional details')}</p>}
            </div>}
          </div>;
        })}
      </div>}
    </div>
    {newCount > 0 && <button className="execution-new-activity" onClick={() => {
      pendingNavigation.current = graph.rows.at(-1)?.id; setSelected(graph.rows.at(-1)?.id); setNewCount(0);
    }}><ArrowDown size={13} />{t('{count} new events', { count: newCount })}</button>}
  </section>;
}
