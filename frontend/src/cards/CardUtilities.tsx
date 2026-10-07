import { t, useLocale } from "../i18n";
import { ArrowLeftRight, ArrowRight, GitBranch, Link2Off } from "lucide-react";
import { getRelationshipOption } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import { useStateMachineEditor } from "../stateMachines/editorStore";
import { stateMachineEditorAvailable } from "../stateMachines/model";
import { useWorkspaceAccess } from "../workspace/WorkspaceAccess";
import type { WorldCard } from "../types/world";

export function StateMachineButton({ card, compact = false }: { card: WorldCard; compact?: boolean }) {
  useLocale();
  const { deployed } = useWorkspaceAccess();
  const catalog = useWorldStore(state => state.catalog);
  if (deployed || !stateMachineEditorAvailable(card, catalog)) return null;
  return <button type="button" className={`${compact ? 'icon-button' : 'secondary-button'} nodrag nopan`}
    aria-label={t("State machine")} title={t("Edit states and event-driven transitions")}
    onClick={event => { event.stopPropagation(); useStateMachineEditor.getState().open(card.id); }}>
    <GitBranch size={14} aria-hidden="true" />{!compact && t("State machine")}
  </button>;
}

export function RelationshipList({ card, empty = t("No capabilities connected yet.") }: { card: WorldCard; empty?: string }) {
  useLocale();
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
            <span title={other?.name ?? otherId}>{other?.name ?? t("Unknown object")}</span>
            {edge.direction === "bidirectional" ? (
              <ArrowLeftRight size={12} aria-hidden="true" />
            ) : (
              <ArrowRight
                size={12}
                className={outgoing ? undefined : "is-reversed"}
                aria-hidden="true"
              />
            )}
            <small>{t(getRelationshipOption(catalog, edge.relationship).shortLabel)}</small>
          </li>
        );
      })}
    </ul>
  );
}

export function InstrumentOutput({ lines, empty }: { lines: string[]; empty: string }) {
  useLocale();
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
