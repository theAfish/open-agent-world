import { describe, expect, it } from 'vitest';
import { graphLayout } from '../../../plugins/matcreator/frontend/graphLayout';

describe('knowledge topology layout', () => {
  it('keeps connected chains compact, separates components, and avoids node overlap', () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'isolated'];
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'd', target: 'e' }, { source: 'e', target: 'f' }];
    const positions = graphLayout(ids, edges);
    const distance = (a: string, b: string) => Math.hypot(positions.get(a)!.x - positions.get(b)!.x, positions.get(a)!.y - positions.get(b)!.y);
    for (const edge of edges) expect(distance(edge.source, edge.target)).toBeLessThan(260);
    for (const a of ['a', 'b', 'c']) for (const b of ['d', 'e', 'f']) expect(distance(a, b)).toBeGreaterThan(300);
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) expect(distance(ids[i], ids[j])).toBeGreaterThan(145);
    expect(graphLayout([...ids].reverse(), edges)).toEqual(positions);
  });

  it('keeps dragged nodes in place while adding their neighbors', () => {
    const saved = new Map([['a', { x: 700, y: -200 }], ['b', { x: 900, y: -200 }]]);
    const positions = graphLayout(['a', 'b', 'c'], [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }], saved);
    expect(positions.get('a')).toEqual(saved.get('a'));
    expect(positions.get('b')).toEqual(saved.get('b'));
    expect(Math.hypot(positions.get('c')!.x - 900, positions.get('c')!.y + 200)).toBeLessThan(270);
  });
});
