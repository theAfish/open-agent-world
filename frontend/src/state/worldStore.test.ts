import { beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { LegionSummary, WorldCard, WorldEdge, WorldSnapshot } from "../types/world";
import { buildCardDraft } from "./helpers";
import { TEST_CATALOG } from "./catalog.fixture";
import { mergeEdges, useWorldStore } from "./worldStore";

function card(id: string, type: WorldCard["type"]): WorldCard {
  return { id, ...buildCardDraft(type, { x: 0, y: 0 }) };
}

function legion(id = "legion-1"): LegionSummary {
  return {
    id,
    name: "Research Cell",
    description: "Reusable formation",
    node_count: 2,
    edge_count: 1,
    bounds: { width: 240, height: 96 },
    node_types: ["agent", "text"],
    plugin_ids: ["open-agent-world.core"],
    compatible: true,
    issues: [],
    revision: 1,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("authoritative world synchronization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(worldApi, "getModelConnections").mockResolvedValue({ revision: 0, connections: [], default_model: null });
    vi.spyOn(worldApi, "getCatalog").mockResolvedValue(TEST_CATALOG);
    vi.spyOn(worldApi, "getLegions").mockResolvedValue([]);
    useWorldStore.setState({
      cards: [],
      catalog: TEST_CATALOG,
      edges: [],
      legions: [],
      legionError: undefined,
      stressCards: [],
      loadedChunkKeys: [],
      loadingChunkKeys: [],
      syncState: "online",
      socketState: "closed",
      eventStream: undefined,
      eventSequence: undefined,
      cardTombstones: {},
      edgeTombstones: {},
      selectedEdgeId: undefined,
      selectedCardIds: [],
      selectionRevision: 0,
      pendingConnection: undefined,
      events: [],
      toasts: [],
      undoStack: [],
      redoStack: [],
      historyBusy: false,
      positionCommitBusy: false,
    });
  });

  it("does not revive a deleted incarnation from a delayed create response", async () => {
    const node = { ...card("new", "text"), revision: 1, created_at: "2026-09-11T00:00:00Z" };
    const response = deferred<WorldCard>();
    const create = vi.spyOn(worldApi, "createNode").mockReturnValue(response.promise);
    const pending = useWorldStore.getState().createCard("text");
    await vi.waitFor(() => expect(create).toHaveBeenCalled());
    const edge: WorldEdge = { id: "new-edge", source: node.id, target: "other", relationship: "read", direction: "forward", revision: 1, created_at: node.created_at };
    useWorldStore.setState({ cards: [node], edges: [edge] });
    useWorldStore.getState().ingestEvent({ id: "deleted", type: "card_deleted", timestamp: node.created_at, payload: { node } });
    response.resolve(node);
    await pending;
    expect(useWorldStore.getState().cards).toEqual([]);
    expect(mergeEdges([], [edge], useWorldStore.getState().edgeTombstones)).toEqual([]);
    useWorldStore.getState().ingestEvent({ id: "restored", type: "card_created", timestamp: node.created_at,
      payload: { node: { ...node, created_at: "2026-09-11T00:01:00Z" } } });
    expect(useWorldStore.getState().cards).toHaveLength(1);
  });

  it("reconciles initialization when a background mutation arrives during loading", async () => {
    const node = { ...card("initializing", "text"), revision: 1 };
    const snapshot = deferred<WorldSnapshot>();
    vi.spyOn(worldApi, "getWorld").mockReturnValueOnce(snapshot.promise).mockResolvedValue({ nodes: [], edges: [], chunks: [] });
    const pending = useWorldStore.getState().initialize();
    useWorldStore.getState().ingestEvent({ id: "deleted", type: "card_deleted", timestamp: "2026-09-11T00:00:00Z", payload: { node } });
    snapshot.resolve({ nodes: [node], edges: [], chunks: [] });
    await pending;
    expect(useWorldStore.getState().cards).toEqual([]);
  });

  it.each([false, true])("preserves a newer background edit during a position commit (failed=%s)", async (failed) => {
    const node = { ...card("moving-background", "text"), revision: 1 };
    const response = deferred<WorldCard[]>();
    const update = vi.spyOn(worldApi, "batchUpdateNodes").mockReturnValue(response.promise);
    useWorldStore.setState({ cards: [node] });
    const position = { x: 80, y: 90 };
    const pending = useWorldStore.getState().updateCardPositions([{ id: node.id, position }]);
    await vi.waitFor(() => expect(update).toHaveBeenCalled());
    expect(update).toHaveBeenCalledWith([{ node_id: node.id, patch: { position } }]);
    useWorldStore.getState().ingestEvent({ id: "newer", type: "card_updated", timestamp: "2026-09-11T00:00:00Z",
      payload: { node: { ...node, position, revision: 3, name: "Background edit" } } });
    if (failed) response.reject(new Error("conflict"));
    else response.resolve([{ ...node, position, revision: 2 }]);
    await pending;
    expect(useWorldStore.getState().cards[0]).toMatchObject({ revision: 3, name: "Background edit" });
  });

  it("keeps a newer background edge change when an older editor response arrives", async () => {
    const edge: WorldEdge = { id: "editing-edge", source: "agent", target: "text", relationship: "read", direction: "forward", revision: 1 };
    const response = deferred<WorldEdge>();
    const update = vi.spyOn(worldApi, "updateEdge").mockReturnValue(response.promise);
    useWorldStore.setState({ edges: [edge], selectedEdgeId: edge.id });
    const pending = useWorldStore.getState().updateSelectedEdge("read_edit");
    await vi.waitFor(() => expect(update).toHaveBeenCalled());
    useWorldStore.getState().ingestEvent({ id: "newer-edge", type: "edge_updated", timestamp: "2026-09-11T00:00:00Z",
      payload: { edge: { ...edge, revision: 3 } } });
    response.resolve({ ...edge, relationship: "read_edit", revision: 2 });
    await pending;
    expect(useWorldStore.getState().edges[0]).toMatchObject({ revision: 3, relationship: "read" });
  });

  it("applies background graph events without changing editor history or using selection", () => {
    const node = { ...card("background", "conversation"), revision: 1, created_at: "2026-09-11T00:00:00Z" };
    const emit = (type: string, payload: Record<string, unknown>, sequence: number) => useWorldStore.getState().ingestEvent({
      id: `event-${sequence}`, type, timestamp: node.created_at, payload, stream_id: "stream", sequence,
    });
    emit("card_created", { node }, 1);
    useWorldStore.setState(state => ({ cards: state.cards.map(card => ({ ...card, config: { ...card.config, output: ["Live output"] } })) }));
    emit("card_updated", { node: { ...node, revision: 3, name: "Changed elsewhere", position: { x: 80, y: 90 } } }, 2);
    emit("card_updated", { node: { ...node, revision: 2, name: "Stale" } }, 3);
    expect(useWorldStore.getState().cards[0]).toMatchObject({ name: "Changed elsewhere", position: { x: 80, y: 90 }, revision: 3 });
    expect(useWorldStore.getState().cards[0].config.output).toEqual(["Live output"]);
    const edge = { id: "background-edge", source: node.id, target: "external", relationship: "read", direction: "forward", revision: 1 };
    emit("edge_created", { edge }, 4);
    emit("edge_updated", { edge: { ...edge, relationship: "read_edit", revision: 2 } }, 5);
    expect(useWorldStore.getState().edges[0]).toMatchObject({ relationship: "read_edit", revision: 2 });
    emit("edge_deleted", { edge: { ...edge, revision: 2 } }, 6);
    expect(useWorldStore.getState().edges).toEqual([]);
    emit("card_deleted", { node: { ...node, revision: 3 } }, 7);
    expect(useWorldStore.getState().cards).toEqual([]);
    expect(useWorldStore.getState().undoStack).toEqual([]);
  });

  it("reconciles missed deletions in requested chunks and their owned descendants", async () => {
    const group = card("deleted-group", "legion");
    const child = { ...card("deleted-child", "text"), parent_id: group.id, position: { x: 10000, y: 0 } };
    const cached = { ...card("cached", "text"), position: { x: 9000, y: 9000 } };
    useWorldStore.setState({ cards: [group, child, cached], activeChunkKeys: ["0:0"], loadedChunkKeys: ["0:0", "4:4"] });
    vi.spyOn(worldApi, "getWorld").mockResolvedValue({ nodes: [], edges: [], chunks: [[0, 0]] });
    await useWorldStore.getState().refreshWorld();
    expect(useWorldStore.getState().cards).toEqual([cached]);
    expect(useWorldStore.getState().loadedChunkKeys).not.toContain("4:4");
  });

  it("does not resurrect a deleted card from a chunk request already in flight", async () => {
    const node = { ...card("deleted-in-flight", "text"), revision: 1 };
    const pending = deferred<WorldSnapshot>();
    vi.spyOn(worldApi, "getWorld").mockReturnValueOnce(pending.promise).mockResolvedValue({ nodes: [], edges: [], chunks: [[0, 0]] });
    useWorldStore.setState({ cards: [node], loadedChunkKeys: [] });
    const loading = useWorldStore.getState().ensureChunks(["0:0"]);
    useWorldStore.getState().ingestEvent({ id: "deleted", type: "card_deleted", node_id: node.id, timestamp: "now", payload: { node } });
    pending.resolve({ nodes: [node], edges: [], chunks: [[0, 0]] });
    await loading;
    expect(useWorldStore.getState().cards).toEqual([]);
  });

  it("resynchronizes on stream gaps including the last event lost before a heartbeat", async () => {
    const refresh = vi.spyOn(useWorldStore.getState(), "refreshWorld").mockResolvedValue();
    const emit = (sequence: number, type = "stdout") => useWorldStore.getState().ingestEvent({
      id: `seq-${sequence}`, type, timestamp: "now", payload: {}, stream_id: "watermark", sequence,
    });
    emit(1);
    emit(3);
    expect(refresh).toHaveBeenCalledTimes(1);
    emit(4, "connection_ready");
    expect(refresh).toHaveBeenCalledTimes(2);
    emit(4, "connection_ready");
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("preserves a newer background edit when an older HTTP update completes", async () => {
    const node = { ...card("concurrent-update", "conversation"), revision: 1 };
    const pending = deferred<WorldCard>();
    const update = vi.spyOn(worldApi, "updateNode").mockReturnValue(pending.promise);
    useWorldStore.setState({ cards: [node] });
    const editing = useWorldStore.getState().updateCard(node.id, { name: "Local" });
    await vi.waitFor(() => expect(update).toHaveBeenCalledWith(node.id, { name: "Local" }));
    useWorldStore.getState().ingestEvent({ id: "external-update", type: "card_updated", timestamp: "now", payload: { node: { ...node, revision: 3, name: "External" } } });
    pending.resolve({ ...node, revision: 2, name: "Local" });
    await editing;
    expect(useWorldStore.getState().cards[0]).toMatchObject({ name: "External", revision: 3 });
  });

  it("copies a selection snapshot with internal links and supports paste undo/redo", async () => {
    const a = card("a", "agent");
    const b = { ...card("b", "agent"), position: { x: 200, y: 100 } };
    const edge: WorldEdge = { id: "ab", source: "a", target: "b", relationship: "related", direction: "forward" };
    useWorldStore.setState({ cards: [a, b], edges: [edge, { ...edge, id: "external", target: "outside" }], selectedCardIds: ["a", "b"], clipboard: undefined });
    vi.spyOn(worldApi, "restoreNode").mockImplementation(async input => input as WorldCard);
    vi.spyOn(worldApi, "createEdge").mockImplementation(async input => input as WorldEdge);
    vi.spyOn(worldApi, "deleteNode").mockResolvedValue(undefined);
    expect(await useWorldStore.getState().copySelection()).toBe(true);
    a.config.changed = true;
    await useWorldStore.getState().pasteSelection();
    const pasted = useWorldStore.getState().cards.slice(2);
    expect(pasted).toHaveLength(2);
    expect(pasted[0].config.changed).toBeUndefined();
    expect(pasted[0].position).toEqual({ x: 48, y: 48 });
    expect(pasted[1].position).toEqual({ x: 248, y: 148 });
    expect(useWorldStore.getState().edges.at(-1)).toMatchObject({ source: pasted[0].id, target: pasted[1].id });
    expect(useWorldStore.getState().selectedCardIds).toEqual(pasted.map(c => c.id));
    await useWorldStore.getState().undo();
    expect(useWorldStore.getState().cards).toHaveLength(2);
    await useWorldStore.getState().redo();
    expect(useWorldStore.getState().cards).toHaveLength(4);
    expect(useWorldStore.getState().edges).toHaveLength(3);
    await useWorldStore.getState().pasteSelection();
    expect(useWorldStore.getState().cards.at(-2)?.position).toEqual({ x: 96, y: 96 });
  });

  it("loads server-created toolbox members and includes missing members when deleting", async () => {
    const definition = { ...TEST_CATALOG.node_types.find((node) => node.id === 'legion')!, id: 'test.toolbox', user_creatable: true, traits: [] };
    const toolbox = { ...card('toolbox', 'test.toolbox') };
    const member = { ...card('skill', 'test.skill'), parent_id: toolbox.id };
    useWorldStore.setState({ catalog: { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types, definition] } });
    vi.spyOn(worldApi, 'createNode').mockResolvedValue(toolbox);
    vi.spyOn(worldApi, 'getWorld').mockResolvedValue({ nodes: [toolbox, member], edges: [], chunks: [] });
    const remove = vi.spyOn(worldApi, 'deleteNodes').mockResolvedValue([toolbox, member]);
    await useWorldStore.getState().createCard(toolbox.type);
    expect(useWorldStore.getState().cards.map((node) => node.id)).toContain(member.id);
    // A stale client can still display only the parent when Delete is pressed.
    useWorldStore.setState({ cards: [toolbox] });
    await useWorldStore.getState().deleteCards([toolbox.id]);
    expect(remove).toHaveBeenCalledWith([toolbox.id, member.id]);
    expect(useWorldStore.getState().cards).toEqual([]);
  });

  it('saves a container resize with members as one batch and restores sizes and positions on undo', async () => {
    const group = { ...card('group', 'legion'), size: { width: 1400, height: 700 } };
    const members = Array.from({ length: 4 }, (_, i) => ({ ...card(`member-${i}`, 'text'), parent_id: group.id, position: { x: 440 + i * 310, y: 160 } }));
    useWorldStore.setState({ cards: [group, ...members] });
    const update = vi.spyOn(worldApi, 'batchUpdateNodes').mockImplementation(async patches => patches.map(p => ({ ...useWorldStore.getState().cards.find(c => c.id === p.node_id)!, ...p.patch })));
    await useWorldStore.getState().resizeContainer(group.id, { width: 800, height: 550 });
    const resized = useWorldStore.getState().cards.map(c => ({ ...c }));
    expect(update).toHaveBeenCalledTimes(1);
    expect(useWorldStore.getState().undoStack).toHaveLength(1);
    expect(resized.find(c => c.id === group.id)!.size.height).toBeGreaterThan(550);
    await useWorldStore.getState().undo();
    expect(useWorldStore.getState().cards).toEqual([group, ...members]);
    await useWorldStore.getState().redo();
    expect(useWorldStore.getState().cards).toEqual(resized);
  });

  it('rolls back the frame and members together when a resize cannot be saved', async () => {
    const group = card('group', 'legion');
    const member = { ...card('member', 'text'), parent_id: group.id, position: { x: 2000, y: 400 } };
    useWorldStore.setState({ cards: [group, member] });
    vi.spyOn(worldApi, 'batchUpdateNodes').mockRejectedValue(new Error('offline'));
    await useWorldStore.getState().resizeContainer(group.id, { width: 800, height: 550 });
    expect(useWorldStore.getState().cards).toEqual([group, member]);
    expect(useWorldStore.getState().undoStack).toHaveLength(0);
  });

  it("forms, undoes and restores a team without recreating its existing members", async () => {
    const first = card("first", "agent");
    const second = card("second", "text");
    const group = { ...card("group", "agent"), type: "legion", size: { width: 1100, height: 700 } };
    const grouped = [first, second].map((c) => ({ ...c, parent_id: group.id }));
    useWorldStore.setState({ cards: [first, second] });
    vi.spyOn(worldApi, "formLegionGroup").mockResolvedValue([group, ...grouped]);
    vi.spyOn(worldApi, "getLegionState").mockResolvedValue({ value: { phase: "review" }, revision: 2 });
    const saveState = vi.spyOn(worldApi, "saveLegionState").mockResolvedValue({ value: { phase: "review" }, revision: 1 });
    const create = vi.spyOn(worldApi, "restoreNode").mockResolvedValue(group);
    const remove = vi.spyOn(worldApi, "deleteNode").mockResolvedValue();
    vi.spyOn(worldApi, "batchUpdateNodes").mockImplementation(async (updates) => updates.map((item) => ({
      ...useWorldStore.getState().cards.find((c) => c.id === item.node_id)!, ...item.patch,
    })));
    await useWorldStore.getState().formLegionGroup([first.id, second.id]);
    expect(useWorldStore.getState().cards.find((c) => c.id === first.id)?.parent_id).toBe(group.id);
    await useWorldStore.getState().undo();
    expect(remove).toHaveBeenCalledWith(group.id);
    expect(useWorldStore.getState().cards.map((c) => c.parent_id)).toEqual([null, null]);
    await useWorldStore.getState().redo();
    expect(create).toHaveBeenCalledTimes(1);
    expect(saveState).toHaveBeenCalledWith(group.id, { phase: "review" }, 0);
    expect(useWorldStore.getState().cards.find((c) => c.id === first.id)?.parent_id).toBe(group.id);
  });

  it("moves every member once and preserves membership when undoing the move", async () => {
    const group = { ...card("group", "agent"), type: "legion" };
    const member = { ...card("member", "agent"), parent_id: group.id, position: { x: 400, y: 200 } };
    useWorldStore.setState({ cards: [group, member] });
    const batch = vi.spyOn(worldApi, "batchUpdateNodes").mockImplementation(async (updates) => updates.map((item) => ({
      ...useWorldStore.getState().cards.find((c) => c.id === item.node_id)!, ...item.patch,
    })));
    await useWorldStore.getState().updateCardPositions([{ id: group.id, position: { x: 100, y: 70 } }]);
    expect(batch.mock.calls[0][0]).toHaveLength(2);
    expect(useWorldStore.getState().cards.find((c) => c.id === member.id)?.position).toEqual({ x: 500, y: 270 });
    await useWorldStore.getState().undo();
    expect(useWorldStore.getState().cards.find((c) => c.id === member.id)).toMatchObject({ parent_id: group.id, position: { x: 400, y: 200 } });
  });

  it("replaces local graph state with the backend snapshot on initialization", async () => {
    const agent = card("agent", "agent");
    const text = card("text", "text");
    const edge: WorldEdge = {
      id: "edge",
      source: agent.id,
      target: text.id,
      relationship: "read",
      direction: "forward",
    };
    useWorldStore.setState({ cards: [card("stale", "sandbox")] });
    vi.spyOn(worldApi, "getWorld").mockResolvedValue({
      nodes: [agent, text],
      edges: [edge],
      chunks: [[0, 0]],
    });

    await useWorldStore.getState().initialize();

    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual(["agent", "text"]);
    expect(useWorldStore.getState().edges).toEqual([edge]);
    expect(useWorldStore.getState().syncState).toBe("online");
  });

  it("keeps the world online when only the Legion library fails to initialize", async () => {
    const agent = card("agent", "agent");
    vi.spyOn(worldApi, "getWorld").mockResolvedValue({ nodes: [agent], edges: [], chunks: [[0, 0]] });
    vi.spyOn(worldApi, "getLegions").mockRejectedValue(new Error("library unavailable"));

    await useWorldStore.getState().initialize();

    expect(useWorldStore.getState()).toMatchObject({
      cards: [agent],
      legions: [],
      syncState: "online",
      syncError: undefined,
      legionError: "library unavailable",
    });
    expect(useWorldStore.getState().toasts.at(-1)).toMatchObject({
      title: "Legion library unavailable",
      detail: expect.stringContaining("canvas remains available"),
    });
  });

  it("refreshes the world while retaining cached Legions when their refresh fails", async () => {
    const cached = legion("cached");
    const agent = card("fresh", "agent");
    useWorldStore.setState({ legions: [cached] });
    vi.spyOn(worldApi, "getWorld").mockResolvedValue({ nodes: [agent], edges: [], chunks: [[0, 0]] });
    vi.spyOn(worldApi, "getLegions").mockRejectedValue(new Error("library refresh unavailable"));

    await useWorldStore.getState().refreshWorld();

    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([agent.id]);
    expect(useWorldStore.getState().legions).toEqual([cached]);
    expect(useWorldStore.getState()).toMatchObject({
      syncState: "online",
      legionError: "library refresh unavailable",
    });
  });

  it("invalidates non-active chunk edges so they are reloaded after a scoped refresh", async () => {
    const activeOne = { ...card("active-one", "agent"), position: { x: 0, y: 0 } };
    const activeTwo = { ...card("active-two", "text"), position: { x: 120, y: 0 } };
    const cachedOne = { ...card("cached-one", "agent"), position: { x: 4096, y: 0 } };
    const cachedTwo = { ...card("cached-two", "text"), position: { x: 4216, y: 0 } };
    const activeEdge: WorldEdge = {
      id: "active-edge",
      source: activeOne.id,
      target: activeTwo.id,
      relationship: "read",
      direction: "forward",
    };
    const cachedEdge: WorldEdge = {
      id: "cached-edge",
      source: cachedOne.id,
      target: cachedTwo.id,
      relationship: "read",
      direction: "forward",
    };
    useWorldStore.setState({
      cards: [activeOne, activeTwo, cachedOne, cachedTwo],
      edges: [activeEdge, cachedEdge],
      activeChunkKeys: ["0:0"],
      loadedChunkKeys: ["0:0", "2:0"],
    });
    const getWorld = vi.spyOn(worldApi, "getWorld")
      .mockResolvedValueOnce({
        nodes: [activeOne, activeTwo],
        edges: [activeEdge],
        chunks: [[0, 0]],
      })
      .mockResolvedValueOnce({
        nodes: [cachedOne, cachedTwo],
        edges: [cachedEdge],
        chunks: [[2, 0]],
      });

    await useWorldStore.getState().refreshWorld();

    expect(useWorldStore.getState().edges).toEqual([activeEdge]);
    expect(useWorldStore.getState().loadedChunkKeys).toEqual(["0:0"]);

    await useWorldStore.getState().ensureChunks(["2:0"]);

    expect(getWorld).toHaveBeenNthCalledWith(1, ["0:0"]);
    expect(getWorld).toHaveBeenNthCalledWith(2, ["2:0"]);
    expect(useWorldStore.getState().edges).toEqual([activeEdge, cachedEdge]);
    expect(useWorldStore.getState().loadedChunkKeys).toEqual(["0:0", "2:0"]);
  });

  it("retries a refresh invalidated by Legion collection and deletion", async () => {
    const first = card("first", "agent");
    const second = card("second", "text");
    const removedLegion = legion("removed-legion");
    const createdLegion = { ...legion("created-legion"), name: "Created Cell" };
    const snapshot: WorldSnapshot = {
      nodes: [first, second],
      edges: [],
      chunks: [[0, 0]],
    };
    const staleWorld = deferred<WorldSnapshot>();
    const staleLibrary = deferred<LegionSummary[]>();
    useWorldStore.setState({
      cards: [first, second],
      legions: [removedLegion],
      activeChunkKeys: ["0:0"],
      loadedChunkKeys: ["0:0"],
    });
    const getWorld = vi.spyOn(worldApi, "getWorld")
      .mockImplementationOnce(() => staleWorld.promise)
      .mockResolvedValueOnce(snapshot);
    const getLegions = vi.spyOn(worldApi, "getLegions")
      .mockImplementationOnce(() => staleLibrary.promise)
      .mockResolvedValueOnce([createdLegion]);
    vi.spyOn(worldApi, "createLegion").mockResolvedValue(createdLegion);
    vi.spyOn(worldApi, "deleteLegion").mockResolvedValue(removedLegion);

    const refreshing = useWorldStore.getState().refreshWorld();
    await vi.waitFor(() => {
      expect(getWorld).toHaveBeenCalledTimes(1);
      expect(getLegions).toHaveBeenCalledTimes(1);
    });
    await useWorldStore.getState().createLegion({
      name: createdLegion.name,
      nodeIds: [first.id, second.id],
    });
    await useWorldStore.getState().deleteLegion(removedLegion.id);

    staleWorld.resolve(snapshot);
    staleLibrary.resolve([removedLegion]);
    await refreshing;

    expect(getWorld).toHaveBeenCalledTimes(2);
    expect(getLegions).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().legions).toEqual([createdLegion]);
  });

  it("retries a refresh invalidated by a Legion deployment", async () => {
    const summary = legion();
    const existing = card("existing", "agent");
    const first = { ...card("deployed-a", "agent"), position: { x: 40, y: 72 } };
    const second = { ...card("deployed-b", "text"), position: { x: 160, y: 72 } };
    const relationship: WorldEdge = {
      id: "deployed-edge",
      source: first.id,
      target: second.id,
      relationship: "read",
      direction: "forward",
    };
    const staleWorld = deferred<WorldSnapshot>();
    useWorldStore.setState({
      cards: [existing],
      legions: [summary],
      activeChunkKeys: ["0:0"],
      loadedChunkKeys: ["0:0"],
    });
    const getWorld = vi.spyOn(worldApi, "getWorld")
      .mockImplementationOnce(() => staleWorld.promise)
      .mockResolvedValueOnce({
        nodes: [existing, first, second],
        edges: [relationship],
        chunks: [[0, 0]],
      });
    vi.spyOn(worldApi, "getLegions").mockResolvedValue([summary]);
    vi.spyOn(worldApi, "instantiateLegion").mockResolvedValue({
      legion_id: summary.id,
      nodes: [first, second],
      edges: [relationship],
    });

    const refreshing = useWorldStore.getState().refreshWorld();
    await vi.waitFor(() => expect(getWorld).toHaveBeenCalledTimes(1));
    await useWorldStore.getState().instantiateLegion(summary.id, { x: 160, y: 120 });
    staleWorld.resolve({ nodes: [existing], edges: [], chunks: [[0, 0]] });
    await refreshing;

    expect(getWorld).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([
      existing.id,
      first.id,
      second.id,
    ]);
    expect(useWorldStore.getState().edges).toEqual([relationship]);
  });

  it("waits for an invalidating position commit before retrying refresh", async () => {
    const original = card("moving", "agent");
    const moved = { ...original, position: { x: 80, y: 40 } };
    const staleWorld = deferred<WorldSnapshot>();
    const positionCommit = deferred<WorldCard[]>();
    useWorldStore.setState({
      cards: [original],
      activeChunkKeys: ["0:0"],
      loadedChunkKeys: ["0:0"],
    });
    const getWorld = vi.spyOn(worldApi, "getWorld")
      .mockImplementationOnce(() => staleWorld.promise)
      .mockResolvedValueOnce({ nodes: [moved], edges: [], chunks: [[0, 0]] });
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes")
      .mockImplementationOnce(() => positionCommit.promise);

    const refreshing = useWorldStore.getState().refreshWorld();
    await vi.waitFor(() => expect(getWorld).toHaveBeenCalledTimes(1));
    const moving = useWorldStore.getState().updateCardPositions([
      { id: original.id, position: moved.position },
    ]);
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));

    staleWorld.resolve({ nodes: [original], edges: [], chunks: [[0, 0]] });
    await Promise.resolve();
    await Promise.resolve();
    expect(getWorld).toHaveBeenCalledTimes(1);

    positionCommit.resolve([moved]);
    await Promise.all([moving, refreshing]);

    expect(getWorld).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().cards[0].position).toEqual(moved.position);
  });

  it("applies backend-confirmed permission creation, change, and revocation", async () => {
    const agent = card("agent", "agent");
    const text = card("text", "text");
    const edge: WorldEdge = {
      id: "edge",
      source: agent.id,
      target: text.id,
      relationship: "read",
      direction: "forward",
    };
    useWorldStore.setState({
      cards: [agent, text],
      pendingConnection: {
        source: agent.id,
        target: text.id,
        options: [],
      },
    });
    vi.spyOn(worldApi, "createEdge").mockResolvedValue(edge);

    await useWorldStore.getState().createConnection("read");
    expect(useWorldStore.getState().edges).toEqual([edge]);

    useWorldStore.getState().selectEdge(edge.id);
    vi.spyOn(worldApi, "updateEdge").mockResolvedValue({
      ...edge,
      relationship: "read_edit",
    });
    await useWorldStore.getState().updateSelectedEdge("read_edit");
    expect(useWorldStore.getState().edges[0].relationship).toBe("read_edit");

    vi.spyOn(worldApi, "deleteEdge").mockResolvedValue(undefined);
    await useWorldStore.getState().deleteSelectedEdge();
    expect(useWorldStore.getState().edges).toEqual([]);
    expect(useWorldStore.getState().selectedEdgeId).toBeUndefined();
  });

  it("stores a reverse drag in the relationship's allowed direction", async () => {
    const agent = card("agent", "agent");
    const sandbox = card("sandbox", "sandbox");
    const edge: WorldEdge = {
      id: "edge",
      source: agent.id,
      target: sandbox.id,
      relationship: "execute",
      direction: "forward",
    };
    useWorldStore.setState({ cards: [agent, sandbox] });

    useWorldStore.getState().requestConnection(sandbox.id, agent.id);

    expect(useWorldStore.getState().pendingConnection).toMatchObject({
      source: agent.id,
      target: sandbox.id,
    });
    expect(
      useWorldStore.getState().pendingConnection?.options.map((option) => option.value),
    ).toEqual(["execute"]);

    const createEdge = vi.spyOn(worldApi, "createEdge").mockResolvedValue(edge);
    await useWorldStore.getState().createConnection("execute");
    expect(createEdge).toHaveBeenCalledWith({
      source: agent.id,
      target: sandbox.id,
      relationship: "execute",
      direction: "forward",
    });
  });

  it("keeps document nodes when their undo snapshot cannot be read", async () => {
    const board = { ...card("board", "text"), type: "example.board" };
    const catalog = useWorldStore.getState().catalog;
    useWorldStore.setState({ cards: [board], catalog: { ...catalog, node_types: [...catalog.node_types,
      { ...catalog.node_types[0], id: board.type, has_document: true }] } });
    vi.spyOn(worldApi, "getNodeDocument").mockRejectedValue(new Error("Document is temporarily unavailable"));
    const remove = vi.spyOn(worldApi, "deleteNode");
    await useWorldStore.getState().deleteCards([board.id]);
    expect(remove).not.toHaveBeenCalled();
    expect(useWorldStore.getState().cards).toEqual([board]);
    expect(useWorldStore.getState().undoStack).toEqual([]);
  });

  it("restores a deleted card and its relationships, then can delete it again", async () => {
    const agent = card("agent", "agent");
    const text = card("text", "text");
    const edge: WorldEdge = {
      id: "edge",
      source: agent.id,
      target: text.id,
      relationship: "read",
      direction: "forward",
    };
    useWorldStore.setState({
      cards: [agent, text],
      edges: [edge],
      selectedCardIds: [text.id],
    });
    const deleteNode = vi.spyOn(worldApi, "deleteNode").mockResolvedValue(undefined);
    const createNode = vi.spyOn(worldApi, "restoreNode").mockResolvedValue(text);
    const createEdge = vi.spyOn(worldApi, "createEdge").mockResolvedValue(edge);
    vi.spyOn(worldApi, "getTextContent").mockResolvedValue("remembered text");

    await useWorldStore.getState().deleteCards([text.id]);
    expect(useWorldStore.getState().cards).toEqual([agent]);
    expect(useWorldStore.getState().edges).toEqual([]);
    expect(useWorldStore.getState().undoStack.at(-1)?.kind).toBe("cards-deleted");

    await useWorldStore.getState().undo();
    expect(createNode).toHaveBeenCalledWith(expect.objectContaining({
      id: text.id,
      content: "remembered text",
    }));
    expect(createEdge).toHaveBeenCalledWith(edge);
    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([agent.id, text.id]);
    expect(useWorldStore.getState().edges).toEqual([edge]);

    await useWorldStore.getState().redo();
    expect(deleteNode).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().cards).toEqual([agent]);
    expect(useWorldStore.getState().edges).toEqual([]);
  });

  it("deletes an Agent and its equipment atomically and restores the whole ownership tree", async () => {
    const agent = card("owner", "agent");
    const sandbox = { ...card("private", "sandbox"), equipment: { owner_id: agent.id, relationship: "execute" } };
    const shared = card("shared", "sandbox");
    useWorldStore.setState({ cards: [agent, sandbox, shared] });
    const batch = vi.spyOn(worldApi, "deleteNodes").mockResolvedValue([agent, sandbox]);
    const single = vi.spyOn(worldApi, "deleteNode").mockRejectedValue(new Error("already deleted"));
    vi.spyOn(worldApi, "restoreNode").mockResolvedValueOnce(agent).mockResolvedValueOnce(sandbox);
    await useWorldStore.getState().deleteCard(agent.id);
    expect(batch).toHaveBeenCalledWith([agent.id, sandbox.id]);
    expect(single).not.toHaveBeenCalled();
    expect(useWorldStore.getState().cards).toEqual([shared]);
    await useWorldStore.getState().undo();
    expect(useWorldStore.getState().cards).toContainEqual(sandbox);
    await useWorldStore.getState().redo();
    expect(batch).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().cards).toEqual([shared]);
  });

  it("returns a completed Sandbox command to ready state without a live socket", async () => {
    const sandbox = { ...card("sandbox", "sandbox"), status: "ready" as const };
    useWorldStore.setState({ cards: [sandbox], socketState: "closed" });
    vi.spyOn(worldApi, "executeSandbox").mockResolvedValue({
      exit_code: 0,
      stdout: "contained output\r\n",
      stderr: "",
    });

    await useWorldStore.getState().executeSandbox(sandbox.id, "echo contained output");

    const updated = useWorldStore.getState().cards[0];
    expect(updated.status).toBe("ready");
    expect(updated.config.active_command).toBe("");
    expect(updated.config.output).toContain("contained output");
  });

  it("refuses to directly create a managed node type", async () => {
    const managed = {
      ...TEST_CATALOG.node_types[0],
      id: "legion",
      label: "Legion",
      user_creatable: false,
    };
    useWorldStore.setState({
      catalog: { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types, managed] },
    });
    const createNode = vi.spyOn(worldApi, "createNode");

    expect(await useWorldStore.getState().createCard("legion")).toBeUndefined();
    expect(createNode).not.toHaveBeenCalled();
    expect(useWorldStore.getState().toasts.at(-1)).toMatchObject({
      title: "Use the dedicated creation action",
    });
  });

  it("merges a Legion instance as one undoable topology operation", async () => {
    const existing = card("existing", "agent");
    const first = { ...card("instance-a", "agent"), position: { x: 100, y: 100 } };
    const second = { ...card("instance-b", "text"), position: { x: 220, y: 100 } };
    const relationship: WorldEdge = {
      id: "instance-edge",
      source: first.id,
      target: second.id,
      relationship: "read",
      direction: "forward",
    };
    useWorldStore.setState({ cards: [existing], legions: [legion()] });
    const instantiateLegion = vi.spyOn(worldApi, "instantiateLegion").mockResolvedValue({
      legion_id: "legion-1",
      nodes: [first, second],
      edges: [relationship],
    });
    const deleteNodes = vi.spyOn(worldApi, "deleteNodes").mockResolvedValue([first, second]);

    await useWorldStore.getState().instantiateLegion("legion-1", { x: 160, y: 120 });

    expect(instantiateLegion).toHaveBeenCalledWith("legion-1", { x: 40, y: 72 });
    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([existing.id, first.id, second.id]);
    expect(useWorldStore.getState().edges).toEqual([relationship]);
    expect(useWorldStore.getState().selectedCardIds).toEqual([first.id, second.id]);
    expect(useWorldStore.getState().undoStack.at(-1)).toMatchObject({ kind: "legion-instantiated" });

    await useWorldStore.getState().undo();

    expect(deleteNodes).toHaveBeenCalledWith([first.id, second.id]);
    expect(useWorldStore.getState().cards).toEqual([existing]);
    expect(useWorldStore.getState().edges).toEqual([]);
  });

  it("removes stale Legion deployment operations from both history stacks when deleting its card", async () => {
    const summary = legion();
    const deployed = card("deployed", "agent");
    const operation = {
      id: 101,
      label: "Deploy Research Cell",
      kind: "legion-instantiated" as const,
      legionId: summary.id,
      position: { x: 40, y: 72 },
      cards: [deployed],
      edges: [],
    };
    const unrelated = {
      id: 102,
      label: "Create existing",
      kind: "card-created" as const,
      cards: [card("existing", "agent")],
    };
    useWorldStore.setState({
      legions: [summary],
      undoStack: [operation, unrelated],
      redoStack: [operation],
    });
    vi.spyOn(worldApi, "deleteLegion").mockResolvedValue(summary);

    await useWorldStore.getState().deleteLegion(summary.id);

    expect(useWorldStore.getState().legions).toEqual([]);
    expect(useWorldStore.getState().undoStack).toEqual([unrelated]);
    expect(useWorldStore.getState().redoStack).toEqual([]);
  });

  it("serializes deployment and deletion of the same Legion", async () => {
    const summary = legion();
    const deployed = card("deployed-after-wait", "agent");
    useWorldStore.setState({ legions: [summary] });
    let resolveDeployment: ((value: {
      legion_id: string;
      nodes: WorldCard[];
      edges: WorldEdge[];
    }) => void) | undefined;
    const instantiate = vi.spyOn(worldApi, "instantiateLegion").mockImplementation(() => (
      new Promise((resolve) => { resolveDeployment = resolve; })
    ));
    const remove = vi.spyOn(worldApi, "deleteLegion").mockResolvedValue(summary);

    const deploying = useWorldStore.getState().instantiateLegion(summary.id, { x: 160, y: 120 });
    await vi.waitFor(() => expect(instantiate).toHaveBeenCalledTimes(1));
    const deleting = useWorldStore.getState().deleteLegion(summary.id);
    expect(remove).not.toHaveBeenCalled();

    resolveDeployment?.({ legion_id: summary.id, nodes: [deployed], edges: [] });
    await deploying;
    await deleting;

    expect(remove).toHaveBeenCalledTimes(1);
    expect(useWorldStore.getState().legions).toEqual([]);
    expect(useWorldStore.getState().undoStack).toEqual([]);
    expect(useWorldStore.getState().redoStack).toEqual([]);
  });

  it("does not deadlock when a position update is queued immediately after deployment", async () => {
    const summary = legion();
    const existing = card("existing", "agent");
    const moved = { ...existing, position: { x: 80, y: 40 } };
    const deployed = card("deployed", "text");
    const deployment = deferred<{
      legion_id: string;
      nodes: WorldCard[];
      edges: WorldEdge[];
    }>();
    useWorldStore.setState({ cards: [existing], legions: [summary] });
    const instantiate = vi.spyOn(worldApi, "instantiateLegion")
      .mockImplementationOnce(() => deployment.promise);
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes")
      .mockResolvedValueOnce([moved]);

    const deploying = useWorldStore.getState().instantiateLegion(summary.id, { x: 160, y: 120 });
    const moving = useWorldStore.getState().updateCardPositions([
      { id: existing.id, position: moved.position },
    ]);
    await vi.waitFor(() => expect(instantiate).toHaveBeenCalledTimes(1));
    expect(batchUpdateNodes).not.toHaveBeenCalled();

    deployment.resolve({ legion_id: summary.id, nodes: [deployed], edges: [] });
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));
    await Promise.all([deploying, moving]);

    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([
      existing.id,
      deployed.id,
    ]);
    expect(useWorldStore.getState().cards.find((item) => item.id === existing.id)?.position)
      .toEqual(moved.position);
  });

  it("serializes Legion undo with deletion and removes the resulting redo entry", async () => {
    const summary = legion();
    const deployed = card("deployed-before-undo", "agent");
    const operation = {
      id: 103,
      label: "Deploy Research Cell",
      kind: "legion-instantiated" as const,
      legionId: summary.id,
      position: { x: 40, y: 72 },
      cards: [deployed],
      edges: [],
    };
    useWorldStore.setState({
      cards: [deployed],
      legions: [summary],
      undoStack: [operation],
    });
    let resolveUndo: ((cards: WorldCard[]) => void) | undefined;
    const deleteNodes = vi.spyOn(worldApi, "deleteNodes").mockImplementation(() => (
      new Promise((resolve) => { resolveUndo = resolve; })
    ));
    const remove = vi.spyOn(worldApi, "deleteLegion").mockResolvedValue(summary);

    const undoing = useWorldStore.getState().undo();
    await vi.waitFor(() => expect(deleteNodes).toHaveBeenCalledTimes(1));
    const deleting = useWorldStore.getState().deleteLegion(summary.id);
    expect(remove).not.toHaveBeenCalled();

    resolveUndo?.([deployed]);
    await undoing;
    await deleting;

    expect(remove).toHaveBeenCalledTimes(1);
    expect(useWorldStore.getState().legions).toEqual([]);
    expect(useWorldStore.getState().undoStack).toEqual([]);
    expect(useWorldStore.getState().redoStack).toEqual([]);
  });

  it("does not replay a stale Legion redo after deletion wins the operation queue", async () => {
    const summary = legion();
    const deployed = card("stale-redo-node", "agent");
    const operation = {
      id: 104,
      label: "Deploy Research Cell",
      kind: "legion-instantiated" as const,
      legionId: summary.id,
      position: { x: 40, y: 72 },
      cards: [deployed],
      edges: [],
    };
    useWorldStore.setState({
      legions: [summary],
      redoStack: [operation],
    });
    let resolveDeletion: ((summary: LegionSummary) => void) | undefined;
    const remove = vi.spyOn(worldApi, "deleteLegion").mockImplementation(() => (
      new Promise((resolve) => { resolveDeletion = resolve; })
    ));
    const instantiate = vi.spyOn(worldApi, "instantiateLegion");

    const deleting = useWorldStore.getState().deleteLegion(summary.id);
    await vi.waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    const redoing = useWorldStore.getState().redo();
    expect(instantiate).not.toHaveBeenCalled();

    resolveDeletion?.(summary);
    await deleting;
    await redoing;

    expect(instantiate).not.toHaveBeenCalled();
    expect(useWorldStore.getState().legions).toEqual([]);
    expect(useWorldStore.getState().undoStack).toEqual([]);
    expect(useWorldStore.getState().redoStack).toEqual([]);
  });

  it("blocks collection when an internal relationship opts out of Legion templates", async () => {
    const first = card("first", "agent");
    const second = card("second", "agent");
    const relationship: WorldEdge = {
      id: "blocked-edge",
      source: first.id,
      target: second.id,
      relationship: "communicate",
      direction: "bidirectional",
    };
    const catalog = {
      ...TEST_CATALOG,
      relationships: TEST_CATALOG.relationships.map((item) => (
        item.id === relationship.relationship ? { ...item, templateable: false } : item
      )),
    };
    useWorldStore.setState({ cards: [first, second], edges: [relationship], catalog });
    const createLegion = vi.spyOn(worldApi, "createLegion");

    const created = await useWorldStore.getState().createLegion({
      name: "Blocked Cell",
      nodeIds: [first.id, second.id],
    });

    expect(created).toBeUndefined();
    expect(createLegion).not.toHaveBeenCalled();
    expect(useWorldStore.getState().toasts.at(-1)).toMatchObject({
      title: "A plugin blocked this Legion",
      detail: expect.stringContaining("Relationships: communicate (open-agent-world.core)"),
    });
  });

  it("waits for every dragged card position before collecting a Legion", async () => {
    const first = card("first", "agent");
    const second = card("second", "text");
    useWorldStore.setState({ cards: [first, second] });
    let resolveBatch: ((cards: WorldCard[]) => void) | undefined;
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes").mockImplementation(() => (
      new Promise((resolve) => { resolveBatch = resolve; })
    ));
    const createLegion = vi.spyOn(worldApi, "createLegion").mockResolvedValue(legion());

    const moving = useWorldStore.getState().updateCardPositions([
      { id: first.id, position: { x: 80, y: 40 } },
      { id: second.id, position: { x: 200, y: 40 } },
    ]);
    const collecting = useWorldStore.getState().createLegion({
      name: "Research Cell",
      nodeIds: [first.id, second.id],
    });
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));
    expect(createLegion).not.toHaveBeenCalled();
    expect(batchUpdateNodes).toHaveBeenCalledWith([
      { node_id: first.id, patch: { position: { x: 80, y: 40 } } },
      { node_id: second.id, patch: { position: { x: 200, y: 40 } } },
    ]);

    resolveBatch?.([
      { ...first, position: { x: 80, y: 40 } },
      { ...second, position: { x: 200, y: 40 } },
    ]);
    await moving;
    await collecting;

    expect(createLegion).toHaveBeenCalledWith({
      name: "Research Cell",
      node_ids: [first.id, second.id],
    });
    expect(useWorldStore.getState().cards.map((item) => item.position)).toEqual([
      { x: 80, y: 40 },
      { x: 200, y: 40 },
    ]);
    expect(useWorldStore.getState().positionCommitBusy).toBe(false);
  });

  it("moves, undoes, and redoes a multi-card position change with atomic batches", async () => {
    const first = card("first", "agent");
    const second = card("second", "text");
    useWorldStore.setState({ cards: [first, second] });
    const updateNode = vi.spyOn(worldApi, "updateNode");
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes").mockImplementation(async (updates) => (
      updates.map((update) => {
        const original = update.node_id === first.id ? first : second;
        return { ...original, position: update.patch.position ?? original.position };
      })
    ));

    await useWorldStore.getState().updateCardPositions([
      { id: first.id, position: { x: 80, y: 40 } },
      { id: second.id, position: { x: 200, y: 40 } },
    ]);
    await useWorldStore.getState().undo();
    await useWorldStore.getState().redo();

    expect(updateNode).not.toHaveBeenCalled();
    expect(batchUpdateNodes.mock.calls.map(([updates]) => updates)).toEqual([
      [
        { node_id: first.id, patch: { position: { x: 80, y: 40 } } },
        { node_id: second.id, patch: { position: { x: 200, y: 40 } } },
      ],
      [
        { node_id: first.id, patch: { position: { x: 0, y: 0 } } },
        { node_id: second.id, patch: { position: { x: 0, y: 0 } } },
      ],
      [
        { node_id: first.id, patch: { position: { x: 80, y: 40 } } },
        { node_id: second.id, patch: { position: { x: 200, y: 40 } } },
      ],
    ]);
    expect(useWorldStore.getState().cards.map((item) => item.position)).toEqual([
      { x: 80, y: 40 },
      { x: 200, y: 40 },
    ]);
  });

  it("waits for a pending position commit before choosing the undo operation", async () => {
    const first = card("first", "agent");
    const moved = { ...first, position: { x: 80, y: 40 } };
    const positionCommit = deferred<WorldCard[]>();
    useWorldStore.setState({ cards: [first] });
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes")
      .mockImplementationOnce(() => positionCommit.promise)
      .mockImplementationOnce(async (updates) => [{
        ...first,
        position: { ...updates[0].patch.position! },
      }]);

    const moving = useWorldStore.getState().updateCardPositions([
      { id: first.id, position: moved.position },
    ]);
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));
    const undoing = useWorldStore.getState().undo();
    await Promise.resolve();
    expect(batchUpdateNodes).toHaveBeenCalledTimes(1);

    positionCommit.resolve([moved]);
    await Promise.all([moving, undoing]);

    expect(batchUpdateNodes).toHaveBeenCalledTimes(2);
    expect(batchUpdateNodes).toHaveBeenLastCalledWith([
      { node_id: first.id, patch: { position: { x: 0, y: 0 } } },
    ]);
    expect(useWorldStore.getState().cards[0].position).toEqual({ x: 0, y: 0 });
    expect(useWorldStore.getState().undoStack).toEqual([]);
    expect(useWorldStore.getState().redoStack.at(-1)).toMatchObject({ kind: "cards-updated" });
  });

  it("waits for a pending position commit before re-reading the redo stack", async () => {
    const first = card("first", "agent");
    const moved = { ...first, position: { x: 80, y: 40 } };
    const staleRedo = card("stale-redo", "text");
    const positionCommit = deferred<WorldCard[]>();
    useWorldStore.setState({
      cards: [first],
      redoStack: [{
        id: 901,
        label: `Place ${staleRedo.name}`,
        kind: "card-created",
        cards: [staleRedo],
      }],
    });
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes")
      .mockImplementationOnce(() => positionCommit.promise);
    const createNode = vi.spyOn(worldApi, "createNode").mockResolvedValue(staleRedo);

    const moving = useWorldStore.getState().updateCardPositions([
      { id: first.id, position: moved.position },
    ]);
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));
    const redoing = useWorldStore.getState().redo();
    await Promise.resolve();
    expect(createNode).not.toHaveBeenCalled();

    positionCommit.resolve([moved]);
    await Promise.all([moving, redoing]);

    expect(createNode).not.toHaveBeenCalled();
    expect(useWorldStore.getState().redoStack).toEqual([]);
    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([first.id]);
  });

  it("does not let an async history producer replace the operation being undone", async () => {
    const first = card("first", "agent");
    const second = card("second", "text");
    const created = card("created", "text");
    const relationship: WorldEdge = {
      id: "existing-edge",
      source: first.id,
      target: second.id,
      relationship: "read",
      direction: "forward",
    };
    const operation = {
      id: 902,
      label: "Create relationship",
      kind: "edge-created" as const,
      edge: relationship,
    };
    const undoDelete = deferred<void>();
    useWorldStore.setState({
      cards: [first, second],
      edges: [relationship],
      undoStack: [operation],
    });
    const deleteEdge = vi.spyOn(worldApi, "deleteEdge").mockImplementationOnce(() => undoDelete.promise);
    const createNode = vi.spyOn(worldApi, "createNode").mockResolvedValue(created);

    const undoing = useWorldStore.getState().undo();
    await vi.waitFor(() => expect(deleteEdge).toHaveBeenCalledTimes(1));
    const creating = useWorldStore.getState().createCard("text", { x: 240, y: 80 });
    await Promise.resolve();
    expect(createNode).not.toHaveBeenCalled();

    undoDelete.resolve(undefined);
    await Promise.all([undoing, creating]);

    expect(createNode).toHaveBeenCalledTimes(1);
    expect(useWorldStore.getState().undoStack).toHaveLength(1);
    expect(useWorldStore.getState().undoStack[0]).toMatchObject({
      kind: "card-created",
      cards: [{ id: created.id }],
    });
    expect(useWorldStore.getState().redoStack).toEqual([]);
    expect(useWorldStore.getState().edges).toEqual([]);
    expect(useWorldStore.getState().cards.map((item) => item.id)).toEqual([
      first.id,
      second.id,
      created.id,
    ]);
  });

  it("uses the persisted origin when a failed drag is followed by a successful drag and undo", async () => {
    const original = card("moving", "agent");
    const firstTarget = { x: 80, y: 40 };
    const secondTarget = { x: 180, y: 90 };
    const firstCommit = deferred<WorldCard[]>();
    useWorldStore.setState({ cards: [original] });
    const batchUpdateNodes = vi.spyOn(worldApi, "batchUpdateNodes")
      .mockImplementationOnce(() => firstCommit.promise)
      .mockImplementationOnce(async () => [{ ...original, position: secondTarget }])
      .mockImplementationOnce(async () => [original]);

    const firstMove = useWorldStore.getState().updateCardPositions([
      { id: original.id, position: firstTarget },
    ]);
    await vi.waitFor(() => expect(batchUpdateNodes).toHaveBeenCalledTimes(1));
    const secondMove = useWorldStore.getState().updateCardPositions([
      { id: original.id, position: secondTarget },
    ]);
    await Promise.resolve();
    expect(batchUpdateNodes).toHaveBeenCalledTimes(1);

    firstCommit.reject(new Error("first drag failed"));
    await Promise.all([firstMove, secondMove]);

    expect(batchUpdateNodes).toHaveBeenCalledTimes(2);
    expect(useWorldStore.getState().cards[0].position).toEqual(secondTarget);
    expect(useWorldStore.getState().undoStack).toHaveLength(1);
    expect(useWorldStore.getState().undoStack[0]).toMatchObject({
      kind: "cards-updated",
      before: [{ id: original.id, position: original.position }],
      after: [{ id: original.id, position: secondTarget }],
    });

    await useWorldStore.getState().undo();

    expect(batchUpdateNodes).toHaveBeenCalledTimes(3);
    expect(useWorldStore.getState().cards[0].position).toEqual(original.position);
  });
});
