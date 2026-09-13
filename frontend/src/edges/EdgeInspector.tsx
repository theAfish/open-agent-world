import { t, useLocale } from "../i18n";
import { ArrowLeftRight, ArrowRight, ShieldOff, X } from "lucide-react";
import { getRelationshipOptions, getRelationshipOption } from "../state/relationships";
import { useWorldStore } from "../state/worldStore";
import type { EdgeDirection, Relationship } from "../types/world";

export function EdgeInspector() {
  useLocale();
  const selectedId = useWorldStore((state) => state.selectedEdgeId);
  const catalog = useWorldStore((state) => state.catalog);
  const edge = useWorldStore((state) => state.edges.find((item) => item.id === selectedId));
  const source = useWorldStore((state) => state.cards.find((card) => card.id === edge?.source));
  const target = useWorldStore((state) => state.cards.find((card) => card.id === edge?.target));
  const selectEdge = useWorldStore((state) => state.selectEdge);
  const updateSelectedEdge = useWorldStore((state) => state.updateSelectedEdge);
  const deleteSelectedEdge = useWorldStore((state) => state.deleteSelectedEdge);

  if (!edge || !source || !target) return null;
  const options = getRelationshipOptions(catalog, source.type, target.type);
  const activeOption = getRelationshipOption(catalog, edge.relationship);
  const generated = catalog.relationships.find((item) => item.id === edge.relationship)?.generated;

  return (
    <aside className="edge-inspector" aria-label={t("Selected relationship")}>
      <div className="edge-inspector-route">
        <span title={source.name}>{source.name}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <span title={target.name}>{target.name}</span>
      </div>
      {!generated && options.length > 1 ? (
        <label>
          <span className="sr-only">{t("Permission")}</span>
          <select
            value={edge.relationship}
            onChange={(event) => {
              const relationship = event.target.value as Relationship;
              const selectedOption = options.find((option) => option.value === relationship);
              void updateSelectedEdge({
                relationship,
                ...(selectedOption?.directions.includes(edge.direction)
                  ? {}
                  : { direction: "forward" }),
              });
            }}
          >
            {options.map((option) => <option key={option.value} value={option.value}>{t(option.label)}</option>)}
          </select>
        </label>
      ) : (
        <span className="edge-inspector-permission">{t(activeOption.label)}</span>
      )}
      {activeOption.directions.includes("bidirectional") && (
        <label className="edge-direction-control">
          {edge.direction === "bidirectional"
            ? <ArrowLeftRight size={13} aria-hidden="true" />
            : <ArrowRight size={13} aria-hidden="true" />}
          <span className="sr-only">{t("Direction")}</span>
          <select
            aria-label={t("Relationship direction")}
            value={edge.direction}
            onChange={(event) => void updateSelectedEdge({ direction: event.target.value as EdgeDirection })}
          >
            <option value="forward">{t("One-way")}</option>
            <option value="bidirectional">{t("Two-way")}</option>
          </select>
        </label>
      )}
      <button type="button" className="revoke-button" onClick={() => void deleteSelectedEdge()}>
        <ShieldOff size={14} /> {generated ? t("Disconnect") : t("Revoke")}
      </button>
      <button type="button" className="icon-button" onClick={() => selectEdge(undefined)} aria-label={t("Close relationship controls")}>
        <X size={14} />
      </button>
    </aside>
  );
}
