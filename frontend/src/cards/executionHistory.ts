import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot } from '../plugins/executionSources';

export const GRAPH_ROW_HEIGHT = 54;
export const GRAPH_LANES = 4;
export const MAX_THREAD_SPAN = 8;
export const isComplete = (status: string) => ['success', 'succeeded', 'completed'].includes(status);
export const isContext = (node: ExecutionNode) => node.record_type === 'context';
export const isPlanned = (node: ExecutionNode) => node.record_type === 'planned' || node.role === 'planned_step';
export const isInProgress = (status: string) => ['created', 'pending', 'running', 'waiting'].includes(status);
/** Unknown provider states keep their label and a neutral, distinct glyph. */
export const statusKind = (status: string) => isComplete(status) ? 'complete'
  : ['failed', 'error'].includes(status) ? 'failed'
  : ['needs_replanning', 'changes_requested', 'needs_rework'].includes(status) ? 'rework'
  : ['waiting', 'blocked', 'paused'].includes(status) ? 'waiting'
  : ['cancelled', 'interrupted'].includes(status) ? 'stopped'
  : status === 'running' ? 'running'
  : ['created', 'pending', 'idle'].includes(status) ? 'pending' : 'custom';
export type EventEdge = ExecutionEdge & { records: ExecutionEdge[] };
export type ExecutionIndex = ReturnType<typeof executionIndex>;
export type LaneMemory = { nodes: Map<string, number>; streams: Map<string, number>; actors: Map<string, number> };
export const laneMemory = (): LaneMemory => ({ nodes: new Map(), streams: new Map(), actors: new Map() });

function chronological(nodes: ExecutionNode[]) {
  const ordinal = new Map(nodes.map((node, i) => [node.id, i]));
  const hasTimes = nodes.some(node => Number.isFinite(Date.parse(node.started_at ?? '')));
  let lastTime = Date.parse(nodes.find(node => Number.isFinite(Date.parse(node.started_at ?? '')))?.started_at ?? '') || 0;
  const order = new Map(nodes.map(node => {
    const time = Date.parse(node.started_at ?? '');
    if (Number.isFinite(time)) lastTime = time;
    return [node.id, hasTimes ? lastTime : node.sequence ?? ordinal.get(node.id)!];
  }));
  return nodes.slice().sort((a, b) => order.get(a.id)! - order.get(b.id)!
    || (a.sequence ?? ordinal.get(a.id)!) - (b.sequence ?? ordinal.get(b.id)!));
}

/** Global chronology. Containers never reorder activities. Ordering and
 * containment never manufacture causal edges. */
export function executionIndex(snapshot?: ExecutionSnapshot) {
  const nodes = [...new Map((snapshot?.nodes ?? []).map(node => [node.id, node])).values()];
  const byId = new Map(nodes.map(node => [node.id, node]));
  const rawEdges: ExecutionEdge[] = [...new Map((snapshot?.edges ?? nodes.flatMap(node => (node.dependency_ids ?? []).map(source => ({
    id: 'control:' + source + ':' + node.id, source, target: node.id, kind: 'control' as const,
  })))).map(edge => [edge.id, edge])).values()];
  // A task attempt is the history event. Its tool calls are supporting evidence,
  // not commits. Only explicit containment establishes ownership; neither a
  // shared Agent nor workstream is enough to merge independent tasks/retries.
  const eventId = new Map<string, string>();
  for (const node of nodes) {
    let owner = node;
    const seen = new Set([node.id]);
    while (owner.kind === 'tool' && owner.parent_id) {
      const parent = byId.get(owner.parent_id);
      if (!parent || seen.has(parent.id) || isContext(parent) || isPlanned(parent)) break;
      seen.add(parent.id); owner = parent;
    }
    eventId.set(node.id, owner.kind === 'tool' ? node.id : owner.id);
  }
  // Contract recorded endpoints onto visible events. Keep every original edge
  // as evidence, without turning internal tool sequencing into a self-edge.
  const connections = new Map<string, EventEdge>();
  for (const record of rawEdges) {
    const source = eventId.get(record.source) ?? record.source, target = eventId.get(record.target) ?? record.target;
    if (source === target && record.source !== record.target) continue;
    const key = JSON.stringify([source, target, record.kind]);
    const existing = connections.get(key);
    if (existing) existing.records.push(record);
    else connections.set(key, { ...record, source, target, records: [record] });
  }
  const edges = [...connections.values()];
  const incoming = new Map<string, EventEdge[]>(), outgoing = new Map<string, EventEdge[]>();
  for (const edge of edges) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    incoming.get(edge.target)!.push(edge); outgoing.get(edge.source)!.push(edge);
  }
  const ordered = chronological(nodes);
  const details = new Map<string, ExecutionNode[]>();
  for (const node of ordered) if (eventId.get(node.id) !== node.id) {
    const owner = eventId.get(node.id)!;
    const list = details.get(owner) ?? []; list.push(node); details.set(owner, list);
  }
  // New tool timestamps must not reorder events that only have sequence data.
  const activities = chronological(nodes.filter(node => eventId.get(node.id) === node.id && !isPlanned(node) && (!isContext(node)
    || incoming.has(node.id) || outgoing.has(node.id))));
  return { nodes, byId, rawEdges, edges, incoming, outgoing, ordered, activities, eventId, details };
}
export const nodeEdges = (index: ExecutionIndex, id?: string) => id
  ? [...new Map([...(index.incoming.get(id) ?? []), ...(index.outgoing.get(id) ?? [])].map(edge => [edge.id, edge])).values()] : [];

