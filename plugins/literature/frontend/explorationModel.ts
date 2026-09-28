export type ExplorationKind = "trail" | "paper" | "method" | "web" | "perspective" | "collection";
export type ExplorationEntity = {
  id: string; node_id: string | null; kind: ExplorationKind; title: string; scope_revision: number;
  frontier_id?: string; paper_id?: string; method_id?: string; paper_ids?: string[];
  url?: string; rationale?: string; source_ids?: string[];
};
export type ExplorationLink = {
  id: string; source: string; target: string;
  relation: "contains" | "discovers" | "method" | "supports" | "contrasts" | "related";
  rationale: string;
};
export type PathCamp = { frontier_id: string; barracks_id: string; rationale: string; task_id?: string };
export type ExplorationRoad = {
  id: string; title: string; parent_id: string | null; anchor_id: string | null;
  mode: "chain" | "branch"; member_ids: string[]; frontier_id?: string; scope_revision: number; attach_after?: string | null;
};

export type RoadConnection = {source: string; target: string; roadId: string; mode: "chain" | "branch"};

/** Physical navigation segments, shared by the UI map and its reveal corridor. */
export function roadConnections(roads: ExplorationRoad[], entities: ExplorationEntity[], indexNodeId: string): RoadConnection[] {
  const nodes = new Map(entities.map(entity => [entity.id, entity.node_id]));
  const result: RoadConnection[] = [];
  const departure=nodes.get("trail:origin") ?? indexNodeId;
  const origin = (road: ExplorationRoad) => road.anchor_id ? nodes.get(road.anchor_id) : departure;
  const push = (source: string | null | undefined, target: string | null | undefined, road: ExplorationRoad) => {
    if (source && target && source !== target) result.push({source,target,roadId:road.id,mode:road.mode});
  };
  if(departure!==indexNodeId && roads[0]) push(indexNodeId,departure,roads.find(road=>road.id==="trunk") ?? roads[0]);
  for (const road of roads) {
    const roadOrigin = origin(road);
    if (!road.parent_id && road.anchor_id) push(departure,roadOrigin,road);
    if (road.parent_id) {
      const parent = roads.find(item => item.id === road.parent_id);
      if (parent) {
        const attach = road.mode === "branch" && road.attach_after && [parent.anchor_id,...parent.member_ids].includes(road.attach_after) ? nodes.get(road.attach_after) : undefined;
        const tail = [...parent.member_ids].reverse().map(id => nodes.get(id)).find(Boolean) ?? origin(parent);
        push(attach ?? tail, roadOrigin, road);
      }
    }
    let previous = roadOrigin;
    for (const id of road.member_ids) {
      const node = nodes.get(id);
      if (!node) continue;
      push(previous,node,road); previous = node;
    }
  }
  return result;
}

/** Preserve unknown/stale member IDs as well: the server owns road membership. */
export function reorderRoadMembers(members: string[], id: string, direction: -1 | 1): string[] {
  const from = members.indexOf(id), to = from + direction;
  if (from < 0 || to < 0 || to >= members.length) return [...members];
  const next = [...members]; [next[from], next[to]] = [next[to], next[from]]; return next;
}

/** A road may attach to another road, never to itself or one of its descendants. */
export function availableRoadParents(roads: ExplorationRoad[], roadId: string) {
  const descendants = new Set([roadId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const road of roads) if (road.parent_id && descendants.has(road.parent_id) && !descendants.has(road.id)) {
      descendants.add(road.id); changed = true;
    }
  }
  return roads.filter(road => !descendants.has(road.id));
}

export function orderedRoads(roads: ExplorationRoad[]) {
  const result: {road: ExplorationRoad; depth: number}[] = [], visited = new Set<string>();
  const append = (road: ExplorationRoad, depth: number) => {
    if (visited.has(road.id)) return;
    visited.add(road.id); result.push({road, depth});
    for (const child of roads) if (child.parent_id === road.id) append(child, depth + 1);
  };
  for (const road of roads) if (!road.parent_id) append(road, 0);
  // Retain orphaned records in the index rather than silently hiding provenance.
  for (const road of roads) if (!visited.has(road.id)) append(road, 0);
  return result;
}

/** A shared finding can belong to more than one branch without duplicating its Paper. */
export function branchEntities(entities: ExplorationEntity[], links: ExplorationLink[], frontierId?: string) {
  if (!frontierId) return entities;
  const trailIds = new Set(entities.filter(entity => entity.kind === "trail" && entity.frontier_id === frontierId).map(entity => entity.id));
  const connected = new Set(links.flatMap(link => trailIds.has(link.source) ? [link.target] : trailIds.has(link.target) ? [link.source] : []));
  const direct = entities.filter(entity => entity.frontier_id === frontierId || connected.has(entity.id));
  const paperIds = new Set(direct.flatMap(entity => entity.paper_id ? [entity.paper_id] : entity.kind === "collection" ? entity.paper_ids ?? [] : []));
  return entities.filter(entity => direct.includes(entity) || (entity.kind === "method" && entity.paper_ids?.some(id => paperIds.has(id))));
}

export function filterExploration(entities: ExplorationEntity[], query: string, kind?: ExplorationKind) {
  const needle = query.trim().toLocaleLowerCase();
  return entities.filter(entity => (!kind || entity.kind === kind) && (!needle || `${entity.title} ${entity.rationale ?? ""} ${entity.url ?? ""}`.toLocaleLowerCase().includes(needle)));
}

export function scopePaperIds(value: {paper_ids: string[]; revisions: {seed_paper_ids: string[]}[]}) {
  return [...new Set([...value.paper_ids, ...(value.revisions.at(-1)?.seed_paper_ids ?? [])])];
}
