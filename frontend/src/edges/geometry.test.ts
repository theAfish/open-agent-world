import { describe, expect, it } from "vitest";
import { relationshipPath, roundedRectAnchor } from "./geometry";

const rect = { x: 100, y: 100, width: 200, height: 120 };

describe("relationship edge geometry", () => {
  it("moves anchors to the side facing the related node", () => {
    const right = roundedRectAnchor(rect, { x: 500, y: 160 });
    expect(right.x).toBeCloseTo(300, 6);
    expect(right.y).toBeCloseTo(160, 6);
    expect(right).toMatchObject({ normalX: 1, normalY: 0 });
    const top = roundedRectAnchor(rect, { x: 200, y: -100 });
    expect(top.x).toBeCloseTo(200, 6);
    expect(top.y).toBeCloseTo(100, 6);
    expect(top).toMatchObject({ normalX: 0, normalY: -1 });
  });

  it("uses the rounded corner and its radial normal for diagonal relations", () => {
    const anchor = roundedRectAnchor(rect, { x: 500, y: 300 });
    const cornerCenter = { x: 278, y: 198 };
    expect(Math.hypot(anchor.x - cornerCenter.x, anchor.y - cornerCenter.y)).toBeCloseTo(22, 5);
    expect(anchor.normalX).toBeGreaterThan(0);
    expect(anchor.normalY).toBeGreaterThan(0);
    expect(Math.hypot(anchor.normalX, anchor.normalY)).toBeCloseTo(1, 6);
  });

  it("builds a curve whose endpoint tangents follow the boundary normals", () => {
    const geometry = relationshipPath(rect, { x: 450, y: 80, width: 180, height: 150 });
    expect(geometry.path).toContain(" C ");
    expect(geometry.source.normalX).toBeGreaterThan(0);
    expect(geometry.target.normalX).toBeLessThan(0);
    expect(Number.isFinite(geometry.labelX)).toBe(true);
    expect(Number.isFinite(geometry.labelY)).toBe(true);
  });
});
