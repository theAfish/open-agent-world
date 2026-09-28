export type CitationRoad = {id:string;title:string;frontier_id?:string|null;member_ids:string[];scope_revision?:number};

/** Choosing a destination never relocates an existing Paper on another road. */
export function defaultCitationRoad(roads:CitationRoad[], scopeId:string, sourceId:string, activeScopeId?:string, frontierId?:string):string {
  const selected = activeScopeId === scopeId && frontierId ? roads.find(road => road.frontier_id === frontierId) : undefined;
  return selected?.id ?? roads.find(road => road.member_ids.includes(`paper:${sourceId}`))?.id ?? "trunk";
}
