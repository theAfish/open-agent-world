/** A view of domain data. IDs, direction and relation labels stay owned by the Pack. */
export interface NetworkNode {
  id: string;
  label: string;
  kind?: string;
  /** Topic is independent of kind (capability/procedure/etc.). */
  topic?: string;
  tags?: string[];
}
export interface NetworkEdge { id: string; source: string; target: string; label?: string }
export interface NetworkData { nodes: NetworkNode[]; edges: NetworkEdge[] }
export interface Point { x: number; y: number }
export interface Island {
  id: string; label: string; members: string[]; center: Point;
  labelPosition: Point;
  rings: number[][][][];
}
export interface LayoutRequest extends NetworkData {
  positions: [string, Point][];
  communities: [string, string][];
}
export interface LayoutResult { positions: [string, Point][]; islands: Island[]; duration: number }
export interface NetworkMapHandle {
  fit(): void;
  focus(ids: string[]): void;
  arrange(): void;
}
