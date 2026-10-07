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
    expect(geometry.markerPath).toContain(" C ");
    expect(geometry.source.normalX).toBeGreaterThan(0);
    expect(geometry.target.normalX).toBeLessThan(0);
    expect(Number.isFinite(geometry.labelX)).toBe(true);
    expect(Number.isFinite(geometry.labelY)).toBe(true);
  });

  it("uses a true circular boundary for compact square nodes", () => {
    const circle = { x: 100, y: 80, width: 96, height: 96 };
    const anchor = roundedRectAnchor(circle, { x: 400, y: 300 }, 48);
    expect(Math.hypot(anchor.x - 148, anchor.y - 128)).toBeCloseTo(48, 5);
    expect(Math.hypot(anchor.normalX, anchor.normalY)).toBeCloseTo(1, 6);
  });

  it("places both arrows equally clear of their endpoint dots", () => {
    const geometry = relationshipPath(rect, { x: 450, y: 100, width: 180, height: 120 });

    expect(geometry.markerSource.x).toBeCloseTo(312, 6);
    expect(geometry.markerSource.y).toBeCloseTo(160, 6);
    expect(geometry.source.x).toBeCloseTo(300, 6);
    expect(geometry.source.y).toBeCloseTo(160, 6);
    expect(geometry.markerTarget.x).toBeCloseTo(438, 6);
    expect(geometry.markerTarget.y).toBeCloseTo(160, 6);
    expect(geometry.target.x).toBeCloseTo(450, 6);
    expect(geometry.target.y).toBeCloseTo(160, 6);
    expect(geometry.target).toMatchObject({ normalX: -1, normalY: 0 });
    expect(geometry.bidirectionalMarkerPath).not.toBe(geometry.markerPath);
  });

  it('keeps a self-loop outside the circle with separate boundary endpoints', () => {
    const circle = {x: 100, y: 100, width: 104, height: 104};
    const loop = relationshipPath(circle, circle, 52, 52, {selfLoop: true, markerOffset: 7});
    expect(loop.source.x).toBeGreaterThan(152);
    expect(loop.target.x).toBeLessThan(152);
    for (const endpoint of [loop.source, loop.target]) {
      expect(Math.hypot(endpoint.x - 152, endpoint.y - 152)).toBeCloseTo(52);
    }
    expect(loop.labelY).toBeGreaterThan(204);
    // Sample the actual cubic, not only its bounds: no part may cut the state.
    const values = loop.path.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/g)!.map(Number);
    for (let step = 1; step < 20; step++) {
      const t = step / 20, u = 1 - t;
      const x = u ** 3 * values[0] + 3 * u ** 2 * t * values[2] + 3 * u * t ** 2 * values[4] + t ** 3 * values[6];
      const y = u ** 3 * values[1] + 3 * u ** 2 * t * values[3] + 3 * u * t ** 2 * values[5] + t ** 3 * values[7];
      expect(Math.hypot(x - 152, y - 152)).toBeGreaterThan(52);
    }
    expect(relationshipPath(circle, circle, 52, 52, {selfLoop: true, offset: 44}).labelY).toBeGreaterThan(loop.labelY);
  });

  it('separates reciprocal curves and follows circles after moving them vertically', () => {
    const a = {x: 100, y: 100, width: 104, height: 104}, b = {...a, x: 400};
    const forward = relationshipPath(a, b, 52, 52, {offset: 18});
    const back = relationshipPath(b, a, 52, 52, {offset: 18});
    expect(forward.labelY).toBeGreaterThan(152);
    expect(back.labelY).toBeLessThan(152);
    const vertical = relationshipPath(a, {...b, x: 100, y: 400}, 52);
    expect(vertical.source.x).toBeCloseTo(152);
    expect(vertical.source.y).toBeCloseTo(204);
    expect(vertical.target.x).toBeCloseTo(152);
    expect(vertical.target.y).toBeCloseTo(400);
  });
});
