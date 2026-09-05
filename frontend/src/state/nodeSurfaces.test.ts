import { beforeEach, describe, expect, it } from "vitest";
import { NODE_SURFACE_SUPPORT, surfaceLevelForNode, useNodeSurfaceStore } from "./nodeSurfaces";

describe("node surface state", () => {
  beforeEach(() => {
    useNodeSurfaceStore.setState({
      surfaceLevels: {},
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
});
