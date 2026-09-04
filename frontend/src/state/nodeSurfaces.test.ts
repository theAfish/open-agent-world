import { beforeEach, describe, expect, it } from "vitest";
import { NODE_SURFACE_SUPPORT, surfaceLevelForNode, useNodeSurfaceStore } from "./nodeSurfaces";

describe("node surface state", () => {
  beforeEach(() => {
    useNodeSurfaceStore.setState({
      surfaceLevels: {},
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
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-2": "inspector" });
    actions.closeInspector("agent-1");
    expect(useNodeSurfaceStore.getState().surfaceLevels).toEqual({ "agent-2": "inspector" });
  });

  it("keeps drafts outside transient inspector mounts and declares supported levels", () => {
    useNodeSurfaceStore.getState().setDraft("agent-1", "unfinished thought");
    expect(useNodeSurfaceStore.getState().drafts["agent-1"]).toBe("unfinished thought");
    expect(NODE_SURFACE_SUPPORT.agent.workspace).toBe(true);
    expect(NODE_SURFACE_SUPPORT.text.workspace).toBe(false);
    expect(surfaceLevelForNode("other", { "agent-1": "workspace" })).toBe("node");
  });

  it("locks a preview for the full connection gesture", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.showPreview("agent-1");
    actions.beginConnection("agent-1");
    actions.hidePreview("agent-1");
    actions.showPreview("agent-2");
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      surfaceLevels: { "agent-1": "preview" },
      connectingNodeId: "agent-1",
    });
    actions.endConnection();
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      surfaceLevels: {},
      connectingNodeId: undefined,
    });
  });
});
