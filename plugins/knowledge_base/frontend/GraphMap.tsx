import { NetworkMap, t, type NetworkData } from '@oaw/plugin-api';
import { useMemo } from 'react';

export type Entity = { id: string; type: string; name: string; properties: Record<string, unknown> };
export type Relation = { id: string; type: string; source_id: string; target_id: string };
type Props = {
  graphKey: string; entities: Entity[]; relations: Relation[]; selected: string;
  onSelect: (id: string) => void; onExpand: (id: string) => void;
};

/** Published entity IDs and relation direction pass through unchanged. */
export function GraphMap({ graphKey, entities, relations, selected, onSelect, onExpand }: Props) {
  const data = useMemo<NetworkData>(() => ({
    nodes: entities.map(entity => ({ id: entity.id, label: entity.name, kind: entity.type,
      topic: typeof entity.properties.topic === 'string' ? entity.properties.topic : undefined })),
    edges: relations.map(relation => ({ id: relation.id, source: relation.source_id, target: relation.target_id, label: relation.type.replaceAll('_', ' ') })),
  }), [entities, relations]);
  return <div className="knowledge-map"><NetworkMap graphKey={graphKey} data={data} selected={selected ? [selected] : []}
    onSelect={onSelect} onSelectionChange={ids => { if (!ids.length) onSelect(''); }} onExpand={onExpand} label={t('Knowledge graph map')} /></div>;
}
