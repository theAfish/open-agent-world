import { beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { LegionSummary, WorldCard, WorldEdge, WorldSnapshot } from "../types/world";
import { buildCardDraft } from "./helpers";
import { TEST_CATALOG } from "./catalog.fixture";
import { useWorldStore } from "./worldStore";

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
    const createNode = vi.spyOn(worldApi, "createNode").mockResolvedValue(text);
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
