import { useShallow } from "zustand/react/shallow";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { nodeActivity } from "./activity";

export function useNodeActivity(card: WorldCard, aggregate = false) {
  // Subscribe to the displayed activity, not the entire world's arrays. Keep
  // aggregation live, but unrelated edits/output must not repaint every card.
  return useWorldStore(useShallow(state => nodeActivity(card, state.cards, state.events, aggregate)));
}
