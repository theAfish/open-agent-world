import { ArrowRight, ShieldOff, X } from "lucide-react";
import { getRelationshipOptions, getRelationshipOption } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import type { Relationship } from "../types/world";

export function EdgeInspector() {
  const selectedId = useWorldStore((state) => state.selectedEdgeId);
  const edge = useWorldStore((state) => state.edges.find((item) => item.id === selectedId));
  const source = useWorldStore((state) => state.cards.find((card) => card.id === edge?.source));
  const target = useWorldStore((state) => state.cards.find((card) => card.id === edge?.target));
  const selectEdge = useWorldStore((state) => state.selectEdge);
  const updateSelectedEdge = useWorldStore((state) => state.updateSelectedEdge);
  const deleteSelectedEdge = useWorldStore((state) => state.deleteSelectedEdge);

  if (!edge || !source || !target) return null;
  const options = getRelationshipOptions(source.type, target.type);

  return (
    <aside className="edge-inspector" aria-label="Selected relationship">
      <div className="edge-inspector-route">
        <span title={source.name}>{source.name}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span title={target.name}>{target.name}</span>
      </div>
      {options.length > 1 ? (
        <label>
          <span className="sr-only">Permission</span>
          <select
            value={edge.relationship}
            onChange={(event) => void updateSelectedEdge(event.target.value as Relationship)}
          >
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
      ) : (
        <span className="edge-inspector-permission">{getRelationshipOption(edge.relationship).label}</span>
      )}
      <button type="button" className="revoke-button" onClick={() => void deleteSelectedEdge()}>
        <ShieldOff size={14} /> Revoke
      </button>
      <button type="button" className="icon-button" onClick={() => selectEdge(undefined)} aria-label="Close relationship controls">
        <X size={14} />
      </button>
    </aside>
  );
}
