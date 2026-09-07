import { describe, expect, it } from "vitest";
import { normalizeCard } from "../api/client";
import { TEST_CATALOG } from "./catalog.fixture";
import { buildCardDraft } from "./helpers";
import { canEquip, equipmentOwner } from "./equipment";
import { ownedDescendants, parentFirst } from "./containers";
import { validateConnection } from "./relationships";
import type { PluginCatalog, WorldCard } from "../types/world";

const catalog: PluginCatalog = TEST_CATALOG;
const card = (id: string, type: string): WorldCard => ({ id, ...buildCardDraft(type, { x: 0, y: 0 }) });

describe("equipment contracts", () => {
  it("rejects owner connections in either direction, including nested equipment", () => {
    const owner = card("owner", "agent");
    const resource = { ...card("resource", "sandbox"), equipment: { owner_id: owner.id, relationship: "execute" } };
    const nested = { ...card("nested", "text"), parent_id: resource.id };
    const other = card("other", "agent");
    const cards = [owner, resource, nested, other];
    for (const item of [resource, nested]) {
      expect(validateConnection(catalog, owner.id, item.id, owner.type, item.type, [], cards).valid).toBe(false);
      expect(validateConnection(catalog, item.id, owner.id, item.type, owner.type, [], cards).valid).toBe(false);
    }
    expect(validateConnection(catalog, other.id, resource.id, other.type, resource.type, [], cards).valid).toBe(true);
  });
  it("separates equipment eligibility from graph relationships and rejects ownership cycles", () => {
    const agent = card("agent", "agent");
    const resource = card("sandbox", "sandbox");
    const conversation = card("conversation", "conversation");
    expect(canEquip(conversation, agent, catalog, [conversation, agent])).toBe(true);
    const nonportable = { ...catalog, node_types: catalog.node_types.map((type) => ({ ...type, templateable: false })) };
    expect(canEquip(conversation, agent, nonportable, [conversation, agent])).toBe(true);
    expect(canEquip(resource, agent, catalog, [resource, agent])).toBe(true);
    expect(canEquip(agent, resource, catalog, [resource, agent])).toBe(false);
    expect(canEquip(resource, { ...agent, parent_id: resource.id }, catalog, [resource, agent])).toBe(false);
    expect(canEquip({ ...resource, ephemeral: true }, agent, catalog, [resource, agent])).toBe(false);
  });

  it("preserves ownership through transport, nested display and dependency ordering for undo", () => {
    const agent = card("agent", "agent");
    const resource = normalizeCard({ ...card("sandbox", "sandbox"), equipment: { owner_id: agent.id, relationship: "execute" } });
    const nested = { ...card("file", "text"), parent_id: resource.id };
    const cards = [nested, resource, agent];
    expect(resource.equipment).toEqual({ owner_id: agent.id, relationship: "execute" });
    expect(equipmentOwner(nested, cards)?.id).toBe(agent.id);
    expect(ownedDescendants(cards, agent.id).map((c) => c.id)).toEqual([resource.id, nested.id]);
    expect(parentFirst(cards).map((c) => c.id)).toEqual([agent.id, resource.id, nested.id]);
  });
});
