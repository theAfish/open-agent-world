import { beforeEach, describe, expect, it } from "vitest";
import { NODE_SURFACE_SUPPORT, surfaceLevelForNode, useNodeSurfaceStore } from "./nodeSurfaces";

describe("node surface state", () => {
  beforeEach(() => {
    useNodeSurfaceStore.setState({
      activeNodeId: undefined,
      level: "node",
      connectingNodeId: undefined,
      inspectorNodeIds: [],
      drafts: {},
      maximizedWorkspaces: {},
    });
  });

  it("keeps multiple inspectors open until each is explicitly closed", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.showPreview("agent-1");
    expect(useNodeSurfaceStore.getState()).toMatchObject({ activeNodeId: "agent-1", level: "preview" });

    actions.openInspector("agent-1");
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      activeNodeId: undefined,
      level: "node",
      inspectorNodeIds: ["agent-1"],
    });
    actions.showPreview("agent-2");
    actions.openInspector("agent-2");
    expect(useNodeSurfaceStore.getState().inspectorNodeIds).toEqual(["agent-1", "agent-2"]);
    expect(surfaceLevelForNode("agent-1", undefined, "node", ["agent-1", "agent-2"])).toBe("inspector");

    actions.openWorkspace("agent-1");
    expect(useNodeSurfaceStore.getState().level).toBe("workspace");
    actions.closeWorkspace();
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      activeNodeId: undefined,
      level: "node",
      inspectorNodeIds: ["agent-1", "agent-2"],
    });
    actions.closeInspector("agent-1");
    expect(useNodeSurfaceStore.getState().inspectorNodeIds).toEqual(["agent-2"]);
  });

  it("keeps drafts outside transient inspector mounts and declares supported levels", () => {
    useNodeSurfaceStore.getState().setDraft("agent-1", "unfinished thought");
    expect(useNodeSurfaceStore.getState().drafts["agent-1"]).toBe("unfinished thought");
    expect(NODE_SURFACE_SUPPORT.agent.workspace).toBe(true);
    expect(NODE_SURFACE_SUPPORT.text.workspace).toBe(false);
    expect(surfaceLevelForNode("other", "agent-1", "workspace")).toBe("node");
  });

  it("locks a preview for the full connection gesture", () => {
    const actions = useNodeSurfaceStore.getState();
    actions.showPreview("agent-1");
    actions.beginConnection("agent-1");
    actions.hidePreview("agent-1");
    actions.showPreview("agent-2");
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      activeNodeId: "agent-1",
      level: "preview",
      connectingNodeId: "agent-1",
    });
    actions.endConnection();
    expect(useNodeSurfaceStore.getState()).toMatchObject({
      activeNodeId: undefined,
      level: "node",
      connectingNodeId: undefined,
    });
  });
});
