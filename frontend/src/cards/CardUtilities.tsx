import { ArrowLeftRight, ArrowRight, Link2Off } from "lucide-react";
import { getRelationshipOption } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";

export function RelationshipList({ card, empty = "No capabilities connected yet." }: { card: WorldCard; empty?: string }) {
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const catalog = useWorldStore((state) => state.catalog);
  const relationships = edges.filter((edge) => edge.source === card.id || edge.target === card.id);

  if (relationships.length === 0) {
    return (
      <div className="mini-empty">
        <Link2Off size={14} aria-hidden="true" />
        <span>{empty}</span>
      </div>
    );
  }

  return (
    <ul className="relationship-list">
      {relationships.map((edge) => {
        const outgoing = edge.source === card.id;
        const otherId = outgoing ? edge.target : edge.source;
        const other = cards.find((item) => item.id === otherId);
        return (
          <li key={edge.id}>
            <span title={other?.name ?? otherId}>{other?.name ?? "Unknown object"}</span>
            {edge.direction === "bidirectional" ? (
              <ArrowLeftRight size={12} aria-hidden="true" />
            ) : (
              <ArrowRight
                size={12}
                className={outgoing ? undefined : "is-reversed"}
                aria-hidden="true"
              />
            )}
            <small>{getRelationshipOption(catalog, edge.relationship).shortLabel}</small>
          </li>
        );
      })}
    </ul>
  );
}

export function InstrumentOutput({ lines, empty }: { lines: string[]; empty: string }) {
  return (
    <div className="instrument-output" role="log" aria-live="polite">
      {lines.length > 0 ? (
        lines.slice(-12).map((line, index) => <div key={`${index}-${line.slice(0, 20)}`}>{line}</div>)
      ) : (
        <span className="output-empty">{empty}</span>
      )}
    </div>
  );
}
