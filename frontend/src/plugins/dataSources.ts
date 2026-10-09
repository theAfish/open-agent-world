/** Config field names and compatible datasets are declared by the consuming Pack. */
export interface DataConsumer {
  source_field: string; schema_field: string; kinds: ('table' | 'graph')[];
}
export interface DataSchema {
  id: string; label: string; kind: 'table' | 'graph';
  fields: {name: string; type: string}[]; aggregates?: boolean;
}
export interface DataQuery {
  schema_id: string; columns?: string[]; limit?: number; group_by?: string;
  value?: string; aggregate?: 'none' | 'count' | 'sum' | 'mean' | 'min' | 'max';
  order_by?: string; descending?: boolean; entity_type?: string; relation_type?: string;
}
export interface Dataset {
  kind: 'table' | 'graph'; columns?: string[]; rows?: unknown[][];
  nodes?: {id: string; name: string; type?: string}[];
  edges?: {id: string; source: string; target: string; type?: string}[];
  truncated: boolean; scope?: string; value_column?: string;
}
export interface DataSourceHost {
  list(): Promise<{sources: {id: string; name: string; type: string}[]}>;
  schemas(source: string): Promise<{schemas: DataSchema[]; truncated?: boolean}>;
  read(source: string, query: DataQuery): Promise<Dataset>;
  /** Opens the schema chooser. Subscribe for changes after the user confirms. */
  connect(source: string, relationship: string): Promise<void>;
  subscribe(listener: () => void): () => void;
}
