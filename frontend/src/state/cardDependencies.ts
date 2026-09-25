import { t } from "../i18n";
import { useCardLibrary, type LibrarySnapshot } from "./cardLibrary";

export function collectionDependencies(snapshot: LibrarySnapshot, cardIds: string[]) {
  const required = new Set(cardIds);
  // Containers declare the cards their creation action adds. Follow this public
  // contract recursively, including dependencies of already collected parents.
  for (const id of required) {
    const member = snapshot.card_definitions[id]?.container?.member_type;
    if (member) required.add(member);
  }
  const missing = [...required].filter(id => !snapshot.collection[id]?.unlocked);
  const packs: LibrarySnapshot["packs"][string][] = [];
  const blocked: string[] = [];
  for (const id of missing) {
    if (packs.some(pack => pack.definition.cards.includes(id))) continue;
    const pack = Object.values(snapshot.packs).find(pack => pack.owned
      && snapshot.available_pack_ids.includes(pack.definition.id) && pack.definition.cards.includes(id));
    if (pack) packs.push(pack);
    else blocked.push(id);
  }
  return { missing, packs, blocked };
}

// Shared by placement and the Library. Cancellation never mutates the collection.
export async function ensureCardsCollected(cardIds: string[]): Promise<boolean> {
  if (!useCardLibrary.getState().snapshot) await useCardLibrary.getState().refresh();
  const snapshot = useCardLibrary.getState().snapshot;
  if (!snapshot || useCardLibrary.getState().busy) return false;
  const plan = collectionDependencies(snapshot, cardIds);
  if (!plan.missing.length) return true;
  if (plan.blocked.length) {
    useCardLibrary.setState({ error: t("Required cards have no available owned pack: {cards}", { cards: plan.blocked.join(", ") }), open: true, tab: "packs" });
    return false;
  }
  const cards = plan.missing.map(id => `${t(snapshot.card_definitions[id]?.label ?? id)} (${id})`).join("\n");
  const packs = plan.packs.map(pack => t(pack.definition.name)).join(", ");
  if (!window.confirm(t("This action requires uncollected cards:\n{cards}\n\nOpen these owned packs and continue?\n{packs}", { cards, packs }))) return false;
  for (const pack of plan.packs) {
    const current = useCardLibrary.getState().snapshot;
    if (current && pack.definition.cards.every(id => current.collection[id]?.unlocked)) continue;
    if (!await useCardLibrary.getState().edit({ action: "open_pack", id: pack.definition.id })) {
      useCardLibrary.setState({ open: true, tab: "packs" });
      return false;
    }
  }
  const current = useCardLibrary.getState().snapshot;
  return Boolean(current && !collectionDependencies(current, cardIds).missing.length);
}
