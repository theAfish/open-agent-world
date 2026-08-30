import { beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { WorldCard, WorldEdge } from "../types/world";
import { buildCardDraft } from "./helpers";
import { useWorldStore } from "./worldStore";

function card(id: string, type: WorldCard["type"]): WorldCard {
  return { id, ...buildCardDraft(type, { x: 0, y: 0 }) };
}

describe("authoritative world synchronization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useWorldStore.setState({
      cards: [],
      edges: [],
      stressCards: [],
      loadedChunkKeys: [],
      loadingChunkKeys: [],
      syncState: "online",
      socketState: "closed",
      selectedEdgeId: undefined,
      pendingConnection: undefined,
      events: [],
      toasts: [],
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

  it("applies backend-confirmed permission creation, change, and revocation", async () => {
    const agent = card("agent", "agent");
    const text = card("text", "text");
    const edge: WorldEdge = {
      id: "edge",
      source: agent.id,
      target: text.id,
      relationship: "read",
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
});