export type PositionedRow = { id: string; node: ExecutionNode; ordinal: number; top: number; y: number; height: number; lane: number; color: number; stream: string };
export type GraphTrack = { id: string; source: PositionedRow; target: PositionedRow; edge?: EventEdge; edges: EventEdge[];
  kind: ExecutionEdge['kind'] | 'workstream'; reference: boolean; route: 'straight' | 'fork' | 'join' | 'folded';
  rail?: { lane: number; y: number }[] };

/** Traverse each direction separately: an ancestor's other children are not
 * descendants of the selected event. Workstream guides never enter this lens. */
export function causalFocus(index: ExecutionIndex, id?: string) {
  const upstream = new Set<string>(), downstream = new Set<string>(), edges = new Set<string>();
  const walk = (incoming: boolean, found: Set<string>) => {
    const seen = new Set([id!]), queue = [id!];
    for (let i = 0; i < queue.length; i++) {
      for (const edge of (incoming ? index.incoming : index.outgoing).get(queue[i]) ?? []) {
        edges.add(edge.id);
        const next = incoming ? edge.source : edge.target;
        if (!seen.has(next)) { seen.add(next); found.add(next); queue.push(next); }
      }
    }
  };
  if (id) { walk(true, upstream); walk(false, downstream); }
  return { upstream, downstream, edges, nodes: new Set([...(id ? [id] : []), ...upstream, ...downstream]) };
}

const overlaps = (a: ExecutionNode, b: ExecutionNode) => {
  const start = Date.parse(b.started_at ?? ''), previous = Date.parse(a.started_at ?? ''), finish = Date.parse(a.finished_at ?? '');
  return Number.isFinite(start) && Number.isFinite(previous) && start >= previous
    && (Number.isFinite(finish) ? finish > start : ['running', 'waiting'].includes(a.status));
};

/** Four reusable positions are routing space, not Agent or branch identities.
 * Allocate from recorded continuations, nearby structural edges and observed
 * execution intervals. Data references never reserve an entire historical lane.
 * Keep assigned vertices stable; later conflicts become explicit references. */
