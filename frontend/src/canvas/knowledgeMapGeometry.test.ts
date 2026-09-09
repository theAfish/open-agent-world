import { describe, expect, it } from 'vitest';
import { mapCurve } from '../../../plugins/matcreator/frontend/mapGeometry';

describe('knowledge map circle geometry', () => {
  it.each([[300, 0], [0, 300], [-240, 180], [300, -200]])('attaches radially at %s,%s with center-facing tangents', (x, y) => {
    const source = { x: 15, y: 23, radius: 55 }, target = { x, y, radius: 40 };
    const curve = mapCurve(source, target)!;
    const dx = x - source.x, dy = y - source.y;
    expect(Math.hypot(curve.start.x - source.x, curve.start.y - source.y)).toBeCloseTo(55);
    expect(Math.hypot(curve.end.x - x, curve.end.y - y)).toBeCloseTo(40);
    expect((curve.c1.x - curve.start.x) * dy - (curve.c1.y - curve.start.y) * dx).toBeCloseTo(0);
    expect((curve.end.x - curve.c4.x) * dy - (curve.end.y - curve.c4.y) * dx).toBeCloseTo(0);
    expect(curve.path.match(/ C /g)).toHaveLength(2);
  });
  it('does not generate invalid geometry for coincident centers', () => {
    expect(mapCurve({x:0,y:0,radius:55}, {x:0,y:0,radius:55})).toBeNull();
  });
});
