import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeCard,
  normalizeLegionInstantiation,
  normalizeLegionSummary,
  normalizeRuntimeEvent,
  worldApi,
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API normalization boundary", () => {
  it("merges authoritative resource metadata and status into a card", () => {
    const card = normalizeCard({
      id: "image-1",
      type: "image",
      name: "Plate",
      position: { x: 1, y: 2 },
      size: { width: 240, height: 220 },
      expanded: false,
      status: "available",
      config: { filename: "old.png" },
      resource: {
        filename: "plate.png",
        media_type: "image/png",
        size_bytes: 42,
        width: 7,
        height: 5,
        revision: 1,
      },
    });
    expect(card.config.filename).toBe("plate.png");
    expect(card.config.image_width).toBe(7);
    expect(card.config.image_height).toBe(5);
    expect(card.config.bytes).toBe(42);
    expect(card.config.preview_url).toContain("/resources/image-1/content");
  });

  it("normalizes operational events without inventing reasoning fields", () => {
    const event = normalizeRuntimeEvent({
      id: "evt",
      type: "stdout",
      sandbox_id: "lab",
      timestamp: "2026-01-01T00:00:00Z",
      payload: { text: "ok" },
    });
    expect(event.type).toBe("stdout");
    expect(event.sandbox_id).toBe("lab");
    expect(event.payload).toEqual({ text: "ok" });
  });

  it("normalizes Legion summaries and complete instantiated subgraphs", () => {
    expect(normalizeLegionSummary({
      id: "legion-1",
      name: "Research Cell",
      description: "Two agents",
      node_count: 2,
      edge_count: 1,
      bounds: { width: 320, height: 180 },
      node_types: ["vendor.agent"],
      plugin_ids: ["vendor.plugin"],
      compatible: false,
      issues: ["vendor.plugin is missing"],
      revision: 3,
    })).toMatchObject({
      id: "legion-1",
      compatible: false,
      node_types: ["vendor.agent"],
      issues: ["vendor.plugin is missing"],
      revision: 3,
    });

    const instance = normalizeLegionInstantiation({
      legion_id: "legion-1",
      nodes: [
        { id: "new-a", type: "vendor.agent", name: "A", position: { x: 10, y: 20 }, size: { width: 96, height: 96 }, config: {} },
        { id: "new-b", type: "vendor.agent", name: "B", position: { x: 110, y: 20 }, size: { width: 96, height: 96 }, config: {} },
      ],
      edges: [{ id: "new-edge", source: "new-a", target: "new-b", relationship: "vendor.talk", direction: "bidirectional" }],
    });
    expect(instance.nodes.map((node) => node.id)).toEqual(["new-a", "new-b"]);
    expect(instance.edges[0]).toMatchObject({ relationship: "vendor.talk", direction: "bidirectional" });
  });

  it("sends only Legion identity, metadata, and selected node ids", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: "legion-1",
      name: "Research Cell",
      node_count: 2,
      edge_count: 1,
      bounds: { width: 100, height: 100 },
      node_types: ["agent"],
      plugin_ids: ["open-agent-world.core"],
      compatible: true,
      issues: [],
      revision: 1,
    }), {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.createLegion({ name: "Research Cell", description: "Reusable", node_ids: ["a", "b"] });

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/legions");
    expect(JSON.parse(String(options.body))).toEqual({
      name: "Research Cell",
      description: "Reusable",
      node_ids: ["a", "b"],
    });
  });

  it("normalizes the deleted Legion summary returned by the backend", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: "legion-deleted",
      name: "Archived Cell",
      description: "",
      node_count: 2,
      edge_count: 1,
      bounds: { width: 240, height: 96 },
      node_types: ["agent"],
      plugin_ids: ["open-agent-world.core"],
      compatible: true,
      issues: [],
      revision: 1,
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const deleted = await worldApi.deleteLegion("legion-deleted");

    expect(deleted).toMatchObject({ id: "legion-deleted", name: "Archived Cell", node_count: 2 });
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
  });

  it("deletes a deployed formation through one batch request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      {
        id: "node-a",
        type: "agent",
        name: "A",
        position: { x: 0, y: 0 },
        size: { width: 240, height: 160 },
        config: {},
      },
      {
        id: "node-b",
        type: "text",
        name: "B",
        position: { x: 260, y: 0 },
        size: { width: 240, height: 160 },
        config: {},
      },
    ]), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const deleted = await worldApi.deleteNodes(["node-a", "node-b"]);

    expect(deleted.map((node) => node.id)).toEqual(["node-a", "node-b"]);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/nodes/batch-delete");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({ node_ids: ["node-a", "node-b"] });
  });

  it("updates a moved selection through one normalized batch request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { id: "a", type: "agent", name: "A", position: { x: 10, y: 20 }, size: { width: 96, height: 96 }, config: {} },
      { id: "b", type: "text", name: "B", position: { x: 30, y: 40 }, size: { width: 96, height: 96 }, config: {} },
    ]), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const updates = [
      { node_id: "a", patch: { position: { x: 10, y: 20 } } },
      { node_id: "b", patch: { position: { x: 30, y: 40 } } },
    ];

    const cards = await worldApi.batchUpdateNodes(updates);

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/nodes/batch-update");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({ updates });
    expect(cards.map((card) => [card.id, card.position])).toEqual([
      ["a", { x: 10, y: 20 }],
      ["b", { x: 30, y: 40 }],
    ]);
  });

  it("uses the backend's media_type field when importing an image", async () => {
    class TestFileReader {
      result = "data:image/png;base64,cG5n";
      addEventListener(event: string, listener: () => void) {
        if (event === "load") queueMicrotask(listener);
      }
      readAsDataURL() {}
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("FileReader", TestFileReader);
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.uploadImage("image 1", { name: "sample.png", type: "image/png" } as File);

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(options.body))).toMatchObject({
      filename: "sample.png",
      media_type: "image/png",
      data_base64: "cG5n",
    });
    expect(JSON.parse(String(options.body))).not.toHaveProperty("mime_type");
  });

  it("derives an accepted media type from the filename when the browser omits it", async () => {
    class TestFileReader {
      result = "data:application/octet-stream;base64,cG5n";
      addEventListener(event: string, listener: () => void) {
        if (event === "load") queueMicrotask(listener);
      }
      readAsDataURL() {}
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("FileReader", TestFileReader);
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.uploadImage("image-1", { name: "sample.webp", type: "" } as File);

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(options.body))).toMatchObject({ media_type: "image/webp" });
  });

  it("surfaces backend validation messages instead of a generic status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: [{ msg: "Field required" }],
    }), {
      status: 422,
      headers: { "content-type": "application/json" },
    })));

    await expect(worldApi.getWorld()).rejects.toMatchObject({
      message: "Field required",
      status: 422,
    });
  });
});