export function layoutGraph(index: ExecutionIndex, memory: LaneMemory, selected?: string, detailHeight = 0, query = '') {
  const positions = new Map(index.activities.map((node, i) => [node.id, i]));
  const reservations = new Map<string, number>();
  const previousStream = new Map<string, ExecutionNode>();
  const continuations = new Map<string, ExecutionNode>();
  const reserve = (source: string, target: number) => reservations.set(source, Math.max(reservations.get(source) ?? -1, target));
  for (const edge of index.edges) {
    const start = positions.get(edge.source), end = positions.get(edge.target);
    if (edge.kind !== 'data' && start !== undefined && end !== undefined && end > start && end - start <= MAX_THREAD_SPAN) reserve(edge.source, end);
  }
  for (const node of index.activities) if (node.workstream_id) {
    const prior = previousStream.get(node.workstream_id);
    if (prior && !overlaps(prior, node)) {
      continuations.set(node.id, prior);
      if (positions.get(node.id)! - positions.get(prior.id)! <= MAX_THREAD_SPAN) reserve(prior.id, positions.get(node.id)!);
    }
    previousStream.set(node.workstream_id, node);
  }
  const dated = index.activities.flatMap((node, position) => {
    const time = Date.parse(node.started_at ?? '');
    return Number.isFinite(time) ? [{ time, position }] : [];
  });
  const occupied = new Map<number, number>();
  const occupants = new Map<number, string>();
  for (const node of index.activities) {
    const position = positions.get(node.id)!;
    for (const [lane, end] of occupied) if (end <= position) occupied.delete(lane);
    if (!memory.nodes.has(node.id)) {
      const prior = continuations.get(node.id);
      const parents = (index.incoming.get(node.id) ?? []).filter(edge => edge.kind !== 'data'
        && (positions.get(edge.source) ?? position) < position).map(edge => edge.source);
      const predecessors = [...(prior ? [prior.id] : []), ...parents];
      // A successor can continue its own predecessor's cable even when that
      // predecessor also has a later replan/fan-out reference. It cannot take
      // a lane occupied by another task, or by an observed overlapping task.
      const inherited = predecessors.map(id => ({ id, lane: memory.nodes.get(id) })).find(({ id, lane }) =>
        lane !== undefined && (!occupied.has(lane) || (occupants.get(lane) === id && !overlaps(index.byId.get(id)!, node))));
      const streamLane = node.workstream_id ? memory.streams.get(node.workstream_id) : undefined;
      const available = Array.from({ length: GRAPH_LANES }, (_, i) => i).filter(lane => !occupied.has(lane));
      const lane = inherited?.lane ?? (streamLane !== undefined && available.includes(streamLane) ? streamLane : undefined)
        ?? available[0] ?? [...occupied].sort((a, b) => a[1] - b[1] || a[0] - b[0])[0][0];
      memory.nodes.set(node.id, lane);
      if (node.workstream_id) memory.streams.set(node.workstream_id, lane);
    }
    const lane = memory.nodes.get(node.id)!;
    let end = reservations.get(node.id) ?? position;
    const finish = Date.parse(node.finished_at ?? '');
    if (Number.isFinite(finish) && Number.isFinite(Date.parse(node.started_at ?? ''))) {
      let lo = 0, hi = dated.length;
      while (lo < hi) { const mid = (lo + hi) >>> 1; if (dated[mid].time < finish) lo = mid + 1; else hi = mid; }
      // Release at the first event starting at/after this recorded completion.
      end = Math.max(end, dated[lo]?.position ?? index.activities.length);
    } else if (['running', 'waiting'].includes(node.status) && Number.isFinite(Date.parse(node.started_at ?? ''))) end = index.activities.length;
    occupied.set(lane, Math.max(occupied.get(lane) ?? position, end));
    occupants.set(lane, node.id);
  }
  const needle = query.trim().toLocaleLowerCase();
  const searchable = (node: ExecutionNode) => [node.label, node.actor_label, node.workstream_label, node.execution_id, node.status, node.detail, node.result, node.id].join(' ').toLocaleLowerCase();
  const matchIds = needle ? index.activities.filter(node => searchable(node).includes(needle)
    || (index.details.get(node.id) ?? []).some(detail => searchable(detail).includes(needle))).map(node => node.id) : [];
  let top = 12;
  const rows: PositionedRow[] = index.activities.map((node, ordinal) => {
    const lane = memory.nodes.get(node.id) ?? 0;
    // Absence of actor identity is unknown; a workstream is never an Agent.
    const actor = node.actor_id;
    if (actor && !memory.actors.has(actor)) memory.actors.set(actor, memory.actors.size % 6);
    const height = GRAPH_ROW_HEIGHT + (node.id === selected ? detailHeight : 0);
    const row = { id: node.id, node, ordinal, top, y: top + GRAPH_ROW_HEIGHT / 2, height, lane,
      color: actor ? memory.actors.get(actor)! : -1, stream: node.workstream_id ?? '' };
    top += height; return row;
  });
  const ports = new Map(rows.map(row => [row.id, row]));
  const bundles = new Map<string, GraphTrack>(), unavailable: EventEdge[] = [];
  for (const edge of index.edges) {
    const source = ports.get(edge.source), target = ports.get(edge.target);
    if (!source || !target) { unavailable.push(edge); continue; }
    const key = JSON.stringify([source.id, target.id]);
    const bundle = bundles.get(key);
    if (bundle) bundle.edges.push(edge);
    else bundles.set(key, { id: key, source, target, edge, edges: [edge], kind: edge.kind, reference: false, route: 'straight' });
  }
  for (const [id, prior] of continuations) {
    const key = JSON.stringify([prior.id, id]);
    if (!bundles.has(key)) bundles.set(key, { id: key, source: ports.get(prior.id)!, target: ports.get(id)!,
      edges: [], kind: 'workstream', reference: false, route: 'straight' });
  }
  const tracks = [...bundles.values()];
  const laneRows = Array.from({ length: GRAPH_LANES }, () => [] as PositionedRow[]);
  for (const row of rows) laneRows[row.lane].push(row);
  const blocked = (lane: number, first: number, last: number) => {
    const candidates = laneRows[lane];
    let lo = 0, hi = candidates.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (candidates[mid].ordinal <= first) lo = mid + 1; else hi = mid; }
    return lo < candidates.length && candidates[lo].ordinal < last;
  };
  const references = new Map<string, GraphTrack[]>();
  for (const track of tracks) {
    const { source, target } = track;
    const span = target.ordinal - source.ordinal;
    if (span > 0 && span <= MAX_THREAD_SPAN) {
      if (source.lane === target.lane && !blocked(source.lane, source.ordinal, target.ordinal)) track.route = 'straight';
      else if (source.lane !== target.lane) {
        const fork = track.edges.some(edge => edge.kind === 'fork');
        const canFork = !blocked(target.lane, source.ordinal, target.ordinal);
        const canJoin = !blocked(source.lane, source.ordinal, target.ordinal);
        track.route = (fork && canFork) || (!canJoin && canFork) ? 'fork' : canJoin ? 'join' : 'folded';
      } else track.route = 'folded';
    } else track.route = 'folded';
    track.reference = track.route === 'folded';
    if (track.reference) for (const id of new Set([source.id, target.id])) {
      const list = references.get(id) ?? []; list.push(track); references.set(id, list);
    }
  }
  compactTracks(tracks, rows);
  return { rows, ports, tracks, unavailable, references, matchIds, height: top + 14,
    // Fixed routing space avoids shifting every label when a new branch arrives.
    lanes: GRAPH_LANES };
}

