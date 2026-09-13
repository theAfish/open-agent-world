import { t, useLocale } from "../i18n";
import { Layers3, Link2 } from "lucide-react";
import { useMemo, useState } from "react";
import { summarizeLegionSelection } from "../state/legions";
import { useWorldStore } from "../state/worldStore";

export function LegionSelection() {
  useLocale();
  const cards = useWorldStore((s) => s.cards);
  const stressCards = useWorldStore((s) => s.stressCards);
  const edges = useWorldStore((s) => s.edges);
  const catalog = useWorldStore((s) => s.catalog);
  const selectedIds = useWorldStore((s) => s.selectedCardIds);
  const positionBusy = useWorldStore((s) => s.positionCommitBusy);
  const syncState = useWorldStore((s) => s.syncState);
  const formGroup = useWorldStore((s) => s.formLegionGroup);
  const [busy, setBusy] = useState(false);
  const selection = useMemo(() => summarizeLegionSelection([...cards, ...stressCards], edges, selectedIds, catalog),
    [cards, stressCards, edges, selectedIds, catalog]);
  if (selection.cards.length < 2) return null;
  const canForm = !busy && !positionBusy && syncState !== "offline"
    && selection.cards.every((card) => !card.ephemeral && card.type !== "legion" && !card.parent_id);
  return <aside className="legion-selection-bar" aria-label={t("Selected formation actions")} data-testid="legion-selection-bar">
    <span className="legion-selection-mark" aria-hidden="true"><Layers3 size={15} /></span>
    <div className="legion-selection-summary"><strong>{selection.cards.length} {t("selected")}</strong>
      <span><Link2 size={11} /> {selection.internalEdges.length} {t("internal links")} {selection.externalEdges.length > 0 && t(" / {v0} external links retained", { v0: String(selection.externalEdges.length) })}</span></div>
    <span className="legion-help">{t("Form a team, configure it, then save it to your library.")}</span>
    <button className="primary-button" disabled={!canForm} onClick={async () => {
      setBusy(true); try { await formGroup(selectedIds); } finally { setBusy(false); }
    }}><Layers3 size={14} /> {busy ? t("Forming...") : t("Form Legion")}</button>
  </aside>;
}
