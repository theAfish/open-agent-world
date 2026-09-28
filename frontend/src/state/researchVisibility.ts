import type { WorldCard } from "../types/world";

/** Presentation membership only; never grants Paper/tool access to an Agent. */
export function researchVisibleIds(cards:WorldCard[], createdIds:string[], scopeMembers:string[]) {
  const ids = new Set([...createdIds,...scopeMembers]);
  for (const card of cards) if (card.type === "library.paper" || card.type.startsWith("literature.") || ['paper_skill', 'paper_modeling'].includes(String(card.config?.research_projection))) ids.add(card.id);
  // Keep a visible entity's workspace and its contents legible as one unit.
  let changed = true;
  while (changed) {
    changed = false;
    for (const card of cards) {
      if (ids.has(card.id) && card.parent_id && !ids.has(card.parent_id)) { ids.add(card.parent_id); changed = true; }
      if (card.parent_id && ids.has(card.parent_id) && !ids.has(card.id)) { ids.add(card.id); changed = true; }
    }
  }
  return ids;
}