/** Reuse inner rails after their work ends. Only cable geometry changes: event
 * positions, identities and dependency endpoints stay fixed. Reserve both row
 * crossings and the spaces between rows, including other cables, so a bend
 * cannot accidentally look like a connection to unrelated work. */
function compactTracks(tracks: GraphTrack[], rows: PositionedRow[]) {
  const atRow = rows.map(() => new Int32Array(GRAPH_LANES));
  const between = rows.map(() => new Int32Array(GRAPH_LANES));
  for (const row of rows) atRow[row.ordinal][row.lane]++;
  const routes = new Map<GraphTrack, number[]>();
  const occupy = (track: GraphTrack, lanes: number[], delta: number) => {
    const start = track.source.ordinal, end = lanes.length - 1;
    const mark = (counts: Int32Array, a: number, b: number) => {
      for (let lane = Math.min(a, b); lane <= Math.max(a, b); lane++) counts[lane] += delta;
    };
    for (let i = 0; i <= end; i++) {
      mark(atRow[start + i], lanes[i], i === 0 ? track.source.lane : i === end ? track.target.lane : lanes[i]);
      if (i < end) mark(between[start + i], lanes[i], lanes[i + 1]);
    }
  };
  for (const track of tracks) if (!track.reference) {
    const lane = track.route === 'fork' ? track.target.lane : track.source.lane;
    const lanes = Array<number>(track.target.ordinal - track.source.ordinal + 1).fill(lane);
    routes.set(track, lanes); occupy(track, lanes, 1);
  }
  // Earlier-ending cables settle first. Appending independent events does not
  // change an existing route; no second persistent layout state is needed.
  const candidates = [...routes.keys()].filter(track => track.target.ordinal - track.source.ordinal > 1
    && (track.route === 'join' ? track.source.lane - track.target.lane : track.target.lane - track.source.lane) > 1)
    .sort((a, b) => a.target.ordinal - b.target.ordinal || a.source.ordinal - b.source.ordinal || a.id.localeCompare(b.id));
  for (const track of candidates) {
    const lanes = routes.get(track)!, start = track.source.ordinal, end = track.target.ordinal;
    const join = track.route === 'join', minimum = (join ? track.target.lane : track.source.lane) + 1;
    occupy(track, lanes, -1);
    for (let gap = join ? start : end - 1; join ? gap < end : gap >= start; gap += join ? 1 : -1) {
      const current = lanes[gap - start + (join ? 0 : 1)];
      for (let lane = minimum; lane < current; lane++) {
        // Check the whole remaining inner corridor, not only the next row.
        // This avoids a weave inward immediately followed by a weave outward.
        let free = true;
        for (let col = lane; col <= current; col++) if (between[gap][col]) free = false;
        for (let row = join ? gap + 1 : start + 1; row < (join ? end : gap + 1); row++) if (atRow[row][lane]) free = false;
        for (let row = join ? gap : start; row < (join ? end : gap + 1); row++) if (between[row][lane]) free = false;
        if (!free) continue;
        for (let i = join ? gap - start + 1 : 0; i <= (join ? end - start : gap - start); i++) lanes[i] = lane;
        break;
      }
    }
    occupy(track, lanes, 1);
    const rail = [{ lane: lanes[0], y: track.source.y }];
    for (let i = 1; i < lanes.length; i++) if (lanes[i] !== lanes[i - 1]) rail.push({ lane: lanes[i],
      y: join ? rows[start + i - 1].y + GRAPH_ROW_HEIGHT / 2 : rows[start + i].y - GRAPH_ROW_HEIGHT / 2 });
    if (rail.length > 1) track.rail = rail;
  }
}

