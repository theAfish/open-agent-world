import { create } from "zustand";
import type { PluginCatalog, WorldCard } from "../types/world";
import { getConnectionOptions } from "./relationships";

export function canEquip(resource: WorldCard, owner: WorldCard, catalog: PluginCatalog, cards: WorldCard[]): boolean {
  if (resource.ephemeral || owner.ephemeral || resource.id === owner.id || resource.equipment?.owner_id === owner.id) return false;
  if (!catalog.node_types.find((t) => t.id === owner.type)?.traits.includes("core.agent")) return false;
  if (!getConnectionOptions(catalog, owner.type, resource.type).length) return false;
  let ancestor: WorldCard | undefined = owner;
  while (ancestor) {
    if (ancestor.id === resource.id) return false;
    const id: string | null | undefined = ancestor.equipment?.owner_id ?? ancestor.parent_id;
    ancestor = cards.find((c) => c.id === id);
  }
  return true;
}

/** Ephemeral pointer intent only; ownership lives in authoritative world cards. */
export const useEquipmentDrag = create<{
  resource?: WorldCard; targetId?: string;
  set: (resource?: WorldCard, targetId?: string) => void;
}>((set) => ({ set: (resource, targetId) => set({ resource, targetId }) }));

export const useEquipmentPanel = create<{
  openIds: string[];
  inspectedId?: string;
  inspect: (id?: string) => void;
  toggle: (id: string) => void;
}>((set) => ({ openIds: [], inspect: (inspectedId) => set({ inspectedId }), toggle: (id) => set((state) => ({
  openIds: state.openIds.includes(id) ? state.openIds.filter((item) => item !== id) : [...state.openIds, id],
})) }));

export function equipmentOwner(card: WorldCard, cards: WorldCard[]): WorldCard | undefined {
  const parent = cards.find((c) => c.id === card.parent_id);
  if (card.equipment) {
    const owner = cards.find((c) => c.id === card.equipment!.owner_id);
    return owner ? equipmentOwner(owner, cards) ?? owner : undefined;
  }
  return parent ? equipmentOwner(parent, cards) : undefined;
}

export function isEquipmentConnection(sourceId: string, targetId: string, cards: WorldCard[]): boolean {
  for (const [resourceId, ownerId] of [[sourceId, targetId], [targetId, sourceId]]) {
    let current = cards.find((card) => card.id === resourceId);
    while (current) {
      if (current.equipment?.owner_id === ownerId) return true;
      const parentId = current.equipment?.owner_id ?? current.parent_id;
      current = cards.find((card) => card.id === parentId);
    }
  }
  return false;
}
