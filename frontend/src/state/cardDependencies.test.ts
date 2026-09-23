// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { collectionDependencies, ensureCardsCollected } from "./cardDependencies";
import { useCardLibrary, type LibrarySnapshot } from "./cardLibrary";
import { worldApi } from "../api/client";
import { useWorldStore } from "./worldStore";
import { TEST_CATALOG } from "./catalog.fixture";

let state: LibrarySnapshot;
beforeEach(() => {
  state = { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, card_definitions: {},
    collection: {}, decks: [], active_deck_id: "", available_card_ids: [], available_pack_ids: ["tools"],
    packs: { tools: { owned: true, opened: false, opened_at: null,
      definition: { id: "tools", plugin_id: "test", name: "Tools", description: "", cards: ["agent", "text"] } } } };
  useCardLibrary.setState({ snapshot: state, busy: false, error: "", open: false });
});
afterEach(() => { vi.restoreAllMocks(); useCardLibrary.setState({ snapshot: null, busy: false, open: false }); });

it("deduplicates shared packs and reports unavailable sources without guessing from issue text", () => {
  expect(collectionDependencies(state, ["agent", "text", "agent"]).packs).toHaveLength(1);
  state.available_pack_ids = [];
  expect(collectionDependencies(state, ["agent"]).blocked).toEqual(["agent"]);
});

it("finds an uncollected member of a collected container and terminates dependency cycles", () => {
  const definition = TEST_CATALOG.node_types.find(card => card.id === "legion")!;
  state.card_definitions.parent = { ...definition, id: "parent", container: { ...definition.container!, member_type: "agent" } };
  state.card_definitions.agent = { ...definition, id: "agent", container: { ...definition.container!, member_type: "parent" } };
  state.collection.parent = { card_id: "parent", plugin_id: "test", source_pack_ids: [], unlocked: true, unlocked_at: "" };
  const plan = collectionDependencies(state, ["parent"]);
  expect(plan.missing).toEqual(["agent"]);
  expect(plan.packs.map(pack => pack.definition.id)).toEqual(["tools"]);
});

it("stops after an opening conflict and exposes the error without claiming success", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(worldApi, "editCardLibrary").mockRejectedValue(new Error("Library changed"));
  vi.spyOn(worldApi, "getCardLibrary").mockResolvedValue(state);
  vi.spyOn(worldApi, "getCatalog").mockResolvedValue(TEST_CATALOG);
  expect(await ensureCardsCollected(["agent"])).toBe(false);
  expect(useCardLibrary.getState().error).toContain("Library changed");
  expect(useCardLibrary.getState().open).toBe(true);
  expect(useCardLibrary.getState().snapshot?.collection.agent).toBeUndefined();
});

it("cancellation opens nothing and unavailable dependencies never request consent", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const edit = vi.spyOn(worldApi, "editCardLibrary");
  expect(await ensureCardsCollected(["agent"])).toBe(false);
  expect(edit).not.toHaveBeenCalled();
  expect(await ensureCardsCollected(["missing"])).toBe(false);
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(useCardLibrary.getState().error).toContain("missing");
});

it("opens a shared pack once after consent and resumes the original Legion placement", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const saved = structuredClone(state);
  saved.revision = 2;
  saved.packs.tools.opened = true;
  for (const id of ["agent", "text"]) saved.collection[id] = {
    card_id: id, plugin_id: "test", unlocked: true, source_pack_ids: ["tools"], unlocked_at: "",
  };
  const edit = vi.spyOn(worldApi, "editCardLibrary").mockResolvedValue(saved);
  const deploy = vi.spyOn(worldApi, "instantiateLegion").mockResolvedValue({ legion_id: "research", nodes: [], edges: [] });
  useWorldStore.setState({ syncState: "online", catalog: TEST_CATALOG, cards: [], edges: [], legions: [{
    id: "research", name: "Research", node_count: 2, edge_count: 0, node_types: ["agent"],
    required_card_ids: ["agent", "text"], plugin_ids: ["test"], compatible: true, issues: [],
    bounds: { width: 200, height: 100 }, revision: 1,
  }] });
  await useWorldStore.getState().instantiateLegion("research", { x: 300, y: 200 });
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(confirm.mock.calls[0][0]).toContain("text");
  expect(edit).toHaveBeenCalledTimes(1);
  expect(edit).toHaveBeenCalledWith({ action: "open_pack", id: "tools", expected_revision: 1 });
  expect(deploy).toHaveBeenCalledWith("research", { x: 200, y: 150 }, { preset: undefined, unwrap: undefined });
  expect(await ensureCardsCollected(["agent", "text"])).toBe(true);
  expect(confirm).toHaveBeenCalledTimes(1);
});

it("does not deploy when consent is declined", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  const deploy = vi.spyOn(worldApi, "instantiateLegion");
  useWorldStore.setState({ syncState: "online", legions: [{ id: "cancel", name: "Cancel", node_count: 1,
    edge_count: 0, node_types: ["agent"], plugin_ids: [], compatible: true, issues: [],
    bounds: { width: 100, height: 100 }, revision: 1 }] });
  expect(await useWorldStore.getState().instantiateLegion("cancel")).toBeUndefined();
  expect(deploy).not.toHaveBeenCalled();
});