/** Git-style rails: straight vertical/horizontal segments meet at tangent
 * quarter-circle arcs. Opposite arcs form an S where an inner rail frees up.
 * A bounded radius keeps short and long edges consistent.
 * Folded references never become paths; focus is strictly a styling concern. */
export function trackPath(track: GraphTrack, x: (lane: number) => number) {
  if (track.reference) return '';
  const sx = x(track.source.lane), tx = x(track.target.lane), sy = track.source.y, ty = track.target.y;
  const radius = (from: number, to: number) => Math.min(14, Math.abs(to - from), (ty - sy) / 2);
  const fork = (from: number, to: number) => {
    const side = Math.sign(to - from), r = radius(from, to);
    return `H${to - side * r} A${r},${r} 0 0 ${side > 0 ? 1 : 0} ${to},${sy + r}`;
  };
  const join = (from: number, to: number) => {
    const side = Math.sign(to - from), r = radius(from, to);
    return `V${ty - r} A${r},${r} 0 0 ${side < 0 ? 1 : 0} ${from + side * r},${ty} H${to}`;
  };
  const start = `M${sx},${sy}`;
  const rail = track.rail ?? [{ lane: track.route === 'fork' ? track.target.lane : track.source.lane, y: sy }];
  let current = x(rail[0].lane), path = start + (current === sx ? '' : ` ${fork(sx, current)}`);
  for (const point of rail.slice(1)) {
    const next = x(point.lane), side = Math.sign(next - current);
    // Leave 14px clear at either row for endpoint bends and node glyphs.
    const r = Math.min(14, GRAPH_ROW_HEIGHT / 2 - 14, Math.abs(next - current) / 2);
    path += ` V${point.y - r} A${r},${r} 0 0 ${side < 0 ? 1 : 0} ${current + side * r},${point.y}`
      + ` H${next - side * r} A${r},${r} 0 0 ${side > 0 ? 1 : 0} ${next},${point.y + r}`;
    current = next;
  }
  return path + (current === tx ? ` V${ty}` : ` ${join(current, tx)}`);
}
