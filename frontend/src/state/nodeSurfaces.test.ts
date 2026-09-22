import { beforeEach, describe, expect, it } from "vitest";
import { collapsedSurface, nodePresentation, NODE_SURFACE_SUPPORT, surfaceLevelForNode, useNodeSurfaceStore } from "./nodeSurfaces";
import { TEST_CATALOG } from "./catalog.fixture";
import type { NodePresentation, NodeSurfaceLevel } from "../types/world";
import { buildCardDraft } from './helpers';

describe("node surface state", () => {
  it('starts new container members at node level and preserves explicit choices', () => {
    useNodeSurfaceStore.setState({ surfaceLevels: { saved: 'inspector' }, baseLevels: {} });
    useNodeSurfaceStore.getState().syncCards([
      { id: 'member', type: 'text', parent_id: 'room' },
      { id: 'saved', type: 'text', parent_id: 'room' },
      { id: 'outside', type: 'text' },
    ], TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels).toMatchObject({
      member: 'node', saved: 'inspector', outside: 'preview',
    });
  });
  it('migrates legacy workspace sizes and keeps resized details independent across collapse', async () => {
    const migrated = await useNodeSurfaceStore.persist.getOptions().migrate!({ workspaceSizes: { a: { width: 1234, height: 876 } }, surfaceLevels: { a: 'workspace' } }, 3);
    expect(migrated).toEqual({ surfaceSizes: { a: { workspace: { width: 1234, height: 876 } } }, surfaceLevels: { a: 'workspace' } });
    const actions = useNodeSurfaceStore.getState();
    actions.resizeSurface('a', 'inspector', { width: 530, height: 440 });
    actions.resizeSurface('a', 'workspace', { width: 1400, height: 950 });
    actions.openInspector('a');
    actions.closeInspector('a');
    actions.openInspector('a');
    expect(useNodeSurfaceStore.getState().surfaceSizes.a).toEqual({ inspector: { width: 530, height: 440 }, workspace: { width: 1400, height: 950 } });
  });
  it('restores template state to new IDs and preserves workspace size and compact return state', () => {
    const original = { id: 'source', ...buildCardDraft('agent', { x: 0, y: 0 }) };
    useNodeSurfaceStore.setState({ surfaceLevels: { source: 'workspace' }, baseLevels: { source: 'node' }, surfaceSizes: { source: { workspace: { width: 1250, height: 900 }, inspector: { width: 600, height: 500 } } } });
    const saved = useNodeSurfaceStore.getState().capturePresentation([original], TEST_CATALOG);
    const restored = { ...original, id: 'copy' };
    useNodeSurfaceStore.getState().restorePresentation([restored], TEST_CATALOG, { copy: saved.source });
    useNodeSurfaceStore.getState().syncCards([restored], TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.copy).toBe('workspace');
    expect(useNodeSurfaceStore.getState().surfaceSizes.copy.workspace).toEqual({ width: 1250, height: 900 });
    expect(useNodeSurfaceStore.getState().surfaceSizes.copy.inspector).toEqual({ width: 600, height: 500 });
    useNodeSurfaceStore.getState().closeWorkspace('copy');
    useNodeSurfaceStore.getState().closeInspector('copy');
    expect(useNodeSurfaceStore.getState().surfaceLevels.copy).toBe('node');
  });
  beforeEach(() => {
    useNodeSurfaceStore.setState({
      surfaceLevels: {},
      surfaceSizes: {},
      presentations: {},
      baseLevels: {},
      dragging: false,
      connectingNodeId: undefined,
      drafts: {},
      maximizedWorkspaces: {},
    });
  });

  it("keeps multiple inspectors open until each is explicitly closed", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.showPreview("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "preview" });

    actions.openInspector("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "inspector" });
    actions.showPreview("agent-2");
    actions.openInspector("agent-2");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "inspector", "agent-2": "inspector" });
    expect(surfaceLevelForNode("agent-1", { "agent-1": "inspector", "agent-2": "inspector" })).toBe("inspector");

    actions.openWorkspace("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "workspace", "agent-2": "inspector" });
    actions.closeWorkspace("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "inspector", "agent-2": "inspector" });
    actions.closeInspector("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-1": "preview", "agent-2": "inspector" });
  });

  it("keeps drafts outside transient inspector mounts and declares supported levels", () => {
    useNodeSurfaceStore.getState().setDraft("agent-1", "unfinished thought");
    expect(useNodeSurfaceStore.getState().drafts["agent-1"]).toBe("unfinished thought");
    expect(NODE_SURFACE_SUPPORT.agent.workspace).toBe(true);
    expect(NODE_SURFACE_SUPPORT.text.workspace).toBe(false);
    expect(surfaceLevelForNode("other", { "agent-1": "workspace" })).toBe("preview");
  });

  it.each(["node", "preview", "inspector", "workspace"] as const)("preserves %s throughout a connection", (level) => {
    useNodeSurfaceStore.setState({ surfaceLevels: { source: level, target: "node" } });
    const actions = useNodeSurfaceStore.getState();
    actions.beginConnection("source");
    actions.hidePreview("source");
    actions.showPreview("target");
    actions.openInspector("target");
    actions.closeInspector();
    actions.closeWorkspace();
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ source: level, target: "node" });
    actions.endConnection();
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ source: level, target: "node" });
    expect(useNodeSurfaceStore.getState().connectingNodeId).toBeUndefined();
  });

  it("restores each node's chosen base level through details and workspace", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.hidePreview("compact");
    actions.openInspector("compact");
    actions.openInspector("default");
    actions.openWorkspace("compact");
    actions.closeWorkspace("compact");
    expect(useNodeSurfaceStore.getState().surfaceLevels.compact).toBe("inspector");
    actions.closeInspector();
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ compact: "node", default: "preview" });
    actions.showPreview("compact");
    actions.showPreview("another");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ compact: "preview", default: "preview", another: "preview" });
  });

  it("does not change surfaces during a node drag", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.setDragging(true);
    actions.hidePreview("first");
    actions.openInspector("second");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({});
    actions.setDragging(false);
    actions.openInspector("second");
    expect(useNodeSurfaceStore.getState().surfaceLevels.second).toBe("inspector");
  });

  it.each(["conversation", "sandbox"])("initializes %s as a window and skips the inspector in both directions", type => {
    const actions = useNodeSurfaceStore.getState();
    actions.syncCards([{ id: "window", type }], TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.window).toBe("workspace");
    actions.closeWorkspace("window");
    expect(useNodeSurfaceStore.getState().surfaceLevels.window).toBe("preview");
    actions.hidePreview("window");
    actions.openPrimary("window");
    expect(useNodeSurfaceStore.getState().surfaceLevels.window).toBe("workspace");
    actions.closeWorkspace("window");
    expect(useNodeSurfaceStore.getState().surfaceLevels.window).toBe("node");
    actions.openInspector("window"); // Existing settings/deep-link callers also obey the policy.
    expect(useNodeSurfaceStore.getState().surfaceLevels.window).toBe("workspace");
  });

  it("initializes once, preserves restored compact choices, and repairs removed surfaces", () => {
    const actions = useNodeSurfaceStore.getState();
    const cards = [{ id: "room", type: "conversation" }];
    useNodeSurfaceStore.setState({ surfaceLevels: { room: "node" } });
    actions.syncCards(cards, TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.room).toBe("node");
    actions.showPreview("room");
    actions.syncCards([], TEST_CATALOG);
    actions.syncCards(cards, TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.room).toBe("preview");
    useNodeSurfaceStore.setState({ surfaceLevels: { room: "inspector" } });
    actions.syncCards(cards, TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.room).toBe("workspace");
    actions.dismiss("room");
    actions.syncCards(cards, TEST_CATALOG);
    expect(useNodeSurfaceStore.getState().surfaceLevels.room).toBe("preview");
  });

  it("waits for the catalog before initializing and persists only instance choices", () => {
    const actions = useNodeSurfaceStore.getState();
    const cards = [{ id: "room", type: "conversation" }];
    actions.syncCards(cards, { ...TEST_CATALOG, node_types: [] });
    expect(useNodeSurfaceStore.getState().surfaceLevels.room).toBeUndefined();
    actions.syncCards(cards, TEST_CATALOG);
    const saved = useNodeSurfaceStore.persist.getOptions().partialize!(useNodeSurfaceStore.getState());
    expect(saved).toMatchObject({ surfaceLevels: { room: "workspace" } });
    expect(saved).not.toHaveProperty("presentations");
  });

  it("adapts legacy plugins that skip preview or inspector", () => {
    const catalog = { ...TEST_CATALOG, node_types: [{ ...TEST_CATALOG.node_types[0], id: "legacy",
      surfaces: { preview: false, inspector: false, workspace: true } }] };
    expect(nodePresentation("legacy", catalog)).toEqual({ states: ["node", "workspace"], initial: "node", open: "workspace" });
  });

  it("Escape closes collapsible details even beside a workspace-only card", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.syncCards([{ id: "text", type: "text" }, { id: "fixed", type: "fixed" }], {
      ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types, { ...TEST_CATALOG.node_types[0], id: "fixed",
        presentation: { states: ["workspace"], initial: "workspace", open: "workspace" } }],
    });
    actions.openPrimary("text");
    actions.closeExpanded();
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ text: "preview", fixed: "workspace" });
  });

  it("Escape ignores deleted and unloaded windows without erasing their saved choices", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.syncCards([{ id: "text", type: "text" }, { id: "room", type: "conversation" }], TEST_CATALOG);
    actions.syncCards([{ id: "text", type: "text" }], TEST_CATALOG);
    actions.openPrimary("text");
    actions.closeExpanded();
    actions.dismiss();
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ text: "preview", room: "workspace" });
  });

  it("supports every nonempty subset without entering an unavailable state", () => {
    const all: NodeSurfaceLevel[] = ["node", "preview", "inspector", "workspace"];
    for (let mask = 1; mask < 16; mask++) {
      const states = all.filter((_, i) => mask & (1 << i));
      for (const initial of states) {
        const presentation: NodePresentation = { states, initial, open: states[states.length - 1] };
        const catalog = { ...TEST_CATALOG, node_types: [{ ...TEST_CATALOG.node_types[0], id: "custom", presentation }] };
        useNodeSurfaceStore.setState({ surfaceLevels: {}, baseLevels: {}, presentations: {} });
        const actions = useNodeSurfaceStore.getState();
        actions.syncCards([{ id: "custom", type: "custom" }], catalog);
        expect(useNodeSurfaceStore.getState().surfaceLevels.custom).toBe(initial);
        for (const action of [actions.openPrimary, actions.openInspector, actions.openWorkspace,
          actions.closeWorkspace, actions.closeInspector, actions.showPreview, actions.hidePreview, actions.dismiss]) {
          action("custom");
          expect(states).toContain(useNodeSurfaceStore.getState().surfaceLevels.custom);
        }
        for (const level of states) expect(states).toContain(collapsedSurface(presentation, level));
      }
    }
  });
});
