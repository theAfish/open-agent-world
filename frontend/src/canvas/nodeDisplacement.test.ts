import { describe, expect, it } from "vitest";
import { buildCardDraft } from "../state/helpers";
import { displacedPosition, nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from "./nodeDisplacement";

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
    expect(preview).toEqual({ x: 305, y: 270 });
    expect(inspector).toEqual({ x: 229, y: 63 });
    expect(nodePositionFromSurfacePosition(preview, "preview")).toEqual(compact);
    expect(nodePositionFromSurfacePosition(inspector, "inspector")).toEqual(compact);
  });
});
