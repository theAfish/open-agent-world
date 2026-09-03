import { describe, expect, it } from "vitest";
import { buildCardDraft } from "../state/helpers";
import { displacedPosition, displacedPositions, nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from "./nodeDisplacement";

function card(id: string, x: number, y: number) {
  return { id, ...buildCardDraft("agent", { x, y }) };
}

describe("local inspector displacement", () => {
  it("moves only nearby peers and never mutates persisted positions", () => {
    const inspector = card("inspector", 100, 100);
    const nearby = card("nearby", 250, 250);
    const far = card("far", 2_000, 2_000);
    const original = { ...nearby.position };

    const displaced = displacedPosition(nearby, inspector);
    expect(displaced.displaced).toBe(true);
    expect(displaced.position).not.toEqual(original);
    expect(nearby.position).toEqual(original);
    expect(displacedPosition(far, inspector)).toEqual({ position: far.position, displaced: false });
  });

  it("leaves the inspector anchored at its original graph position", () => {
    const inspector = card("inspector", 100, 100);
    expect(displacedPosition(inspector, inspector)).toEqual({
      position: inspector.position,
      displaced: false,
    });
  });

  it("combines local repulsion from multiple persistent inspectors", () => {
    const leftInspector = card("left", 100, 100);
    const rightInspector = card("right", 500, 100);
    const nearby = card("nearby", 350, 260);
    const displaced = displacedPosition(nearby, [leftInspector, rightInspector]);
    expect(displaced.displaced).toBe(true);
    expect(displaced.position).not.toEqual(nearby.position);
    expect(displacedPosition(leftInspector, [leftInspector, rightInspector]).displaced).toBe(false);
  });

  it("keeps every expanded surface centered on the compact node", () => {
    const compact = { x: 400, y: 300 };
    const preview = positionSurfaceAtNodeCenter(compact, "preview");
    const inspector = positionSurfaceAtNodeCenter(compact, "inspector");
    const workspace = positionSurfaceAtNodeCenter(compact, "workspace");
    expect(preview).toEqual({ x: 305, y: 270 });
    expect(inspector).toEqual({ x: 229, y: 63 });
    expect(workspace).toEqual({ x: -62, y: -2 });
    expect(nodePositionFromSurfacePosition(preview, "preview")).toEqual(compact);
    expect(nodePositionFromSurfacePosition(inspector, "inspector")).toEqual(compact);
    expect(nodePositionFromSurfacePosition(workspace, "workspace")).toEqual(compact);
  });

  it("clears a large workspace and separates peers that were pushed into one another", () => {
    const workspace = card("workspace", 500, 400);
    const left = card("left", 440, 420);
    const middle = card("middle", 500, 400);
    const right = card("right", 560, 420);
    const layout = displacedPositions([workspace, left, middle, right], [{ card: workspace, level: "workspace" }]);
    const workspaceTopLeft = positionSurfaceAtNodeCenter(workspace.position, "workspace");
    const workspaceRight = workspaceTopLeft.x + 1020;
    const workspaceBottom = workspaceTopLeft.y + 700;
    const peers = [left, middle, right].map((item) => layout.get(item.id)!);

    for (const peer of peers) {
      const right = peer.position.x + 96;
      const bottom = peer.position.y + 96;
      expect(right <= workspaceTopLeft.x || peer.position.x >= workspaceRight || bottom <= workspaceTopLeft.y || peer.position.y >= workspaceBottom).toBe(true);
    }
    for (let first = 0; first < peers.length; first += 1) {
      for (let second = first + 1; second < peers.length; second += 1) {
        const dx = Math.abs(peers[first].position.x - peers[second].position.x);
        const dy = Math.abs(peers[first].position.y - peers[second].position.y);
        expect(dx >= 124 || dy >= 124).toBe(true);
      }
    }
  });
});
