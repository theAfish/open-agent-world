import { afterEach, describe, expect, it, vi } from "vitest";
import * as hostedAuth from "../auth/hostedSession";
import { useWorldStore } from "../state/worldStore";
import { useConversationView } from "../state/conversationView";
import { TEST_CATALOG } from "../state/catalog.fixture";
import {
  configureWorkspaceApi,
  normalizeCard,
  normalizeEdge,
  normalizeLegionInstantiation,
  normalizeLegionSummary,
  normalizeRuntimeEvent,
  normalizeWorldSnapshot,
  worldApi,
} from "./client";

afterEach(() => {
  configureWorkspaceApi(false);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("API normalization boundary", () => {
  it.each([undefined, false, true])('returns the Agent admission receipt with freshContext=%s', async freshContext => {
    const receipt = { accepted: true, agent_id: 'agent one', run_id: 'run-1' };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(receipt), {
      status: 202, headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await worldApi.runAgent('agent one', 'Analyze the assigned articles', freshContext)).toEqual(receipt);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/agents/agent%20one/run');
    expect(init.method).toBe('POST');
    expect(init.headers.get('X-OAW-State-Session')).toBe('default');
    expect(JSON.parse(init.body)).toEqual({ prompt: 'Analyze the assigned articles',
      ...(freshContext ? { fresh_context: true } : {}) });
  });

  it('rejects a refused Agent launch without retrying or replacing its diagnostics', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: {
      code: 'conflict', message: 'Agent is busy', request_id: 'launch-refused', retryable: false,
    } }), { status: 409, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(worldApi.runAgent('agent', 'Analyze')).rejects.toMatchObject({
      status: 409, message: 'Agent is busy', requestId: 'launch-refused',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('uploads resource files as multipart and follows the host file policy', async () => {
    const fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/files/policy')
      ? { max_file_bytes: 10, chunk_bytes: 4 } : { saved: true }), { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['data'], 'input.xlsx');
    await worldApi.nodeResourceFileAction('knowledge', 'ingest', { group_id: 'group' }, { file });
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Headers).get('Content-Type')).toBeNull();
    const body = init.body as FormData;
    expect(JSON.parse(String(body.get('request')))).toEqual({ arguments: { group_id: 'group' }, confirm: false });
    expect(body.get('file')).toBeInstanceOf(File);
    await expect(worldApi.nodeResourceFileAction('knowledge', 'ingest', {}, { file: new File(['oversized data'], 'large.xlsx') })).rejects.toThrow('Upload exceeds');
    expect(fetchMock).toHaveBeenCalledTimes(3); // second attempt read policy, never sent its file
  });
  it('sends sandbox deletion and move paths through explicit mutation endpoints', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    await worldApi.deleteSandboxFile('sandbox one', 'folder/file.txt');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/sandboxes/sandbox%20one/files?path=folder%2Ffile.txt');
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
    await worldApi.moveSandboxFile('sandbox one', 'folder/file.txt', 'other/file.txt');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sandboxes/sandbox%20one/files/move');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ path: 'folder/file.txt', destination: 'other/file.txt' });
  });
  it('sends raw sandbox bytes, relative paths and explicit directory intent through the real client', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ written: 3 }), { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['abc'], 'data.txt');
    await worldApi.uploadSandboxEntry('sandbox one', 'folder/data.txt', file);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/sandboxes/sandbox%20one/files?path=folder%2Fdata.txt&directory=false&state_session=default');
    expect(init.body).toBe(file);
    expect(init.headers.get('Content-Type')).toBe('application/octet-stream');
    await worldApi.uploadSandboxEntry('sandbox one', 'folder/empty');
    expect(fetchMock.mock.calls[1][0]).toContain('directory=true');
    expect(fetchMock.mock.calls[1][1].body).toBe('');
  });
  it('carries the selected native session on file mutations and direct Agent execution', async () => {
    const world = useWorldStore.getState();
    const view = useConversationView.getState();
    const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    useWorldStore.setState({ cards: ['box', 'agent', 'room'].map(id => normalizeCard({ id, type: id === 'room' ? 'conversation' : id === 'box' ? 'sandbox' : 'agent' })),
      catalog: TEST_CATALOG, edges: [
        normalizeEdge({ id: 'execute', source: 'agent', target: 'box', relationship: 'execute' }),
        normalizeEdge({ id: 'join', source: 'agent', target: 'room', relationship: 'participate' }),
      ] });
    useConversationView.setState({ activeConversationId: 'room', sessions: { room: 'selected-session' } });
    try {
      await worldApi.deleteSandboxFile('box', 'Session/file.txt');
      await worldApi.moveSandboxFile('box', 'Session/file.txt', 'Session/moved.txt');
      await worldApi.runAgent('agent', 'Continue');
      for (const [, init] of fetchMock.mock.calls) {
        expect(init.headers.get('X-OAW-State-Session')).toBe('selected-session');
      }
      await worldApi.uploadSandboxEntry('box', 'Session/input.txt', new File(['data'], 'input.txt'));
      expect(fetchMock.mock.calls[3][0]).toContain('state_session=selected-session');
      useWorldStore.setState({ edges: [] });
      await worldApi.deleteSandboxFile('box', 'Default session/file.txt');
      expect(fetchMock.mock.calls[4][1].headers.get('X-OAW-State-Session')).toBe('default');
    } finally {
      useWorldStore.setState({ cards: world.cards, catalog: world.catalog, edges: world.edges });
      useConversationView.setState({ activeConversationId: view.activeConversationId, sessions: view.sessions });
    }
  });
  it("sends global environment variables when saving Sandbox settings", async () => {
    const settings = { workspace_root: null, runtime: "auto", environment_variables: { OAW_MINERU_TOKEN: "test-global-token" } };
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(settings), {
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await worldApi.saveSandboxSettings(settings)).toEqual(settings);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/sandbox");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(settings);
    expect((await worldApi.getSandboxSettings()).environment_variables).toEqual(settings.environment_variables);
  });
  it('retains missing implementation diagnostics and opaque config through snapshots and events', () => {
    const missing_plugin = { plugin_id: 'removed.pack', reason: 'owner_mismatch' };
    const card = { id: 'lost', type: 'text', config: { nested: { items: [1, 2] } }, missing_plugin };
    const edge = { id: 'lost-edge', source: 'agent', target: 'lost', relationship: 'removed.read', missing_plugin };
    expect(normalizeCard(card).missing_plugin).toEqual(missing_plugin);
    expect(normalizeCard(card).config).toEqual(card.config);
    expect(normalizeEdge(edge).missing_plugin).toEqual(missing_plugin);
    const snapshot = normalizeWorldSnapshot({ nodes: [card], edges: [edge] });
    expect(snapshot.nodes[0].missing_plugin).toEqual(missing_plugin);
    expect(snapshot.edges[0].missing_plugin).toEqual(missing_plugin);
  });
  it("preserves field validation messages inside the correlated error envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "invalid_request", message: "Request validation failed.", request_id: "validation-id", retryable: false },
      detail: [{ type: "missing", loc: ["body", "title"], msg: "Field required" }],
    }), { status: 422, headers: { "content-type": "application/json" } })));
    await expect(worldApi.createConversationSession("chat", { title: "", participant_ids: [] }))
      .rejects.toMatchObject({ message: "Field required", requestId: "validation-id", detail: [
        { type: "missing", loc: ["body", "title"], msg: "Field required" },
      ] });
  });
  it("keeps server diagnostics and does not automatically retry a failed mutation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: {
      message: "Please retry later", code: "unavailable", retryable: true, request_id: "body-id",
    } }), { status: 503, headers: { "content-type": "application/json", "X-Request-ID": "server-id" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(worldApi.createConversationSession("chat", { title: "Review", participant_ids: [] }, "logical-operation-1"))
      .rejects.toMatchObject({ status: 503, requestId: "server-id", retryable: true, code: "unavailable" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("logical-operation-1");
    expect(headers.get("X-Request-ID")).toMatch(/^[a-f0-9]{32}$/);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: "Review", participant_ids: [] });
  });
  it("retains the sent request ID without retrying when a mutation response is lost", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("connection lost"));
    vi.stubGlobal("fetch", fetchMock);
    const error = await worldApi.createConversationSession("chat", { title: "Chat", participant_ids: [] }, "create-once").catch(error => error);
    expect(error).toMatchObject({ status: 0, retryable: false });
    expect(error.requestId).toBe(fetchMock.mock.calls[0][1].headers.get("X-Request-ID"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("queues published hosted Runs with a new key per intentional submission", async () => {
    vi.spyOn(hostedAuth, "isHosted").mockReturnValue(true);
    configureWorkspaceApi(true);
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => new Response(
      JSON.stringify({ accepted: true, run_id: "run-id" }),
      { status: 202, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    expect(await worldApi.runAgent("agent one", "Run the analysis", true)).toMatchObject({ run_id: "run-id" });
    await worldApi.runAgent("agent one", "Run the analysis", true);

    const [[url, first], [, second]] = fetchMock.mock.calls;
    expect(url).toBe("/api/runtime-app/workspace/agents/agent%20one/run");
    expect(first.method).toBe("POST");
    expect(JSON.parse(String(first.body))).toEqual({ prompt: "Run the analysis", fresh_context: true, enqueue: true });
    expect(second.body).toBe(first.body);
    const keys = [first, second].map(init => new Headers(init.headers).get("Idempotency-Key"));
    expect(keys[0]).toMatch(/^[\da-f-]{36}$/);
    expect(keys[1]).toMatch(/^[\da-f-]{36}$/);
    expect(keys[1]).not.toBe(keys[0]);
  });
  it("preserves immediate Run submission outside hosted mode", async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => new Response("{}", {
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await worldApi.runAgent("agent", "Run locally");
    configureWorkspaceApi(true);
    await worldApi.runAgent("agent", "Run in a standalone published app");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/agents/agent/run", "/api/runtime-app/workspace/agents/agent/run",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(JSON.parse(String(init.body))).not.toHaveProperty("enqueue");
      expect(new Headers(init.headers).has("Idempotency-Key")).toBe(false);
    }
  });
  it("reports a lost or rejected hosted Run response without issuing another mutation", async () => {
    vi.spyOn(hostedAuth, "isHosted").mockReturnValue(true);
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(new Response("{}", { status: 422, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(worldApi.runAgent("agent", "Run once")).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await expect(worldApi.runAgent("agent", "Run once")).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('loads and restores the same finish, defaulting old cards to normal', async () => {
    const card = normalizeCard({ id: 'printed', type: 'text', finish: 'starlight' });
    expect(card.finish).toBe('starlight');
    expect(normalizeCard({ id: 'old', type: 'text' }).finish).toBe('normal');
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(card), {
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    expect((await worldApi.restoreNode(card)).finish).toBe('starlight');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).finish).toBe('starlight');
    await worldApi.createNode(card);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).finish).toBe('starlight');
  });
  it('preserves the persisted unsigned 32-bit terrain seed without accepting malformed values', () => {
    for (const seed of [0, 123, 456, 0xFFFFFFFF]) {
      expect(normalizeWorldSnapshot({ terrain_seed: seed }).terrain_seed).toBe(seed);
    }
    for (const seed of [undefined, null, '123', -1, 1.5, NaN, Infinity, 0x100000000]) {
      expect(normalizeWorldSnapshot({ terrain_seed: seed })).not.toHaveProperty('terrain_seed');
    }
  });
  it("saves cached glue candidates without gesture offsets", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 1, boxes: {}, bonds: [] })));
    vi.stubGlobal("fetch", fetchMock);
    const candidate = { a: "a", b: "b", side: "right" as const, dx: 10, dy: 0 };
    await worldApi.saveGlue({ revision: 0, bonds: [candidate] });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/canvas/glue");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      revision: 0, bonds: [{ a: "a", b: "b", side: "right" }],
    });
    expect(candidate.dx).toBe(10);
  });
  it("preserves card and edge revisions separately from resource revisions", () => {
    expect(normalizeCard({ id: "text", type: "text", revision: 8, resource: { revision: 3 } })).toMatchObject({ revision: 8, config: { revision: 3 } });
    expect(normalizeEdge({ id: "edge", source: "a", target: "b", relationship: "read", revision: 5 }).revision).toBe(5);
    expect(normalizeRuntimeEvent({ type: "card_updated", sequence: 42, stream_id: "stream" })).toMatchObject({ sequence: 42, stream_id: "stream" });
  });
  it("keeps display metadata out of restorable Legion settings", () => {
    const card = normalizeCard({ id: "team", type: "legion", config: { instruction: "Review", status: "available" }, resource: { status: "available" } });
    expect(card.config).toEqual({ instruction: "Review", status: "available" });
    expect(normalizeCard(card).config).toEqual(card.config);
  });
  it("discovers server runtimes and sends terminal text without choosing a frontend shell", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(
      url.endsWith("/sandbox/runtimes") ? { runtimes: [], default_runtime: null } : { stdout: "hello", exit_code: 0 },
    ), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await worldApi.getSandboxRuntimes();
    await worldApi.executeSandbox("folder/name", "printf 'hello'");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/sandbox/runtimes");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/sandboxes/folder%2Fname/execute");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ command: "printf 'hello'" });
    await worldApi.executeSandbox("folder/name", "python script.py", "managed");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ command: "python script.py", python_environment: "managed" });
  });

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

  it("uses the explicit restore route for history reconstruction", async () => {
    const restored = {
      id: "managed-team",
      type: "legion",
      name: "Managed team",
      position: { x: 10, y: 20 },
      size: { width: 1100, height: 700 },
      expanded: false,
      status: "available",
      config: {},
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(restored), {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.restoreNode(restored);

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/nodes/restore");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toMatchObject({ id: restored.id, type: "legion" });
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
