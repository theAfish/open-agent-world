import { useMemo } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { nodeActivity } from "./activity";

export function useNodeActivity(card: WorldCard, aggregate = false) {
  const cards = useWorldStore((state) => state.cards);
  const events = useWorldStore((state) => state.events);
  return useMemo(() => nodeActivity(card, cards, events, aggregate), [card, cards, events, aggregate]);
}
